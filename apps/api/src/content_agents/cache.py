"""Small TTL cache in a local SQLite file (search results, fetched pages).

SQLite keeps the project runnable without Postgres; swap for a Postgres-backed
implementation with the same get/set interface when the database lands (Step 4).
"""

import asyncio
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (namespace, key)
)
"""


class SqliteCache:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(_SCHEMA)

    async def get(self, namespace: str, key: str) -> str | None:
        return await asyncio.to_thread(self._get, namespace, key)

    async def set(self, namespace: str, key: str, value: str, *, ttl_s: float) -> None:
        await asyncio.to_thread(self._set, namespace, key, value, self._clock() + ttl_s)

    def _get(self, namespace: str, key: str) -> str | None:
        with closing(sqlite3.connect(self._path)) as db:
            row = db.execute(
                "SELECT value, expires_at FROM cache WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        if row is None or row[1] <= self._clock():
            return None
        value: str = row[0]
        return value

    def _set(self, namespace: str, key: str, value: str, expires_at: float) -> None:
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute(
                "INSERT INTO cache (namespace, key, value, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (namespace, key) DO UPDATE "
                "SET value = excluded.value, expires_at = excluded.expires_at",
                (namespace, key, value, expires_at),
            )
