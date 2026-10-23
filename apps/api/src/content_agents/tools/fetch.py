"""Polite, guarded page fetching with main-content extraction (Plan.md §11 Step 3)."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Literal
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from pydantic import BaseModel

from content_agents.cache import SqliteCache
from content_agents.security.url_guard import (
    Resolver,
    UnsafeURLError,
    ensure_public_url,
    system_resolve,
)
from content_agents.tools.sources import canonical_url

FetchFailure = Literal[
    "unsafe_url",
    "robots",
    "http_status",
    "not_html",
    "too_large",
    "no_content",
    "redirects",
    "network",
]

DEFAULT_USER_AGENT = "ContentAgentsBot/0.1 (research crawler; respects robots.txt)"
PAGE_TTL_S = 7 * 24 * 3600
FAILURE_TTL_S = 6 * 3600
_TRANSIENT_STATUSES = frozenset({408, 429})
REQUEST_TIMEOUT_S = 20.0
# trafilatura falls back to boilerplate (menus, cookie banners) when a page has no
# article, so a tiny extraction is treated as no content.
MIN_CONTENT_WORDS = 20
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_HTML_TYPES = ("text/html", "application/xhtml+xml")


class FetchError(Exception):
    def __init__(self, reason: FetchFailure, detail: str, *, transient: bool = False) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: FetchFailure = reason
        self.detail = detail
        # Transient failures (network, 429, 5xx) are retried next run; others are remembered.
        self.transient = transient


class _RememberedFailure(BaseModel):
    reason: FetchFailure
    detail: str


class FetchedPage(BaseModel):
    url: str
    final_url: str
    title: str | None = None
    author: str | None = None
    published: date | None = None
    markdown: str
    word_count: int


class PageFetcher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        cache: SqliteCache,
        *,
        resolve: Resolver = system_resolve,
        user_agent: str = DEFAULT_USER_AGENT,
        max_bytes: int = 3_000_000,
        max_redirects: int = 5,
        per_host_delay_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        ttl_s: float = PAGE_TTL_S,
    ) -> None:
        self._http = http
        self._cache = cache
        self._resolve = resolve
        self._headers = {"User-Agent": user_agent}
        self._user_agent = user_agent
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._delay_s = per_host_delay_s
        self._clock = clock
        self._sleep = sleep
        self._ttl_s = ttl_s
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}

    async def fetch(self, url: str) -> FetchedPage:
        key = canonical_url(url)
        if cached := await self._cache.get("pages", key):
            return FetchedPage.model_validate_json(cached)
        if remembered := await self._cache.get("page_failures", key):
            failure = _RememberedFailure.model_validate_json(remembered)
            raise FetchError(failure.reason, f"{failure.detail} (remembered)")
        try:
            final_url, html = await self._download(url)
            page = await asyncio.to_thread(_extract, key, final_url, html)
        except FetchError as e:
            if not e.transient:
                failure = _RememberedFailure(reason=e.reason, detail=e.detail)
                await self._cache.set(
                    "page_failures", key, failure.model_dump_json(), ttl_s=FAILURE_TTL_S
                )
            raise
        await self._cache.set("pages", key, page.model_dump_json(), ttl_s=self._ttl_s)
        return page

    async def _download(self, url: str) -> tuple[str, str]:
        current = url
        for _ in range(self._max_redirects + 1):
            try:
                await ensure_public_url(current, resolve=self._resolve)
            except UnsafeURLError as e:
                # Checked before any request, so cheap to redo; a DNS blip shouldn't stick.
                raise FetchError("unsafe_url", str(e), transient=True) from e

            host = urlsplit(current).hostname or ""
            # One request at a time per host keeps the crawl polite under concurrency.
            async with self._host_locks.setdefault(host, asyncio.Lock()):
                await self._check_robots(current)
                await self._wait_turn(host)
                status, location, html = await self._get(current)

            if status in _REDIRECT_CODES:
                if not location:
                    raise FetchError("http_status", f"HTTP {status} without Location: {current}")
                current = urljoin(current, location)
                continue
            if status >= 400:
                raise FetchError(
                    "http_status",
                    f"HTTP {status} for {current}",
                    transient=status >= 500 or status in _TRANSIENT_STATUSES,
                )
            return current, html
        raise FetchError("redirects", f"more than {self._max_redirects} redirects from {url}")

    async def _get(self, url: str) -> tuple[int, str | None, str]:
        try:
            async with self._http.stream(
                "GET",
                url,
                headers=self._headers,
                follow_redirects=False,
                timeout=REQUEST_TIMEOUT_S,
            ) as response:
                if response.status_code in _REDIRECT_CODES or response.status_code >= 400:
                    return response.status_code, response.headers.get("location"), ""
                content_type = response.headers.get("content-type", "").lower()
                if not content_type.startswith(_HTML_TYPES):
                    raise FetchError("not_html", f"{content_type or 'unknown type'} at {url}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_bytes:
                        raise FetchError("too_large", f"over {self._max_bytes} bytes at {url}")
                encoding = response.charset_encoding or "utf-8"
                return response.status_code, None, body.decode(encoding, errors="replace")
        except httpx.HTTPError as e:
            raise FetchError("network", f"{url}: {e}", transient=True) from e

    async def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = await self._load_robots(origin)
        rules = self._robots[origin]
        if rules is not None and not rules.can_fetch(self._user_agent, url):
            raise FetchError("robots", f"robots.txt disallows {url}")

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        """Parsed rules, or None (allow all) when the site has no readable robots.txt."""
        try:
            response = await self._http.get(
                f"{origin}/robots.txt", headers=self._headers, timeout=REQUEST_TIMEOUT_S
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        rules = RobotFileParser()
        rules.parse(response.text.splitlines())
        return rules

    async def _wait_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = last + self._delay_s - self._clock()
            if wait > 0:
                await self._sleep(wait)
        self._last_request[host] = self._clock()


def _extract(url: str, final_url: str, html: str) -> FetchedPage:
    markdown = trafilatura.extract(
        html,
        url=final_url,
        output_format="markdown",
        include_formatting=True,
        include_comments=False,
        include_tables=True,
    )
    word_count = len(markdown.split()) if markdown else 0
    if not markdown or word_count < MIN_CONTENT_WORDS:
        raise FetchError("no_content", f"no main content at {final_url}")
    metadata = trafilatura.extract_metadata(html, default_url=final_url)
    return FetchedPage(
        url=url,
        final_url=final_url,
        title=metadata.title if metadata else None,
        author=metadata.author if metadata else None,
        published=_parse_date(metadata.date if metadata else None),
        markdown=markdown,
        word_count=word_count,
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
