"""MotherDuck (cloud DuckDB) connection — personal/scratch warehouse only.

POLICY: NO Aspire athlete PII goes to MotherDuck (data sovereignty —
the account lives in AWS Frankfurt). Scratch analytics and personal
projects only.

NETWORK: blocked from the Aspire laptop (CDN 403/503 + outbound 5432
filtered). Works from Posit Connect, the VMs, and personal machines.

CONFIG (env)
    MOTHERDUCK_TOKEN    service token for the MotherDuck account

USAGE
    from aspire_data.motherduck import duckdb_conn
    con = duckdb_conn()                # md: root
    con = duckdb_conn("scratch_db")    # specific database
    con.sql("SELECT 42").fetchall()
"""
from __future__ import annotations

__all__ = ["duckdb_conn"]

import os


def duckdb_conn(database: str = ""):
    """Open a native DuckDB connection to MotherDuck.

    `duckdb` is imported lazily so the package works without the
    [duckdb] extra installed. Raises RuntimeError when
    MOTHERDUCK_TOKEN is missing (checked before the import so the
    error is actionable even without duckdb present).
    """
    token = os.environ.get("MOTHERDUCK_TOKEN")
    if not token:
        raise RuntimeError(
            "MOTHERDUCK_TOKEN not set — needed for MotherDuck. "
            "(Reminder: scratch data only, never Aspire athlete PII.)")
    import duckdb  # lazy — optional [duckdb] extra
    return duckdb.connect(f"md:{database}?motherduck_token={token}")
