# aspire_data

> Connection clients for every backing store Aspire apps talk to.
> **Sibling of `aspire_dash`** — Dash frontend lib + this data layer = a complete
> new-app stack in minutes.

Public-safe by design: hostnames, keys, and credentials come **only** from
env vars. The package contains *patterns* and *helpers* — never secrets.

```bash
pip install git+https://github.com/kennymcmillan/aspire_data.git
cp .env.example .env  # fill in only the endpoints you need
aspire-data status    # verify every configured connection
```

## What it gives you

| Module | What it wraps | Net LOC saved per app |
|---|---|---|
| `aspire_data.connect` | Posit Connect's 4 Aspire APIs (hana, render, jobs, notify) + generic `ConnectClient(guid)` | ~120 |
| `aspire_data.sports_api` | Sports API on Oracle VM — handles the `parameters` wrapper + `result.data.records` unwrap quirks | ~40 |
| `aspire_data.sams` | SAMS picker drill-down + 1h TTL caches, parallel sport-roster fan-out | ~400 |
| `aspire_data.hana` | SAP HANA via Connect (recommended) OR hdbcli OR hdbsql.exe subprocess | ~80 |
| `aspire_data.aiven` | Aiven Postgres + MySQL with proper SSL | ~30 |
| `aspire_data.oracle` | Oracle VM `aiomysql` + `asyncpg` pools — **fixes the autocommit-False snapshot bug** | ~50 |
| `aspire_data.hetzner` | OpenClaw scraper with proxy-preferred routing | ~30 |
| `aspire_data.posit` | Connect admin REST — content metadata, job logs, deploy settings | ~50 |
| `aspire_data` (root) | Auto-loads `.env`, auto-injects `truststore` for the Aspire MITM CA | ~10 |

**Net: a typical new app skips ~600 lines of connection boilerplate.**

## Five-minute new-app data layer

```python
# api_client.py — the entirety of a new app's data layer
import aspire_data            # auto: .env + truststore
from aspire_data.sams        import SamsClient
from aspire_data.sports_api  import SportsApi
from aspire_data.connect     import hana_sql, jobs_submit

sams   = SamsClient()                    # env: SAMS_*
sports = SportsApi()                     # env: SPORTS_API_URL

# Fuzzy athlete lookup
athletes = sams.search("van Niekerk")

# Sport ranking from the unified DB
rankings = sports.tool("padel_rankings", year=2024, gender="men", limit=20)

# Run SQL against SAP HANA — no client lib install, goes via Connect
rows = hana_sql("SELECT TOP 5 ATHLETE_ID FROM SAMS_VIEW")

# Kick off a long-running scrape; auto-notifies Telegram on completion
job_id = jobs_submit(
    "hetzner_proxy",
    {"path": "/sports/fip/calendar"},
    notify_target="telegram:kenny",
)
```

## The MySQL snapshot bug, fixed

`aiomysql` connections pool with `autocommit=False`. After a SELECT they
keep a stale REPEATABLE-READ snapshot. Subsequent reads on the same
connection miss writes committed by other connections in the meantime.

We bake the fix into the pattern:

```python
from aspire_data.oracle import mysql_pool, with_fresh_snapshot

pool = await mysql_pool()
async with pool.acquire() as conn:
    async with with_fresh_snapshot(conn):              # <-- releases stale snapshot
        async with conn.cursor() as cur:
            await cur.execute("SELECT ...")
            rows = await cur.fetchall()
```

## CLI

```bash
aspire-data status        # ping every env-configured endpoint
aspire-data status --json # machine-readable
aspire-data env           # which env vars are set (no values)
```

Example output:

```
  [OK  ] Connect base                       200 (https://posit.aspire.qa)
  [OK  ] hana-api on Connect                /health OK
  [·   ] render-api on Connect              (env var not set — skipping)
  [OK  ] Sports API                         {'ok': True, 'version': '1.2.0'}
  [OK  ] Hetzner scraper (proxy)            {'status': 'ok'}
  [OK  ] SAMS                               auth headers ok
  [·   ] Aiven Postgres (env present)       (env var not set — skipping)
```

## Pairs perfectly with `aspire_dash`

```bash
# Fresh app in 60 seconds
pip install \
  git+https://github.com/kennymcmillan/aspire_dash.git \
  git+https://github.com/kennymcmillan/aspire_data.git
python -m aspire_dash new my_dash
cd my_dash
# Drop the .env.example from aspire_data, merge with the scaffold one
python app.py
```

The scaffolded `api_client.py` already imports `aspire_data` if available.

## Install

```bash
# Pin to main
pip install git+https://github.com/kennymcmillan/aspire_data.git@main

# Optional extras for actual DB drivers
pip install "aspire_data[mysql,postgres,duckdb,hana]"

# Editable for local dev
git clone https://github.com/kennymcmillan/aspire_data.git
cd aspire_data && pip install -e ".[all]"
```

In your app's `requirements.txt`:

```text
aspire_data @ git+https://github.com/kennymcmillan/aspire_data.git@main
```

## Public-safe rules

- **No hardcoded hostnames** — every URL comes from an env var, with
  the canonical public default (e.g. `qatar-sports-analytics.duckdns.org`)
  used only where the URL is already public.
- **No keys / passwords in code** — `setup.py`, `tests/`, examples in
  `README.md` use placeholders only.
- **No secrets in the test suite** — tests stub-out external calls.

If you find a hardcoded internal hostname or key in a commit, file an
issue or PR — it's a bug.

## Versioning

`CHANGELOG.md` has the per-version notes. Same semver-within-0.x
convention as `aspire_dash`: additive minors, breaking changes get a
major bump when we reach 1.0.

## Roadmap

- **0.2**: MotherDuck (DuckDB + PG endpoint), full Hetzner endpoint
  catalogue helpers, Aiven sync drivers.
- **0.3**: Wearables clients (WHOOP, Firstbeat, RunScribe, VALD)
  harvested from the existing standalone apps.
- **0.4**: `aspire-data new <app>` companion to `aspire-dash new` —
  scaffolds a Quarto report or FastAPI service with the data layer
  pre-wired.
- **0.5**: tests/ directory + GitHub Actions test runner.
- **1.0**: API freeze.
