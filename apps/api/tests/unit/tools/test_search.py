import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from ddgs.exceptions import DDGSException

from content_agents.cache import SqliteCache
from content_agents.config import ResearchSettings
from content_agents.tools.search import (
    CachedSearch,
    DuckDuckGoSearch,
    SearchError,
    SearchResponse,
    SearchResult,
    SerperSearch,
    make_search_provider,
)

SERPER_RESPONSE = {
    "organic": [
        {
            "title": "pgvector docs",
            "link": "https://github.com/pgvector/pgvector",
            "snippet": "Open-source vector similarity search",
            "position": 1,
        },
        {"title": "HNSW explained", "link": "https://example.com/hnsw", "position": 2},
    ],
    "peopleAlsoAsk": [
        {"question": "Is pgvector production ready?", "snippet": "..."},
        {"question": "HNSW or IVFFlat?", "snippet": "..."},
    ],
}


def serper_client(requests: list[httpx.Request], response: httpx.Response) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_serper_maps_organic_results_and_people_also_ask() -> None:
    requests: list[httpx.Request] = []
    http = serper_client(requests, httpx.Response(200, json=SERPER_RESPONSE))

    response = await SerperSearch("secret-key", http).search("pgvector", num=10)

    assert response.results == [
        SearchResult(
            title="pgvector docs",
            url="https://github.com/pgvector/pgvector",
            snippet="Open-source vector similarity search",
            rank=1,
        ),
        SearchResult(title="HNSW explained", url="https://example.com/hnsw", rank=2),
    ]
    assert response.people_also_ask == ["Is pgvector production ready?", "HNSW or IVFFlat?"]
    request = requests[0]
    assert request.url == "https://google.serper.dev/search"
    assert request.headers["X-API-KEY"] == "secret-key"
    assert json.loads(request.content) == {"q": "pgvector", "num": 10}


async def test_serper_http_error_raises_search_error() -> None:
    http = serper_client([], httpx.Response(403, json={"message": "bad key"}))

    with pytest.raises(SearchError, match="403"):
        await SerperSearch("bad", http).search("pgvector", num=10)


async def test_duckduckgo_maps_results_with_ranks() -> None:
    def text_search(query: str, num: int) -> list[dict[str, Any]]:
        assert (query, num) == ("pgvector", 5)
        return [
            {"title": "A", "href": "https://a.example/", "body": "first"},
            {"title": "B", "href": "https://b.example/", "body": "second"},
        ]

    response = await DuckDuckGoSearch(text_search).search("pgvector", num=5)

    assert [(r.title, r.url, r.snippet, r.rank) for r in response.results] == [
        ("A", "https://a.example/", "first", 1),
        ("B", "https://b.example/", "second", 2),
    ]
    assert response.people_also_ask == []


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def ddg(text_search: Any, sleep: FakeSleep) -> DuckDuckGoSearch:
    return DuckDuckGoSearch(
        text_search, min_interval_s=1.0, retry_delay_s=2.0, clock=lambda: 0.0, sleep=sleep
    )


async def test_duckduckgo_errors_raise_search_error_after_one_retry() -> None:
    attempts: list[str] = []

    def text_search(query: str, num: int) -> list[dict[str, Any]]:
        attempts.append(query)
        raise DDGSException("rate limited")

    with pytest.raises(SearchError, match="rate limited"):
        await ddg(text_search, FakeSleep()).search("pgvector", num=5)

    assert attempts == ["pgvector", "pgvector"]


async def test_duckduckgo_retry_recovers_from_a_transient_failure() -> None:
    sleep = FakeSleep()
    outcomes: list[Exception | list[dict[str, Any]]] = [
        DDGSException("error sending request"),
        [{"title": "A", "href": "https://a.example/", "body": ""}],
    ]

    def text_search(query: str, num: int) -> list[dict[str, Any]]:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    response = await ddg(text_search, sleep).search("pgvector", num=5)

    assert [r.url for r in response.results] == ["https://a.example/"]
    assert 2.0 in sleep.calls


async def test_duckduckgo_call_that_never_returns_times_out_and_frees_the_queue() -> None:
    release = threading.Event()

    def stuck_then_fine(query: str, num: int) -> list[dict[str, Any]]:
        if query == "stuck":
            release.wait(timeout=5)
            return []
        return [{"title": "A", "href": "https://a.example/", "body": ""}]

    search = DuckDuckGoSearch(
        stuck_then_fine,
        min_interval_s=0,
        retry_delay_s=0,
        call_timeout_s=0.05,
        sleep=FakeSleep(),
    )
    try:
        with pytest.raises(SearchError, match="timed out"):
            await search.search("stuck", num=5)
        response = await search.search("fine", num=5)
    finally:
        release.set()

    assert [r.url for r in response.results] == ["https://a.example/"]


async def test_duckduckgo_spaces_out_consecutive_queries() -> None:
    sleep = FakeSleep()

    def text_search(query: str, num: int) -> list[dict[str, Any]]:
        return []

    search = ddg(text_search, sleep)
    await search.search("first", num=5)
    await search.search("second", num=5)

    assert sleep.calls == [1.0]


class CountingProvider:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, *, num: int) -> SearchResponse:
        self.calls += 1
        return SearchResponse(results=[SearchResult(title=query, url="https://a.example/", rank=1)])


async def test_cached_search_reuses_results_for_the_same_query(tmp_path: Path) -> None:
    provider = CountingProvider()
    search = CachedSearch(provider, SqliteCache(tmp_path / "c.db"))

    first = await search.search("pgvector", num=10)
    second = await search.search("  PGVector ", num=10)

    assert first == second
    assert provider.calls == 1


async def test_cached_search_keys_on_result_count(tmp_path: Path) -> None:
    provider = CountingProvider()
    search = CachedSearch(provider, SqliteCache(tmp_path / "c.db"))

    await search.search("pgvector", num=5)
    await search.search("pgvector", num=10)

    assert provider.calls == 2


def test_factory_prefers_serper_when_a_key_is_set(tmp_path: Path) -> None:
    settings = ResearchSettings(_env_file=None, serper_api_key="k", cache_path=tmp_path / "c.db")

    provider = make_search_provider(settings, httpx.AsyncClient(), SqliteCache(tmp_path / "c.db"))

    assert provider.name == "serper"


def test_factory_falls_back_to_duckduckgo_without_a_key(tmp_path: Path) -> None:
    settings = ResearchSettings(_env_file=None, serper_api_key=None, cache_path=tmp_path / "c.db")

    provider = make_search_provider(settings, httpx.AsyncClient(), SqliteCache(tmp_path / "c.db"))

    assert provider.name == "duckduckgo"
