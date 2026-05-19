"""Aiven Postgres + MySQL connection helpers.

Aiven runs on public DNS but requires SSL with the Aiven CA chain.
Many app authors hit the verify-cert wall + reach for verify=False —
this module gives them a proper TLS path via psycopg/pymysql defaults.

CONFIG (env)

    AIVEN_PG_URL       postgres://USER:PWD@<your-cluster-host>:PORT/dbname?sslmode=require
    AIVEN_MYSQL_URL    mysql://USER:PWD@<your-cluster-host>:PORT/dbname?ssl-mode=REQUIRED

USAGE

    from aspire_data.aiven import aiven_postgres_conn, aiven_mysql_conn
    with aiven_postgres_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT now()")
        print(cur.fetchall())

NOTES

    - Aiven URLs MUST include sslmode=require (PG) or ssl-mode=REQUIRED (MySQL).
    - These return SYNCHRONOUS connections. For async, use the async
      versions in aspire_data.oracle (the patterns are the same).
    - On Aspire laptop, Aiven `:16439` is intercepted by GlobalProtect's
      MITM. The truststore fix in `aspire_data` __init__ handles that
      transparently for HTTPS calls — but for raw DB connections you
      typically need to use a different path (Connect or VM).
"""
from __future__ import annotations

import os
from contextlib import contextmanager


def _ensure(url_env: str) -> str:
    url = os.environ.get(url_env, "").strip()
    if not url:
        raise RuntimeError(f"{url_env} not set")
    return url


@contextmanager
def aiven_postgres_conn(url: str | None = None):
    """Yield a psycopg connection to Aiven Postgres. Context-manager
    closes the connection on exit. Use with the `with` statement.

    Requires `psycopg[binary]` (install via the [postgres] extra).
    """
    import psycopg
    url = url or _ensure("AIVEN_PG_URL")
    conn = psycopg.connect(url)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def aiven_mysql_conn(url: str | None = None):
    """Yield a pymysql connection to Aiven MySQL. Context-manager
    closes the connection on exit.

    Requires `pymysql` (install via the [mysql] extra).
    """
    import pymysql
    from urllib.parse import urlparse
    url = url or _ensure("AIVEN_MYSQL_URL")
    p = urlparse(url)
    conn = pymysql.connect(
        host=p.hostname,
        port=p.port or 16439,
        user=p.username,
        password=p.password,
        database=(p.path or "/").lstrip("/") or None,
        ssl={"required": True},
        autocommit=False,
        charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()
