# Changelog

All notable changes to `aspire_data`.

## [0.15.0] - 2026-06-22

### Added - benchmarks module (data layer for the aspire_dash benchmarking chart)

`aspire_data.benchmarks` shapes raw results into the inputs for
`aspire_dash.plots.percentile_age_chart`, so any sport's app wires data into the
chart without hand-rolling age, personal-best, direction, or standard logic
(the "aspire_dash component backed by an aspire_data helper" pattern):
- `benchmark_inputs(results, dob, sex, event)` -> one call returns
  `{marks, reference_lines, lower_is_better, value_format}` ready for the chart.
- `marks_from_results(results, dob, ...)` -> `[{age, mark, pb}]` (decimal age at
  result, running-best PB flags, direction inferred from the event).
- `standard_line(event, sex, pin=...)` -> a qualifying-standard reference line
  resolved from a pinned standards table (e.g. `world_athletics_u20_standards`).
- `event_direction(event)` -> (lower_is_better, value_format) for athletics.

## [0.14.0] — 2026-06-17

### Added — Connect user directory on `ConnectAdminClient`

For org-wide "requested by / assign to" pickers (name + email) so apps stop
hand-rolling a user table:

- `get_current_user()` — the API key owner's record (username, first/last name,
  email, guid, user_role). NB: not the app visitor — inside a deployed Connect
  app the visitor is the `RSTUDIO_USER_NAME` env var.
- `list_users(prefix=None, page_size=500)` — every Connect user, auto-paginated
  (`GET /v1/users`). Each row: `username`, `first_name`, `last_name`, `email`,
  `user_role`, `guid`, `locked`, `confirmed`, ….
- `find_user(username_or_email)` — resolve one user by exact username/email
  (case-insensitive); maps a Connect app's `RSTUDIO_USER_NAME` → {name, email}.

First consumer: Operations Tracker (requestor picker → also auto-fills the
completion-email recipient). Requires a key allowed to enumerate users
(administrator sees all).

## [0.13.0] — 2026-06-15

### Fixed — `SamsClient` training-plan endpoints (were 404)

`list_training_plans` / `get_plan_roster` / `list_sport_roster` used the
`/api/ExternalApps/training-plans` paths, which SAMS returns **404** for, and
keyed plan ids on snake_case `training_plan_id` while the API returns
`trainingPlanId`. Repointed to the live `TrainingPlans/Search` +
`TrainingPlanPlayer/Search` endpoints; `list_sport_roster` now returns the picker
shape (`player_id`, `full_name`, `mrn`, `sport`, `photo_url`) and accepts
`committee_id`. Verified live: `list_sport_roster(1, days_back=14)` → 234 athletes.

### Added — picker/save shapes on `SamsClient`

- `search_athletes(q, *, limit, active_only)` — name/MRN search in the picker shape.
- `athlete_card(player_id)` — full athlete card (player_id, full_name, mrn,
  date_of_birth, age, sex, sport, photo_url) enriched with current enrollment
  (sport/discipline/target_event/coach), via the richer `/details` endpoint.

These let athlete apps drop their hand-copied `data/sams.py` roster layer.

### Added — Sports-API write helpers (promoted from DASH_Vyntus)

- `aspire_data.sports_api.sql_literal(v)` — quote a Python value as a MySQL literal
  for hand-built WHERE/DELETE clauses (None/bool/number/date/str, quotes escaped).
  Every Sports-API write app re-implemented this.
- `aspire_data.ingest.replace_children(api, table, key_col, key_val, rows)` —
  NON-destructive child-row replace (insert new generation, then delete the old by
  `max(id)` watermark) so a failed insert never wipes children. The idempotent
  save-children pattern (CPET breaths/stages, anthro, VALD).

## [0.12.0] — 2026-06-11

### Added — `aspire_data.pinboard`: Posit Connect pins (org CSV sharing)

Publish/read versioned datasets as Connect pins — the org-internal sharing
layer (colleagues read via Python/R/browser with their existing Connect
logins). Whole-file + last-write-wins, so it is a distribution layer over a
real store (Oracle), never an app's primary database.

- `publish_dataframe(df, name, title=..., type="csv")` — write/refresh a pin;
  each call is a new version. Bare names are auto-prefixed with the API key
  owner's Connect username (Connect rejects cross-owner writes — the exact
  username, e.g. `Kenneth.Mcmillan@ASPIRE.QA`, is resolved via
  `/__api__/v1/user` and cached).
- `read_pin(name, version=None)` — latest (or pinned-version) read-back.
- `pin_board()` / `full_pin_name()` for direct `pins` access.
- Optional dep: `pip install aspire_data[pins]`. Named `pinboard` (not
  `pins`) to avoid colliding with both the upstream `pins` package and the
  existing `aspire_data.pins` SHA-pin-drift module.
- First consumer: DASH_Anthro republishes an `anthro_records` pin after every
  Oracle write.

## [0.11.0] — 2026-06-10

### Added — `aspire-data bump-pins`: lib SHA-pin drift report + rewrite

Apps pin `aspire_dash`/`aspire_data` to exact commit SHAs (deploy-what-you-
tested), so a library release does nothing until each pin is bumped — a
redeploy silently rebuilds with the old SHA (bit us 2026-06-10: SamsClient
retry shipped in 0.10.0 but apps rebuilt with 0.8.3). New `aspire_data.pins`
module + CLI subcommand:

- `aspire-data bump-pins` — resolve current main SHAs via `git ls-remote`,
  scan `<base>/*/requirements*.txt`, report ok / drift / branch per pin.
- `aspire-data bump-pins --apply` — rewrite drifted exact-SHA pins to current
  main. Branch refs (`@main`) and current pins are never touched. Then test,
  commit, redeploy each app.
- `--base DIR` overrides the default `~/Documents/posit-deploys` root.

## [0.10.0] — 2026-06-10

### Added — SamsClient 5xx/transport auto-retry

`SamsClient` now retries GETs on 5xx responses and httpx transport errors
with exponential backoff (`retries=3`, `retry_backoff=0.5` → 0.5s/1s/2s),
restoring the urllib3.Retry semantics the per-app requests Sessions provided
before the wiring sweep delegated transport here (sams-attendance-dashboard
was the app that lost it). 4xx never retries; all SAMS traffic is GET so the
retry is idempotent-safe. Both knobs are constructor kwargs.

## [0.9.0] — 2026-06-10

### Added — `aspire_data.ingest`: the generic messy-data ingest lane

Convergence of the two pipelines that independently grew the same machinery
(DASH_Anthro `ingest/` and the Smartabase `historical_data/pipeline.py`).
Domain parsers stay app-side; everything reusable now lives in one module:

- `resolve_names_ladder(...)` — the proven 4-step identity ladder: exact join
  (e.g. `athlete_identifiers.smartabase_name`) > profile-MRN → `sams_mrn` >
  durable human decisions > `identity.resolve_to_sams` (DOB ladder) with
  dob/sport hints. Plus `fetch_identifiers(api)` pager.
- `DecisionLedger` — durable yes/no/drop human decisions CSV; re-runs and new
  tables never re-ask (merges the anthro `decisions.json` and Smartabase
  `manual_matches.csv` patterns).
- `validate_ranges(df, rules)` — per-column physical-range nulling + report;
  `flag_future_dates(df, col)` — the D/M/Y-swap dry-run guard from anthro.
- `sanitize_col` / `infer_sql_type` / `build_ddl` — MySQL-safe columns with the
  65KB-row-cap sizing rules and the literal-`ID`-column reserved-name guard;
  standard identity/meta head (`row_uid` unique, `sams_*`, `match_*`).
- `deterministic_id(*parts)` — uuid5 natural-key ids (idempotent upserts);
  `chunked_upsert(api, table, records, key_columns=...)` — width-aware batches.
- 8 tests in `tests/test_ingest_lane.py` (offline; fake api/roster).

First consumer: `historical_data/pipeline.py` (Smartabase loads). Next:
DASH_Anthro `ingest/` should delegate here the way its matcher already does.

## [0.8.3] — 2026-06-10

### Fixed — identity: same-year DOBs no longer count as strong evidence

- `resolve_to_sams` auto-linked "Saleh Al-Sadi" (born 2004-06-15) to
  "Saeed Salem Salem" (born 2004-11-29): the DOB gap was computed by **year
  subtraction**, so any same-year pair scored gap 0, and the `:8080` engine's
  phonetic over-score (Saleh≈Salem, 90) tipped it over the `ns>=88 & gap==0`
  auto rule. Found during the Smartabase historical-data migration.
- New `dob_relation(a, b)` implements Kenny's three-tier DOB ladder:
  **exact date** > **day/month-swapped** (dd-mm vs mm-dd transposition — same
  date through the other entry convention, now treated as strong evidence and
  surfaced as `dob_swapped` in the candidate dict) > **fractional-year gap**
  (real date arithmetic, so 2004-06-15 vs 2004-11-29 is gap 0.46, not 0).
- Verdict changes: the `ns>=88` auto rule now requires exact/swapped DOB
  (same-year alone falls to `review`); `ns>=92` auto now uses the real ≤1y
  gap; swapped-DOB confidence bonus +14 (exact stays +18).
- Regression tests: the Saleh case must be `review`, a dd-mm/mm-dd transposed
  pair with a shared distinctive surname must auto-link.

## [0.8.2] — 2026-06-10

### Fixed — `firstbeat_summary` was DOA (NameError)

- `aspire_data.firstbeat.firstbeat_summary` called `httpx.get(f"{_base()}…",
  verify=_verify())` but the module imported none of `httpx` / `_base` /
  `_verify` — so it raised `NameError` the instant an id resolved. No test
  covered it, so it shipped broken in 0.4.0. Now routes through `_common.get`
  (the same shared, cached client `whoop.py` uses) and drops a duplicate local
  `_num`. Any consumer that imported it (e.g. a wearable recall panel) was
  getting a hard crash — this is the fix.

### Added — nutrition-recall helpers promoted from aspire-nutrition

- `aspire_data.firstbeat.firstbeat_ee_by_slot(player_id|mrn, start, end)` →
  `{(date_iso, 'AM'|'PM'): kcal}`, mapping measured energy expenditure onto a
  SAMS training-plan grid. Includes the **AM-default `_ampm` fix**: a blank/
  unparseable session start time buckets to **AM** (morning training is the
  norm), not PM — the old PM default dumped a whole day's EE into the PM column.
- `aspire_data.identifiers.all_identifiers()` (whole table, 1 h cache) +
  `identifiers_by_mrn()` ({sams_mrn: row}) — bulk lookups for roster grids /
  MRN-keyed dashboards without a resolve_ids() per athlete.

8 new hermetic tests (`tests/test_wearables_v082.py`) — the regression that
would have caught the firstbeat bug, plus AM/PM parsing + cache behaviour.

## [0.8.1] — 2026-06-10

### Added — raw REST passthrough on SportsApi

- `SportsApi.get(path, **params)` / `SportsApi.post(path, json=...)` — for the
  non-tools Sports API REST surfaces (`/api/fencing/*`, `/api/service/match`,
  `/api/firstbeat/*`, ...). Auth/base-URL/TLS stay library-side; apps stop
  hand-rolling `requests` for these routes. Unblocks the
  DASH_Fencing_Reports_App migration (~30 call sites behind one `_get/_post`
  seam) and gcc-games' `sams_loader` match endpoint.

## [0.8.0] — 2026-06-10

### Added — aspire-kb-api client (5th Connect FastAPI now covered)

- `aspire_data.aspire_kb` — `kb_search()` (POST /retrieve: hybrid/vector/bm25,
  opt-in rewrite / multi_query_n / hyde / rerank), `kb_sources()`, `kb_stats()`,
  `kb_document(doc_id)`. Pure HTTP — embedding + cross-encoder stay server-side,
  no PyTorch in consumer apps. Env: `ASPIRE_KB_API_GUID` (+ shared CONNECT_*).
- `aspire-data status` now probes aspire-kb-api `/health` alongside the other
  four Connect APIs.
- `.env.example` + test stub env gained `ASPIRE_KB_API_GUID`.

### Fixed

- `__init__` docstring: removed stale `NUTRITION_API_URL`/`NUTRITION_API_KEY`
  (no such client exists — leftovers from the nutrition extraction); env list
  now matches `.env.example` exactly, including the per-service Connect GUIDs.

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
