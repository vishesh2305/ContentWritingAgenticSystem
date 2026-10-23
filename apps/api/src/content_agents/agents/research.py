"""Agent 3: find, fetch and vet web sources for the brief (Plan.md §7)."""

import asyncio
import hashlib
from collections.abc import Sequence
from datetime import date
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from content_agents.agents.base import ChatLLM, StateUpdate
from content_agents.cache import SqliteCache
from content_agents.graph.state import AgentReport, ContentBrief, PipelineState, Source
from content_agents.tools.fetch import FetchedPage, FetchError
from content_agents.tools.search import SearchError, SearchProvider, SearchResponse
from content_agents.tools.sources import canonical_url, credibility, find_near_duplicates

PLANNER_MAX_TOKENS = 512
MAX_PEOPLE_ALSO_ASK = 10
QUERY_PLAN_TTL_S = 24 * 3600

PLANNER_SYSTEM = """You plan web research for an article. Write search queries that find \
evidence a writer can cite: statistics and studies, official documentation, practitioner \
write-ups and recent developments. Each query is 3 to 8 words, specific, and different \
from the others. Do not repeat the primary keyword on its own; it is searched separately."""


class QueryPlan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


class ResearchAgent:
    name = "research"

    def __init__(
        self,
        llm: ChatLLM,
        search: SearchProvider,
        fetcher: Fetcher,
        *,
        today: date,
        max_sources: int = 15,
        competitor_count: int = 10,
        results_per_query: int = 5,
        min_words: int = 300,
        concurrency: int = 6,
        cache: SqliteCache | None = None,
    ) -> None:
        self._cache = cache
        self._llm = llm
        self._search = search
        self._fetcher = fetcher
        self._today = today
        self._max_sources = max_sources
        self._competitor_count = competitor_count
        self._results_per_query = results_per_query
        self._min_words = min_words
        self._concurrency = asyncio.Semaphore(concurrency)

    async def run(self, state: PipelineState) -> StateUpdate:
        brief = state.brief
        if brief is None:
            raise ValueError("research needs a brief; run the brief agent first")
        notes: list[str] = []

        queries = await self._plan_queries(brief)
        competitor_serp, *other_serps = await asyncio.gather(
            self._search_or_note(brief.primary_keyword, self._competitor_count, notes),
            *(self._search_or_note(q, self._results_per_query, notes) for q in queries),
        )

        # Priority order: competitors (by rank), the user's own links, then research results.
        # Keyed by canonical URL so one page found via several links is fetched once.
        candidates: dict[str, tuple[str, int | None]] = {}
        for result in competitor_serp.results:
            candidates.setdefault(canonical_url(result.url), (result.url, result.rank))
        for url in state.seed_urls:
            candidates.setdefault(canonical_url(url), (url, None))
        for serp in other_serps:
            for result in serp.results:
                candidates.setdefault(canonical_url(result.url), (result.url, None))

        pages = await asyncio.gather(
            *(self._fetch_or_note(url, notes) for url, _ in candidates.values())
        )
        kept: list[tuple[FetchedPage, int | None]] = []
        for (url, rank), page in zip(candidates.values(), pages, strict=True):
            if page is None:
                continue
            if page.word_count < self._min_words:
                notes.append(f"too short ({page.word_count} words): {url}")
                continue
            kept.append((page, rank))

        duplicates = find_near_duplicates([page.markdown for page, _ in kept])
        for index in sorted(duplicates):
            notes.append(f"near-duplicate of an earlier source: {kept[index][0].final_url}")
        sources = [
            self._to_source(page, rank)
            for index, (page, rank) in enumerate(kept)
            if index not in duplicates
        ]

        return {
            "sources": self._select(sources),
            "people_also_ask": _people_also_ask([competitor_serp, *other_serps]),
            "reports": {**state.reports, self.name: AgentReport(agent=self.name, notes=notes)},
        }

    async def _plan_queries(self, brief: ContentBrief) -> list[str]:
        # The planner samples, so the same brief can yield new queries each run; caching the
        # plan keeps a repeat run on the same searches (and so on cached pages).
        key = hashlib.sha256(brief.model_dump_json().encode()).hexdigest()
        cached = await self._cache.get("query_plan", key) if self._cache else None
        if cached:
            plan = QueryPlan.model_validate_json(cached)
        else:
            result = await self._llm.parse(
                "research_planner",
                system=PLANNER_SYSTEM,
                messages=[{"role": "user", "content": _planner_message(brief)}],
                output_format=QueryPlan,
                max_tokens=PLANNER_MAX_TOKENS,
            )
            plan = result.output
            if self._cache:
                await self._cache.set(
                    "query_plan", key, plan.model_dump_json(), ttl_s=QUERY_PLAN_TTL_S
                )
        primary = brief.primary_keyword.strip().lower()
        unique = dict.fromkeys(q.strip() for q in plan.queries)
        return [q for q in unique if q and q.lower() != primary]

    async def _search_or_note(self, query: str, num: int, notes: list[str]) -> SearchResponse:
        try:
            return await self._search.search(query, num=num)
        except SearchError as e:
            notes.append(f"search failed for {query!r}: {e}")
            return SearchResponse()

    async def _fetch_or_note(self, url: str, notes: list[str]) -> FetchedPage | None:
        async with self._concurrency:
            try:
                return await self._fetcher.fetch(url)
            except FetchError as e:
                notes.append(f"skipped {url}: {e}")
                return None

    def _to_source(self, page: FetchedPage, serp_rank: int | None) -> Source:
        return Source.model_validate(
            {
                "url": page.final_url,
                "title": page.title or page.final_url,
                "published_at": page.published,
                "domain": (urlsplit(page.final_url).hostname or "").removeprefix("www."),
                "credibility": credibility(
                    page.final_url,
                    author=page.author,
                    published=page.published,
                    word_count=page.word_count,
                    today=self._today,
                ),
                "is_competitor": serp_rank is not None,
                "serp_rank": serp_rank,
                "word_count": page.word_count,
                "markdown": page.markdown,
            }
        )

    def _select(self, sources: Sequence[Source]) -> list[Source]:
        """Every competitor (Step 5 compares against them), then the most credible rest."""
        competitors = sorted(
            (s for s in sources if s.is_competitor), key=lambda s: s.serp_rank or 0
        )
        others = sorted(
            (s for s in sources if not s.is_competitor), key=lambda s: s.credibility, reverse=True
        )
        room = max(0, self._max_sources - len(competitors))
        return [*competitors, *others[:room]]


def _planner_message(brief: ContentBrief) -> str:
    questions = "\n".join(f"- {q}" for q in brief.questions_to_answer) or "- (none given)"
    return (
        f"Topic: {brief.topic}\n"
        f"Article: {brief.refined_prompt}\n"
        f"Audience: {brief.audience}\n"
        f"Primary keyword: {brief.primary_keyword}\n"
        f"Secondary keywords: {', '.join(brief.secondary_keywords) or '(none)'}\n"
        f"Questions the article must answer:\n{questions}"
    )


def _people_also_ask(responses: Sequence[SearchResponse]) -> list[str]:
    unique = dict.fromkeys(q for response in responses for q in response.people_also_ask)
    return list(unique)[:MAX_PEOPLE_ALSO_ASK]
