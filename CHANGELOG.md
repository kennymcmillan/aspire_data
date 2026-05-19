# Changelog

All notable changes to `aspire_data`.

## [0.1.0] — 2026-05-20

Initial release. Sibling of `aspire_dash` covering the data layer.

### Added

- **`aspire_data.ssl_fix`** — `inject_truststore()` auto-fires on
  package import. Fixes the Aspire MITM TLS chain (GlobalProtect
  re-signs all HTTPS with a corp CA that's in the Windows trust
  store but not in `certifi`).
- **`aspire_data.connect`** — `ConnectClient(guid)` for any Connect
  content, plus convenience wrappers: `hana_sql`, `hana_view`,
  `render_pdf`, `render_doc`, `jobs_submit`, `jobs_get`, `jobs_wait`,
  `notify_send`.
- **`aspire_data.sports_api`** — `SportsApi.tool(name, **params)`
  hides the `{"parameters": {...}}` request envelope + the
  `result.data.records` response unwrap.
- **`aspire_data.sams`** — `SamsClient` with picker drill-down,
  `search`, `get_athlete_by_mrn`, `get_athlete_context`,
  `list_training_plans`, `get_plan_roster`, `list_sport_roster`.
  Parallel sport-roster fan-out via internal `ThreadPoolExecutor`
  (10 workers). 1h athlete-context cache, 30min sport-roster cache.
- **`aspire_data.oracle`** — async pools for Oracle VM:
  - `mysql_pool()` with the right defaults (`autocommit=False`,
    `utf8mb4`, `connect_timeout=10`)
  - `with_fresh_snapshot(conn)` context-manager that
    `await conn.rollback()` before a read — fixes the stale
    REPEATABLE-READ snapshot bug that has bitten every consumer
  - `postgres_pool()` standard `asyncpg` pool
- **`aspire_data.aiven`** — sync context-managers for Aiven Postgres
  (`psycopg`) and Aiven MySQL (`pymysql`) with SSL required.
- **`aspire_data.hetzner`** — `HetznerClient` with the proxy-vs-direct
  routing baked in. Defaults to the proxy (HTTPS 443) — set
  `direct=True` only on the Oracle VM or via SSH tunnel.
- **`aspire_data.hana`** — three paths to SAP HANA in one module:
  - `hana_sql_via_connect()` — via hana-api on Connect (RECOMMENDED)
  - `hana_sql_direct()`     — via `hdbcli` (install via Kakao mirror
    on Aspire laptop)
  - `hana_sql_subprocess()` — via `hdbsql.exe` subprocess (last-resort)
- **`aspire_data.posit`** — `ConnectAdminClient` for the Connect REST
  API: `get_content`, `list_content`, `patch_content`, `list_jobs`,
  `get_job_log`.
- **CLI**: `aspire-data status` pings every env-configured endpoint
  and prints OK / FAIL / skip verdicts. `aspire-data env` shows
  which env vars are set (without leaking values).
- **`.env.example`** at the repo root — copy + fill in.

### Public-safe design

- All hostnames + keys come from env vars
- README + tests use placeholders only
- No hardcoded internal Aspire infrastructure
