"""aspire_data — connection clients for every Aspire backing store.

Public-safe by design: all hostnames, keys, and credentials come from
env vars. The package contains *patterns* not *secrets*.

USAGE
=====

Auto-fix the Aspire laptop TLS chain on import (truststore over certifi):

    import aspire_data   # truststore.inject_into_ssl() fires here

Then grab whichever client(s) your app needs:

    from aspire_data.connect import ConnectClient, hana_sql, render_pdf
    from aspire_data.sports_api import SportsApi
    from aspire_data.sams import SamsClient
    from aspire_data.aiven import aiven_postgres_conn, aiven_mysql_conn
    from aspire_data.oracle import mysql_pool, postgres_pool
    from aspire_data.hetzner import HetznerClient
    from aspire_data.hana import hana_sql_via_connect, hana_subprocess
    from aspire_data.motherduck import duckdb_conn
    from aspire_data.posit import ConnectAdminClient
    from aspire_data.aspire_kb import kb_search, kb_sources, kb_stats
    from aspire_data.identity import resolve_to_sams

Most clients read env vars on construction. Pattern:

    api = SportsApi()        # reads SPORTS_API_URL
    api.tool("search_athlete_anywhere", q="van Niekerk", limit=5)

CONFIG via env (see .env.example):

    CONNECT_BASE_URL      CONNECT_API_KEY    SPORTS_API_URL
    HANA_API_GUID         RENDER_API_GUID    JOBS_API_GUID
    NOTIFY_API_GUID       ASPIRE_KB_API_GUID SPORTS_API_KEY
    SAMS_BASE_URL         SAMS_CLIENT_ID     SAMS_CLIENT_SECRET
    HETZNER_PROXY_BASE    HETZNER_PROXY_KEY  ORACLE_MYSQL_URL
    AIVEN_PG_URL          AIVEN_MYSQL_URL    ORACLE_PG_URL
    MOTHERDUCK_TOKEN      INSECURE_API_TLS   (laptop fallback only)

CLI

    aspire-data status    # ping every env-configured endpoint, print verdicts
"""
from __future__ import annotations

__version__ = "0.8.2"

__all__ = [
    "__version__",
    "inject_truststore",
]

# Auto-load .env in the caller's CWD (if dotenv available)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Auto-fix the Aspire MITM TLS chain — see ssl_fix.py for details.
# Idempotent and silent; only effective on Aspire laptop / similar
# corp-CA environments. No-op on properly-trusted Connect / VMs.
from .ssl_fix import inject_truststore
inject_truststore()
