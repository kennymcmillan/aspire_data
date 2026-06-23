# RESUME: `aspire_data.vald` build (2026-06-23)

Handoff doc written before a laptop reboot. Pick up from "Next steps".

## Goal

Promote the duplicated VALD-from-Oracle read logic into ONE shared, tested module
`aspire_data.vald`, lifted from `endurance-dashboard/data/vald_oracle.py`. Four apps
(development_dashboard, endurance-dashboard, DASH_VALD, vald-vercel) each re-implement
the same `vald_result` pivots today, so this removes the duplication and seeds the next
VALD app. Matches the standing "promote reusable patterns upstream" rule.

## STATUS: module written + tested + version-bumped. NOT wired, NOT committed, NOT released.

- Repo: `C:\Users\Kenneth.Mcmillan\Documents\posit-deploys\aspire_data` (git: `kennymcmillan/aspire_data`, public).
- Tests: **all green**. `py -3.12 -m pytest -q` -> 147 passed (10 new in `tests/test_vald.py`).
- No git commit/push, no release tag, no fleet-bump, no deploy. Those wait for an explicit "ship it" / "deploy" signal (R5 gate).

## Files changed this session

| File | Change |
|---|---|
| `aspire_data/vald.py` | **NEW** module (the scaffold). |
| `tests/test_vald.py` | **NEW** 10 hermetic tests (mock_httpx, pure-Python aggregation, branch_get for vald_summary). |
| `aspire_data/__init__.py` | `__version__` 0.17.0 -> **0.18.0**; added a USAGE docstring line for vald. |
| `setup.py` | `version` 0.17.0 -> **0.18.0**. |
| `CHANGELOG.md` | New `## [0.18.0]` entry at top describing the module. |

(The "keep CHANGELOG + __version__ + setup.py in sync" rule is satisfied. Version is bumped locally but UNRELEASED.)

## API surface of `aspire_data.vald`

Pure-Python aggregation (no pandas, to keep the core dependency-free). Reads Oracle
`vald_*` over the Sports API GET table route `/api/v1/table/{name}` (the same route
whoop/identifiers use). Identity resolves via `athlete_identifiers` (deterministic).

- `vald_summary(player_id=... | mrn=...)` -> SAMS-resolved snapshot. Fail-soft:
  `{matched: False}` (no vald_id) / `{matched: True, has_data: False}` (mapped, no tests).
- `metric_history(vald_id, test_type, metric_name, limb='Trial')` -> ForceDecks per-session best.
- `cmj_history(vald_id, metric='Jump Height (Imp-Mom)')` -> CMJ convenience.
- `rjt_history(vald_id, field='tf_tc')` -> SmartSpeed 10/5 RJT from `vald_smartspeed_result`.
- `acute_chronic(vald_id, metric=..., test_type='CMJ')` -> daily mean + 7d/28d rolling + ACWR.
- `asymmetry_history(vald_id, test_type, metric_name)` -> single-leg L/R `(R-L)/mean*100` via `trial_limb`.
- `squad_metric(vald_ids, test_type, metric_name)` -> one metric across many athletes, ONE query.
- Constants: `CMJ_DEFAULT`, `RJT_FIELDS` (tf_tc/contact/flight/height/rsi -> bare field + agg).

## Facts VERIFIED live this session (so we don't re-derive them)

1. **All four VALD device families ARE in Oracle, backfilled + GHA-current.** Tables:
   `vald_result` (ForceDecks, 5.2M+ rows), `vald_smartspeed` + `vald_smartspeed_result`
   (SmartSpeed, holds the real 10/5 RJT), `vald_forceframe`, `vald_nordbord`, plus
   `vald_test`/`vald_profile`/`vald_group`/`vald_group_member`/`vald_sync`.
   The `tool-vald` skill's old "ForceDecks ONLY" line was STALE; corrected this session.
2. **GHA daily sync** `kennymcmillan/vald-oracle` `scripts/sync.py daily`, cron `0 9,14 * * 0-4`
   (12:00+17:00 Qatar, Sun-Thu), latest green 2026-06-22, loads ALL four devices (SmartSpeed/
   ForceFrame/NordBord wrapped non-fatal).
3. **The GET `/api/v1/table/{name}` route serves `vald_result` + `vald_smartspeed_result`**
   with multi-condition `where`, returns `{data: [...]}`. (So no need for the `query_table` tool path.)
4. **RJT fields are stored BARE** in `vald_smartspeed_result.field` (no `jumpingSummaryFields.`
   prefix): `flightTimeOverContractionTime`, `contactTimeSeconds`, `flightTimeSeconds`,
   `heightMeters`, `rsi`, etc. **Tf/Tc = `flightTimeOverContractionTime`, NOT `rsi`.**
   contact/flight values are already ms despite the "...Seconds" name; height is metres.
5. **Oracle stores `vald_id` UPPER-cased** -> the module uppercases the literal in every WHERE
   and matches case-insensitively in `squad_metric`.

## Doc updates already shipped this session (separate from the module)

- `~/.claude/skills/tool-vald/SKILL.md`: replaced stale "ForceDecks ONLY / not in Oracle" warning
  with a verified note; status -> LIVE/all-device; added schema bullets for the device tables.
- `~/.claude/skills/development_dashboard/SKILL.md`: reworded the "VALD gap" note (no longer a gap;
  Oracle has CMJ + real RJT; app still approximates RJT via ForceDecks HJ -> upgrade available).

## NEXT STEPS (resume here)

1. **(Optional) Live smoke** the module against real Oracle from the laptop (truststore):
   ```python
   import truststore; truststore.inject_into_ssl()
   import os; os.environ["SPORTS_API_URL"] = "https://qatar-sports-analytics.duckdns.org"
   from aspire_data import vald
   print(vald.cmj_history("76EA37CD-D2D2-4D4C-A4B4-7C7F4B840897")[:3])
   print(vald.rjt_history("76EA37CD-D2D2-4D4C-A4B4-7C7F4B840897", field="tf_tc")[:3])
   ```
   (Pick a real vald_id from `vald_profile` if that GUID has no data.)
2. **Wire the consumers** (the payoff): repoint `endurance-dashboard/data/vald_oracle.py` and
   `development_dashboard/lib/vald.py` at `aspire_data.vald`, delete the local copies, keep each
   app's caching/overlay as thin wrappers. development_dashboard could also switch its RJT from the
   ForceDecks-HJ approximation to the true `rjt_history` (SmartSpeed).
3. **Release on signal only:** commit + push aspire_data, then
   `py -3.12 ~/bin/aspire-fleet-bump.py --data-sha <sha> --dash-sha <current>` to re-pin + redeploy
   consumers. Do NOT do this until Kenny says "deploy"/"ship it". Laptop-only (GHA can't reach Connect).

## Re-run checks
```
cd ~/Documents/posit-deploys/aspire_data
py -3.12 -m pytest -q                 # full suite (147)
py -3.12 -m pytest tests/test_vald.py -q
```

## Open question for Kenny
Do you want `aspire_data.vald` to also expose ForceFrame (`vald_forceframe`) and NordBord
(`vald_nordbord`) readers now, or add those later when an app needs them? The scaffold covers
ForceDecks + SmartSpeed (the dev/endurance use cases) only.
