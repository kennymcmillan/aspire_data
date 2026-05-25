# Changelog

All notable changes to `aspire_data`.

## [0.3.0] — 2026-05-25

### Added — SAMS PlayerEnrollmentPeriods integration

Promoted from the `DASH_Anthro` Connect app. Canonical way to get
authoritative sport + discipline + competition event for an athlete.

**New on `SamsClient`:**

- `get_all_enrollment_periods() -> list[dict]` — fetches every row from
  `/api/ExternalApps/PlayerEnrollmentPeriods` (1000+ rows, ~415 KB),
  cached at the same TTL as the athlete-context cache (1h default).
- `get_current_enrollment(player_id: int) -> dict` — picks the most
  relevant **current** (endDate=None) enrollment for one player:
  primary first, else most-recent `startDate`. Returns `{}` if none.
- `get_athlete_context(player_id, *, enrich=False)` — new `enrich`
  flag. When `True`, merges current enrollment data onto the returned
  dict: `sport_id`, `sport`, `discipline_id`, `discipline`,
  `target_event` (first non-TBD token of `targetEventNames`),
  `target_event_raw`, `player_type`, `coach_name`. Default `False`
  preserves the existing API for current callers.

**New module-level helper:**

- `first_target_event(raw: str | None) -> str | None` — splits a
  comma-separated `targetEventNames` string, skips "TBD" tokens,
  returns the first usable event.

### Why

The `/api/ExternalApps/player/{id}` endpoint doesn't reliably surface
`sportId` for multi-sport athletes, and never surfaces the specific
event (Foil/Epee/100m/Hammer Throw/etc). Enrollment periods are the
authoritative source for both.

## [0.2.0] — 2026-05-20

### Added
- **`tests/`** — 46 pytest cases covering smoke imports, ConnectClient
  + the 4 convenience wrappers, SportsApi (parameters envelope + records
  unwrap), SamsClient (search + MRN cache + auth), Oracle pool URL
  parser + env-required validation, Aiven env-required, Hetzner proxy
  + direct mode, Posit admin, the CLI (env / status / status --json).
  Stubs httpx via a recording mock — no live network calls.
  **Run time: ~2 s.**
- **`pyproject.toml`** — pytest config (testpaths, addopts).
- **`__all__` exports** on every public module — clearer surface for
  IDEs + cleaner `from aspire_data.X import *` semantics.
- **CI matrix** — `.github/workflows/audit.yml` now runs the audit
  AND the pytest suite on Python 3.10 / 3.11 / 3.12.

### Changed
- Bumped `__version__` to 0.2.0 across `__init__.py` + `setup.py`.

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
