# Changelog

All notable changes to `aspire_data`.

## [0.7.0] — 2026-06-10

### Added — write-safe + REST surface on SportsApi (unblocks app migrations)

- `SportsApi.tool_raw(name, **params)` — full envelope (ok / result.success /
  rows_affected), for callers that need more than the records list.
- `SportsApi.tool_write(name, **params)` — write tools with the inner-success
  guard: the Sports API returns HTTP 200 even when a write FAILS; this raises
  `SportsApiWriteError` on inner `success=False` (the silent-failure trap).
- `SportsApi.table(name, where=, order_by=, desc=, limit=, offset=)` — the
  generic `GET /api/v1/table/{name}` full-extraction surface with offset
  pagination (what apps were hand-rolling; the `tool("query_table")` path is
  the 20-row preview).
- `aspire_data.motherduck.duckdb_conn()` recreated — was advertised in
  `__init__` docstring + the `[duckdb]` extra but the module was lost in the
  history rewrite. Lazy duckdb import; scratch-only / no-PII policy in docstring.

### Changed — connection reuse + caching (perf)

- New private `aspire_data/_common.py`: single home for `_base`/`_verify`/`_num`
  (previously copy-pasted across 5 modules) + a shared, cached `httpx.Client`
  for the Sports API. One athlete-card render used to cost 4 TLS handshakes.
  (`supplements` keeps its own default-0.0 `_num` — its sums rely on it.)
- `connect.py` convenience wrappers (`hana_sql`, `jobs_get`, `notify_send`, …)
  now reuse a cached `ConnectClient` per GUID instead of opening + leaking a
  fresh TLS connection per call (worst in `jobs_wait` poll loops).
- `identifiers.resolve_ids` — 10-min TTL cache on the hottest path in the
  package (called per athlete card by `whoop_summary`/`firstbeat_summary`).
- `supplements` reads (`fetch_products/receipts/assignments`) — 60s TTL cache;
  `assign()` bypasses the cache for the over-issue guard (`fresh=True`) and
  invalidates after a successful write. Correctness over speed on the write path.
- `aspire_data._common.reset_caches()` — clears cached clients + registered
  TTL caches (tests / long-lived processes).

### Fixed

- `aspire-data status` was lying: render/jobs/notify-api probes were a dead
  stub that printed OK without any HTTP. Now really probe `/health` per GUID.
- Stale `/sports/fip/calendar` examples (route deleted 2026-04-27) updated to
  the live `/fip/calendar` shape in hetzner.py, connect.py, README, tests.
- `hana_sql_subprocess`: documented the hdbsql argv-password exposure and the
  `hdbuserstore -U` migration path (single-user laptop path, unchanged).

## [0.6.0] — 2026-06-10

### Added — `identity` module: DOB-first name → SAMS resolution

`from aspire_data.identity import resolve_to_sams` — map a list of historical
athletes (`{name, dob?, sport?}`) to SAMS player_ids.

- **DOB-first**, not name-score-first. A long historical name ("Mohammed Aly
  Abdelmonem Monsef Noufal") tanks the fuzzy name-score so the top name match is
  often the WRONG person; an exact DOB + a distinctive (surname) token nails the
  right one (Mohamed Noufal). The pool = rapidfuzz blocking **AND** every exact-DOB
  roster athlete; verdict scores DOB + distinctive-token + sport.
- **The `:8080` Sports API match engine is the authoritative scorer** (8-layer
  alias/phonetic/Jaro-Winkler — handles Arabic transliteration variants); rapidfuzz
  is only cheap blocking + offline fallback. Auto-used when `SPORTS_API_URL` is set;
  `use_match_api=False` forces offline. `match_pairs()` exposed as the primitive.
- Verdicts: `auto` (linked) / `review` / `reject` / `no_match`. Common first names
  (Mohammed/Ahmed/Ali…) never auto-link on their own; Jan-1 DOBs treated as placeholders.
- `fetch_roster()` pulls the active roster via `SamsClient` (empty search → full
  roster). Adds `rapidfuzz>=3.0` dep. 9 tests in `tests/test_identity.py`.
- Extracted from the DASH_Anthro anthropometry migration (`ingest/matcher.py`).

## [0.5.0] — 2026-06-04

### Added — supplement inventory client (shared with aspire-supplements)

`aspire_data.supplements` — read an athlete's supplement history and assign
stock against the same Oracle tables the `aspire-supplements` app uses, with
the over-issue guard built in (no re-implementing inventory maths):
- `athlete_history(player_id=…)` — past assignments, newest first, with name
- `products_on_hand()` — catalogue + remaining stock (received − assigned)
- `assign(sams_player_id=…, product_id=…, quantity=…, …)` — writes an
  assignment; raises `OverIssueError` when quantity exceeds on-hand
  (override with `allow_negative=True`). Requires `SPORTS_WRITE_API_KEY`.
- `fetch_products` / `fetch_receipts` / `fetch_assignments` / `on_hand`
Env-only config (`SPORTS_API_URL`, `SPORTS_WRITE_API_KEY`).

## [0.4.0] — 2026-06-04

### Added — athlete identifier resolution + WHOOP / Firstbeat recall

Promoted from the `aspire-nutrition` consultation module. Deterministic
SAMS→device-id mapping (no name matching) + read-only wearable recalls.

**New modules:**
- `aspire_data.identifiers` — `resolve_ids(player_id=…, mrn=…)` returns the
  `athlete_identifiers` row (whoop_id / firstbeat_id / vald_id / gymaware_id /
  …); `device_id(row, field)` pulls one id, treating blank/0 as missing.
- `aspire_data.whoop` — `whoop_summary(player_id=…, mrn=…)`: today's recovery /
  HRV / RHR / strain / sleep + 7/30-day averages + daily series + sleep stages.
  `recovery_zone_color()` helper.
- `aspire_data.firstbeat` — `firstbeat_summary(player_id=…, mrn=…)`: last-week
  sessions / duration / energy (kcal) / TRIMP load / aerobic-TE intensity /
  ACWR + 14-day load series. `acwr_zone_color()` helper.

All env-only config (`SPORTS_API_URL`, `INSECURE_API_TLS`); no hardcoded
hosts/GUIDs. Resolve names (only if unavoidable) via the Sports API AI
resolver, never a local fuzzy match.

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
