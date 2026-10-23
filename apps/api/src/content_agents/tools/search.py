"""Web search providers: Serper (Google, needs a key) or DuckDuckGo (free), with caching."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from pydantic import BaseModel, Field

from content_agents.cache import SqliteCache
from content_agents.config import ResearchSettings

SERPER_URL = "https://google.serper.dev/search"
SEARCH_TIMEOUT_S = 20.0
SEARCH_TTL_S = 24 * 3600

TextSearch = Callable[[str, int], list[dict[str, Any]]]


class SearchError(Exception):
    pass


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    rank: int


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)
    people_also_ask: list[str] = Field(default_factory=list)


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, num: int) -> SearchResponse: ...


class SerperSearch:
    name = "serper"

    def __init__(self, api_key: str, http: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http

    async def search(self, query: str, *, num: int) -> SearchResponse:
        try:
            response = await self._http.post(
                SERPER_URL,
                headers={"X-API-KEY": self._api_key},
                json={"q": query, "num": num},
                timeout=SEARCH_TIMEOUT_S,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise SearchError(f"Serper returned HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise SearchError(f"Serper request failed: {e}") from e

        data = response.json()
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item["link"],
                snippet=item.get("snippet", ""),
                rank=item.get("position", position),
            )
            for position, item in enumerate(data.get("organic", []), start=1)
            if item.get("link")
        ]
        questions = [q["question"] for q in data.get("peopleAlsoAsk", []) if q.get("question")]
        return SearchResponse(results=results, people_also_ask=questions)


def _ddgs_text(query: str, num: int) -> list[dict[str, Any]]:
    return DDGS().text(query, max_results=num)


class DuckDuckGoSearch:
    name = "duckduckgo"

    def __init__(
        self,
        text_search: TextSearch = _ddgs_text,
        *,
        min_interval_s: float = 1.0,
        retry_delay_s: float = 2.0,
        call_timeout_s: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._text_search = text_search
        self._min_interval_s = min_interval_s
        self._retry_delay_s = retry_delay_s
        self._call_timeout_s = call_timeout_s
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_request: float | None = None

    async def search(self, query: str, *, num: int) -> SearchResponse:
        # DuckDuckGo throttles bursts: queries go one at a time, spaced out, with one retry.
        async with self._lock:
            try:
                items = await self._attempt(query, num)
            except (DDGSException, TimeoutError):
                await self._sleep(self._retry_delay_s)
                try:
                    items = await self._attempt(query, num)
                except (DDGSException, TimeoutError) as e:
                    detail = str(e) or f"timed out after {self._call_timeout_s:g}s"
                    raise SearchError(f"DuckDuckGo search failed: {detail}") from e
        return SearchResponse(
            results=[
                SearchResult(
                    title=item.get("title", ""),
                    url=item["href"],
                    snippet=item.get("body", ""),
                    rank=rank,
                )
                for rank, item in enumerate(items, start=1)
                if item.get("href")
            ]
        )

    async def _attempt(self, query: str, num: int) -> list[dict[str, Any]]:
        if self._last_request is not None:
            wait = self._last_request + self._min_interval_s - self._clock()
            if wait > 0:
                await self._sleep(wait)
        try:
            # ddgs can block past its own timeout; the thread is abandoned, not the queue.
            return await asyncio.wait_for(
                asyncio.to_thread(self._text_search, query, num), self._call_timeout_s
            )
        finally:
            self._last_request = self._clock()


class CachedSearch:
    def __init__(
        self, provider: SearchProvider, cache: SqliteCache, *, ttl_s: float = SEARCH_TTL_S
    ) -> None:
        self.name = provider.name
        self._provider = provider
        self._cache = cache
        self._ttl_s = ttl_s

    async def search(self, query: str, *, num: int) -> SearchResponse:
        key = f"{self.name}:{num}:{' '.join(query.lower().split())}"
        if cached := await self._cache.get("search", key):
            return SearchResponse.model_validate_json(cached)
        response = await self._provider.search(query, num=num)
        await self._cache.set("search", key, response.model_dump_json(), ttl_s=self._ttl_s)
        return response


def make_search_provider(
    settings: ResearchSettings, http: httpx.AsyncClient, cache: SqliteCache
) -> SearchProvider:
    provider: SearchProvider
    if settings.serper_api_key:
        provider = SerperSearch(settings.serper_api_key, http)
    else:
        provider = DuckDuckGoSearch()
    return CachedSearch(provider, cache)
