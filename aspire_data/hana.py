"""SAP HANA access — three paths, picks best available.

Aspire HANA can be reached three ways:

  1. **hana-api on Connect** (RECOMMENDED) — no client lib needed,
     works from anywhere with CONNECT_API_KEY. Use this from Dash
     apps, Quarto reports, anything that isn't already inside
     the Aspire data network.

  2. **hdbcli direct** — fastest, but requires the SAP HDB Python
     client. On Aspire laptop this is AV-blocked by default;
     install via the Kakao PyPI mirror works.

  3. **hdbsql.exe subprocess** — fallback when hdbcli won't install.
     Spawns the SAP-shipped CLI, parses its CSV output. Slowest
     but doesn't need a Python wheel.

CONFIG

  Path 1 (Connect):  CONNECT_API_KEY, HANA_API_GUID, CONNECT_BASE_URL
  Path 2 (hdbcli):   HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD
  Path 3 (hdbsql):   HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD,
                     HDBSQL_PATH (path to hdbsql.exe / hdbsql binary)

USAGE

    from aspire_data.hana import hana_sql_via_connect
    rows = hana_sql_via_connect("SELECT TOP 5 * FROM SCHEMA.VIEW")

    # Or direct (if hdbcli installed):
    from aspire_data.hana import hana_sql_direct
    rows = hana_sql_direct("SELECT TOP 5 ...")
"""
from __future__ import annotations

__all__ = ['hana_sql_via_connect', 'hana_sql_direct', 'hana_sql_subprocess']

import csv
import io
import os
import subprocess
from typing import Any


# ---------- Path 1: via hana-api on Connect (RECOMMENDED) ----------

def hana_sql_via_connect(sql: str, *, params: dict | None = None,
                          row_limit: int | None = None) -> list[dict]:
    """Run SQL via the hana-api FastAPI on Posit Connect.
    Just a delegate to aspire_data.connect.hana_sql for one import."""
    from .connect import hana_sql
    return hana_sql(sql, params=params, row_limit=row_limit)


# ---------- Path 2: direct hdbcli ----------

def hana_sql_direct(sql: str, *, params: tuple | None = None) -> list[dict]:
    """Run SQL via hdbcli subprocess-free direct driver.

    Requires `pip install hdbcli`. On Aspire laptop, install via the
    Kakao mirror to bypass AV:

        pip install hdbcli --index-url https://mirror.kakao.com/pypi/simple/ \\
                            --trusted-host mirror.kakao.com
    """
    from hdbcli import dbapi  # noqa: PLC0415 — optional dep
    conn = dbapi.connect(
        address=os.environ["HANA_HOST"],
        port=int(os.environ.get("HANA_PORT", "30015")),
        user=os.environ["HANA_USER"],
        password=os.environ["HANA_PASSWORD"],
        encrypt=True,
        sslValidateCertificate=False,
    )
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ---------- Path 3: hdbsql.exe subprocess fallback ----------

def hana_sql_subprocess(sql: str, *, timeout: int = 60) -> list[dict]:
    """Run SQL via the SAP-shipped hdbsql.exe binary. Parses CSV output.

    Last-resort path when hdbcli won't install. Requires HDBSQL_PATH
    pointing at the binary (typically inside the HDB Client install).
    """
    hdbsql = os.environ.get("HDBSQL_PATH", "hdbsql")
    cmd = [
        hdbsql,
        "-A",  # ASCII
        "-x",  # no header line
        "-n", f"{os.environ['HANA_HOST']}:{os.environ.get('HANA_PORT', '30015')}",
        "-u",  os.environ["HANA_USER"],
        "-p",  os.environ["HANA_PASSWORD"],
        "-o",  ",",
        sql,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"hdbsql failed: {proc.stderr[:300]}")
    reader = csv.reader(io.StringIO(proc.stdout))
    rows = list(reader)
    if not rows:
        return []
    # First row is header in -x mode? No — -x suppresses it. Caller
    # supplies the column list contextually.
    return [{"col_" + str(i): v for i, v in enumerate(r)} for r in rows]
