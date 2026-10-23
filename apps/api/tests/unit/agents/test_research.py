from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest
from fakes import FakeLLM

from content_agents.agents.research import QueryPlan, ResearchAgent
from content_agents.cache import SqliteCache
from content_agents.graph.state import ContentBrief, PipelineState
from content_agents.tools.fetch import FetchedPage, FetchError
from content_agents.tools.search import SearchError, SearchResponse, SearchResult

TODAY = date(2026, 10, 22)


def body(seed: str, words: int = 600) -> str:
    return " ".join(f"{seed}{i}" for i in range(words))


def fetched(
    url: str, *, words: int = 600, seed: str | None = None, author: str | None = None
) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        title=f"Title of {url}",
        author=author,
        published=date(2026, 6, 1),
        markdown=body(seed or url, words),
        word_count=words,
    )


def results(*urls: str) -> SearchResponse:
    return SearchResponse(
        results=[SearchResult(title=u, url=u, rank=i) for i, u in enumerate(urls, start=1)]
    )


class FakeSearch:
    name = "fake"

    def __init__(self, responses: dict[str, SearchResponse | SearchError]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, num: int) -> SearchResponse:
        self.calls.append((query, num))
        response = self.responses.get(query, SearchResponse(results=[]))
        if isinstance(response, SearchError):
            raise response
        return response


class FakeFetcher:
    def __init__(self, pages: Mapping[str, FetchedPage | FetchError]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.fetched.append(url)
        page = self.pages.get(url, FetchError("http_status", "404"))
        if isinstance(page, FetchError):
            raise page
        return page


def make_brief() -> ContentBrief:
    return ContentBrief(
        topic="pgvector",
        refined_prompt="Guide to pgvector indexes",
        search_intent="informational",
        audience="backend engineers",
        tone="conversational",
        target_word_count=1500,
        primary_keyword="pgvector hnsw",
        secondary_keywords=["ivfflat"],
        questions_to_answer=["When to use HNSW?"],
        format="guide",
    )


def make_state(**overrides: object) -> PipelineState:
    fields: dict[str, object] = {"job_id": "j", "raw_prompt": "p", "brief": make_brief()}
    fields.update(overrides)
    return PipelineState.model_validate(fields)


def make_agent(
    search: FakeSearch,
    fetcher: FakeFetcher,
    *,
    queries: tuple[str, ...] = ("pgvector benchmarks",),
    max_sources: int = 15,
    cache: SqliteCache | None = None,
) -> tuple[ResearchAgent, FakeLLM]:
    llm = FakeLLM(parse_replies=[QueryPlan(queries=list(queries))])
    agent = ResearchAgent(
        llm,
        search,
        fetcher,
        today=TODAY,
        max_sources=max_sources,
        min_words=300,
        cache=cache,
    )
    return agent, llm


async def test_query_plan_is_reused_for_the_same_brief(tmp_path: Path) -> None:
    search = FakeSearch({})
    agent, llm = make_agent(search, FakeFetcher({}), cache=SqliteCache(tmp_path / "c.db"))

    await agent.run(make_state())
    await agent.run(make_state())

    assert len(llm.calls) == 1
    assert search.calls.count(("pgvector benchmarks", 5)) == 2


async def test_brief_is_required() -> None:
    agent, _ = make_agent(FakeSearch({}), FakeFetcher({}))

    with pytest.raises(ValueError, match="brief"):
        await agent.run(make_state(brief=None))


async def test_planner_gets_the_brief_and_queries_are_searched() -> None:
    search = FakeSearch({})
    agent, llm = make_agent(search, FakeFetcher({}), queries=("q1", "q2"))

    await agent.run(make_state())

    assert llm.calls[0]["agent"] == "research_planner"
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "pgvector hnsw" in prompt
    assert "When to use HNSW?" in prompt
    assert sorted(search.calls) == [("pgvector hnsw", 10), ("q1", 5), ("q2", 5)]


async def test_top_results_for_primary_keyword_are_competitors() -> None:
    search = FakeSearch(
        {
            "pgvector hnsw": results("https://c1.example/a", "https://c2.example/b"),
            "pgvector benchmarks": results("https://r1.example/x"),
        }
    )
    urls = ("https://c1.example/a", "https://c2.example/b", "https://r1.example/x")
    agent, _ = make_agent(search, FakeFetcher({u: fetched(u) for u in urls}))

    update = await agent.run(make_state())

    by_url = {str(s.url): s for s in update["sources"]}
    assert by_url["https://c1.example/a"].is_competitor
    assert by_url["https://c1.example/a"].serp_rank == 1
    assert by_url["https://c2.example/b"].serp_rank == 2
    assert not by_url["https://r1.example/x"].is_competitor
    assert by_url["https://r1.example/x"].serp_rank is None


async def test_seed_urls_from_intake_are_fetched() -> None:
    seed = "https://github.com/pgvector/pgvector"
    agent, _ = make_agent(FakeSearch({}), FakeFetcher({seed: fetched(seed)}))

    update = await agent.run(make_state(seed_urls=[seed]))

    assert [str(s.url) for s in update["sources"]] == [seed]


async def test_same_page_from_several_queries_is_fetched_once() -> None:
    search = FakeSearch(
        {
            "pgvector hnsw": results("https://a.example/post"),
            "pgvector benchmarks": results("https://www.a.example/post/?utm_source=x"),
        }
    )
    fetcher = FakeFetcher({"https://a.example/post": fetched("https://a.example/post")})
    agent, _ = make_agent(search, fetcher)

    update = await agent.run(make_state())

    assert fetcher.fetched == ["https://a.example/post"]
    assert len(update["sources"]) == 1


async def test_failures_are_reported_and_research_continues() -> None:
    search = FakeSearch(
        {
            "pgvector hnsw": results("https://ok.example/", "https://blocked.example/"),
            "pgvector benchmarks": SearchError("rate limited"),
        }
    )
    fetcher = FakeFetcher(
        {
            "https://ok.example/": fetched("https://ok.example/"),
            "https://blocked.example/": FetchError("robots", "disallowed"),
        }
    )
    agent, _ = make_agent(search, fetcher)

    update = await agent.run(make_state())

    assert [str(s.url) for s in update["sources"]] == ["https://ok.example/"]
    notes = " ".join(update["reports"]["research"].notes)
    assert "rate limited" in notes
    assert "robots" in notes


async def test_thin_and_near_duplicate_pages_are_dropped() -> None:
    search = FakeSearch(
        {
            "pgvector hnsw": results(
                "https://orig.example/", "https://copy.example/", "https://thin.example/"
            )
        }
    )
    fetcher = FakeFetcher(
        {
            "https://orig.example/": fetched("https://orig.example/", seed="same"),
            "https://copy.example/": fetched("https://copy.example/", seed="same"),
            "https://thin.example/": fetched("https://thin.example/", words=120),
        }
    )
    agent, _ = make_agent(search, fetcher)

    update = await agent.run(make_state())

    assert [str(s.url) for s in update["sources"]] == ["https://orig.example/"]
    notes = " ".join(update["reports"]["research"].notes)
    assert "near-duplicate" in notes
    assert "too short" in notes


async def test_cap_keeps_competitors_then_most_credible_sources() -> None:
    competitors = [f"https://c{i}.example/" for i in range(3)]
    others = ["https://blog.example/a", "https://docs.example.gov/b", "https://blog.example/c"]
    search = FakeSearch(
        {"pgvector hnsw": results(*competitors), "pgvector benchmarks": results(*others)}
    )
    pages = {u: fetched(u) for u in competitors + others}
    pages["https://docs.example.gov/b"] = fetched("https://docs.example.gov/b", author="Ada")
    agent, _ = make_agent(search, FakeFetcher(pages), max_sources=4)

    update = await agent.run(make_state())

    urls = [str(s.url) for s in update["sources"]]
    assert urls == [*competitors, "https://docs.example.gov/b"]


async def test_people_also_ask_questions_are_collected() -> None:
    serp = SearchResponse(results=[], people_also_ask=["Is pgvector fast?", "HNSW vs IVF?"])
    agent, _ = make_agent(FakeSearch({"pgvector hnsw": serp}), FakeFetcher({}))

    update = await agent.run(make_state())

    assert update["people_also_ask"] == ["Is pgvector fast?", "HNSW vs IVF?"]
