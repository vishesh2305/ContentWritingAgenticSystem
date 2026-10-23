from pathlib import Path

from content_agents.cache import SqliteCache


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def test_value_round_trips(tmp_path: Path) -> None:
    cache = SqliteCache(tmp_path / "cache.db")

    await cache.set("pages", "https://example.com", '{"title": "x"}', ttl_s=60)

    assert await cache.get("pages", "https://example.com") == '{"title": "x"}'


async def test_missing_key_returns_none(tmp_path: Path) -> None:
    assert await SqliteCache(tmp_path / "cache.db").get("pages", "nope") is None


async def test_entry_expires_after_ttl(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = SqliteCache(tmp_path / "cache.db", clock=clock)
    await cache.set("serp", "pgvector", "[]", ttl_s=60)

    clock.now += 59
    assert await cache.get("serp", "pgvector") == "[]"
    clock.now += 2
    assert await cache.get("serp", "pgvector") is None


async def test_namespaces_are_isolated(tmp_path: Path) -> None:
    cache = SqliteCache(tmp_path / "cache.db")
    await cache.set("serp", "k", "search", ttl_s=60)
    await cache.set("pages", "k", "page", ttl_s=60)

    assert await cache.get("serp", "k") == "search"
    assert await cache.get("pages", "k") == "page"


async def test_set_overwrites_existing_value(tmp_path: Path) -> None:
    cache = SqliteCache(tmp_path / "cache.db")
    await cache.set("pages", "k", "old", ttl_s=60)
    await cache.set("pages", "k", "new", ttl_s=60)

    assert await cache.get("pages", "k") == "new"


async def test_entries_persist_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "cache.db"
    await SqliteCache(path).set("pages", "k", "kept", ttl_s=60)

    assert await SqliteCache(path).get("pages", "k") == "kept"
