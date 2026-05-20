"""Oracle VM Postgres + MySQL helpers.

Wraps two pool factories with the gotchas every consumer has hit:

  - aiomysql: connections default to `autocommit=False`. After a
    SELECT they keep a stale REPEATABLE-READ snapshot until reused.
    Subsequent reads on the SAME connection miss writes committed by
    OTHER connections in the meantime. We `await conn.rollback()`
    before every read SELECT to release the snapshot (or call
    `oracle.with_fresh_snapshot(conn)` as a context manager).
  - asyncpg: standard pool — nothing exotic.

CONFIG (env)

    ORACLE_MYSQL_URL    mysql://user:pwd@host:3306/db
    ORACLE_PG_URL       postgres://user:pwd@host:5432/db

USAGE

    from aspire_data.oracle import mysql_pool, postgres_pool, with_fresh_snapshot

    pool = await mysql_pool()
    async with pool.acquire() as conn:
        async with with_fresh_snapshot(conn):
            async with conn.cursor() as cur:
                await cur.execute("SELECT ...")
                rows = await cur.fetchall()
"""
from __future__ import annotations

__all__ = ['mysql_pool', 'postgres_pool', 'with_fresh_snapshot']

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiomysql  # type: ignore
    import asyncpg   # type: ignore


def _mysql_kwargs_from_url(url: str) -> dict:
    """Parse a mysql:// URL into aiomysql kwargs."""
    from urllib.parse import urlparse
    p = urlparse(url)
    return {
        "host":     p.hostname,
        "port":     p.port or 3306,
        "user":     p.username,
        "password": p.password,
        "db":       (p.path or "/").lstrip("/") or None,
    }


async def mysql_pool(url: str | None = None, *,
                     minsize: int = 1, maxsize: int = 8,
                     connect_timeout: int = 10):
    """Async aiomysql pool. URL defaults to $ORACLE_MYSQL_URL."""
    import aiomysql
    url = url or os.environ.get("ORACLE_MYSQL_URL")
    if not url:
        raise RuntimeError("ORACLE_MYSQL_URL not set")
    return await aiomysql.create_pool(
        minsize=minsize, maxsize=maxsize,
        autocommit=False, charset="utf8mb4",
        connect_timeout=connect_timeout,
        **_mysql_kwargs_from_url(url),
    )


@asynccontextmanager
async def with_fresh_snapshot(conn):
    """Release any stale REPEATABLE-READ snapshot before a read.

        async with pool.acquire() as conn:
            async with with_fresh_snapshot(conn):
                async with conn.cursor() as cur:
                    await cur.execute("SELECT ...")

    Equivalent to calling `await conn.rollback()` first, but signals
    intent + keeps the pattern visible in the call site.
    """
    await conn.rollback()
    yield conn


async def postgres_pool(url: str | None = None, *,
                        min_size: int = 1, max_size: int = 10):
    """Async asyncpg pool. URL defaults to $ORACLE_PG_URL."""
    import asyncpg
    url = url or os.environ.get("ORACLE_PG_URL")
    if not url:
        raise RuntimeError("ORACLE_PG_URL not set")
    return await asyncpg.create_pool(
        dsn=url, min_size=min_size, max_size=max_size,
    )
