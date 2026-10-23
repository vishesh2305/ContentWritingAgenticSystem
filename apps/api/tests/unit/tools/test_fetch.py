from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest

from content_agents.cache import SqliteCache
from content_agents.tools.fetch import FetchError, PageFetcher

ARTICLE_HTML = (
    "<html><head><title>pgvector guide | Example</title>"
    '<meta name="author" content="Jane Doe">'
    '<meta property="article:published_time" content="2026-03-02"></head>'
    "<body><nav>Home About</nav><article><h1>pgvector guide</h1><p>"
    + "pgvector adds vector search to Postgres. " * 20
    + "</p><h2>HNSW</h2><p>"
    + "HNSW builds a graph index for fast queries. " * 20
    + "</p></article><footer>(c) 2026</footer></body></html>"
)
HTML = {"content-type": "text/html; charset=utf-8"}
NOT_FOUND = httpx.Response(404, text="not found")


async def fake_resolve(host: str) -> list[str]:
    return ["10.0.0.1"] if host == "evil.test" else ["93.184.216.34"]


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


Routes = dict[str, httpx.Response | Callable[[], httpx.Response]]


class FakeWeb:
    def __init__(self, routes: Routes) -> None:
        self.routes = routes
        self.hits: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits[url] += 1
        route = self.routes.get(url, NOT_FOUND)
        return route() if callable(route) else route


def make_fetcher(
    web: FakeWeb,
    tmp_path: Path,
    *,
    sleep: FakeSleep | None = None,
    max_bytes: int = 3_000_000,
) -> PageFetcher:
    return PageFetcher(
        httpx.AsyncClient(transport=httpx.MockTransport(web.handler)),
        SqliteCache(tmp_path / "cache.db"),
        resolve=fake_resolve,
        max_bytes=max_bytes,
        per_host_delay_s=1.0,
        clock=lambda: 0.0,
        sleep=sleep or FakeSleep(),
    )


def page(html: str = ARTICLE_HTML) -> httpx.Response:
    return httpx.Response(200, headers=HTML, text=html)


async def test_article_is_extracted_to_markdown_with_metadata(tmp_path: Path) -> None:
    web = FakeWeb({"https://a.test/post": page()})

    fetched = await make_fetcher(web, tmp_path).fetch("https://a.test/post")

    assert fetched.final_url == "https://a.test/post"
    assert fetched.markdown.startswith("# pgvector guide")
    assert "## HNSW" in fetched.markdown
    assert "Home About" not in fetched.markdown
    assert fetched.title == "pgvector guide"
    assert fetched.author == "Jane Doe"
    assert fetched.published == date(2026, 3, 2)
    assert fetched.word_count > 100


async def test_robots_disallow_blocks_the_page(tmp_path: Path) -> None:
    robots = httpx.Response(200, text="User-agent: *\nDisallow: /private")
    web = FakeWeb({"https://a.test/robots.txt": robots, "https://a.test/private/x": page()})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("https://a.test/private/x")

    assert exc.value.reason == "robots"
    assert web.hits["https://a.test/private/x"] == 0


async def test_robots_is_fetched_once_per_host(tmp_path: Path) -> None:
    web = FakeWeb({"https://a.test/one": page(), "https://a.test/two": page()})
    fetcher = make_fetcher(web, tmp_path)

    await fetcher.fetch("https://a.test/one")
    await fetcher.fetch("https://a.test/two")

    assert web.hits["https://a.test/robots.txt"] == 1


async def test_redirect_to_public_page_is_followed(tmp_path: Path) -> None:
    moved = httpx.Response(301, headers={"location": "/new"})
    web = FakeWeb({"https://a.test/old": moved, "https://a.test/new": page()})

    fetched = await make_fetcher(web, tmp_path).fetch("https://a.test/old")

    assert fetched.final_url == "https://a.test/new"


async def test_redirect_to_private_address_is_blocked(tmp_path: Path) -> None:
    moved = httpx.Response(302, headers={"location": "http://evil.test/admin"})
    web = FakeWeb({"https://a.test/go": moved})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("https://a.test/go")

    assert exc.value.reason == "unsafe_url"
    assert web.hits["http://evil.test/admin"] == 0


async def test_redirect_loop_gives_up(tmp_path: Path) -> None:
    loop = httpx.Response(302, headers={"location": "/loop"})
    web = FakeWeb({"https://a.test/loop": loop})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("https://a.test/loop")

    assert exc.value.reason == "redirects"


async def test_private_start_url_is_never_requested(tmp_path: Path) -> None:
    web = FakeWeb({})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("http://127.0.0.1/secret")

    assert exc.value.reason == "unsafe_url"
    assert sum(web.hits.values()) == 0


async def test_http_error_status_is_reported(tmp_path: Path) -> None:
    web = FakeWeb({"https://a.test/gone": httpx.Response(410)})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("https://a.test/gone")

    assert exc.value.reason == "http_status"


async def test_non_html_content_is_skipped(tmp_path: Path) -> None:
    pdf = httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    web = FakeWeb({"https://a.test/file.pdf": pdf})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("https://a.test/file.pdf")

    assert exc.value.reason == "not_html"


async def test_oversized_page_is_rejected(tmp_path: Path) -> None:
    web = FakeWeb({"https://a.test/huge": page("<p>" + "x" * 5_000 + "</p>")})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path, max_bytes=1_000).fetch("https://a.test/huge")

    assert exc.value.reason == "too_large"


async def test_page_without_main_content_is_rejected(tmp_path: Path) -> None:
    web = FakeWeb({"https://a.test/empty": page("<html><body><nav>Menu</nav></body></html>")})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("https://a.test/empty")

    assert exc.value.reason == "no_content"


async def test_network_failure_is_reported(tmp_path: Path) -> None:
    def refuse() -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    web = FakeWeb({"https://a.test/robots.txt": NOT_FOUND, "https://a.test/down": refuse})

    with pytest.raises(FetchError) as exc:
        await make_fetcher(web, tmp_path).fetch("https://a.test/down")

    assert exc.value.reason == "network"


async def test_second_fetch_is_served_from_cache(tmp_path: Path) -> None:
    web = FakeWeb({"https://a.test/post?utm_source=x": page(), "https://a.test/post": page()})

    first = await make_fetcher(web, tmp_path).fetch("https://a.test/post?utm_source=x")
    second = await make_fetcher(web, tmp_path).fetch("https://a.test/post")

    assert second == first
    assert web.hits["https://a.test/post?utm_source=x"] == 1
    assert web.hits["https://a.test/post"] == 0


async def test_permanent_failure_is_remembered(tmp_path: Path) -> None:
    web = FakeWeb({"https://a.test/members": httpx.Response(403)})

    for _ in range(2):
        with pytest.raises(FetchError) as exc:
            await make_fetcher(web, tmp_path).fetch("https://a.test/members")
        assert exc.value.reason == "http_status"

    assert web.hits["https://a.test/members"] == 1


@pytest.mark.parametrize("status", [429, 503])
async def test_transient_failure_is_retried_next_time(tmp_path: Path, status: int) -> None:
    web = FakeWeb({"https://a.test/busy": httpx.Response(status)})

    for _ in range(2):
        with pytest.raises(FetchError):
            await make_fetcher(web, tmp_path).fetch("https://a.test/busy")

    assert web.hits["https://a.test/busy"] == 2


async def test_requests_to_the_same_host_are_spaced_out(tmp_path: Path) -> None:
    sleep = FakeSleep()
    web = FakeWeb(
        {"https://a.test/one": page(), "https://a.test/two": page(), "https://b.test/x": page()}
    )
    fetcher = make_fetcher(web, tmp_path, sleep=sleep)

    await fetcher.fetch("https://a.test/one")
    await fetcher.fetch("https://b.test/x")
    await fetcher.fetch("https://a.test/two")

    assert sleep.calls == [1.0]
