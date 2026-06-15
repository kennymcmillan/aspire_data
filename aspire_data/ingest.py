"""Generic messy-data ingest lane — the convergence of the two pipelines that
independently grew the same machinery (DASH_Anthro ``ingest/`` + the Smartabase
``historical_data/pipeline.py``, 2026-06).

The lane: adapt source to a DataFrame -> ``validate_ranges`` -> resolve athletes
(``resolve_names_ladder``) -> ``build_ddl`` + ``chunked_upsert`` (idempotent on a
deterministic row id). Domain parsers (anthro datasheet shapes, Smartabase
``About`` conventions) stay app-side; everything reusable lives here.

Hard-won rules baked in:
- MySQL 65KB row cap: VARCHARs sized to observed data; empty columns VARCHAR(100).
- ``upsert_records`` is INSERT..ON DUPLICATE: every record needs the SAME key set,
  and partial updates must still carry all NOT NULL columns.
- Reads on newly-created tables go through the ``query_table`` tool — the
  ``/api/v1/table/*`` REST surface only serves allowlisted tables.
- Human decisions (yes/no/override/drop) are DURABLE: record once in a
  :class:`DecisionLedger`, never re-ask. Resolution itself is
  :func:`aspire_data.identity.resolve_to_sams` (DOB ladder: exact >
  day/month-swapped > fractional-year gap).
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

META_COLS = ["row_uid", "event_uuid", "group_uuid", "record_date", "source_name",
             "entered_by", "sams_id", "sams_name", "match_verdict", "match_confidence"]
RESERVED_COLS = set(META_COLS) | {"id", "created_at", "updated_at"}
RESOLVED_VERDICTS = {"exact", "mrn_exact", "auto", "manual_confirmed"}


# ---------------------------------------------------------------- columns / schema

def sanitize_col(name: str, *, used: set[str] | None = None, max_len: int = 60) -> str:
    """MySQL-safe snake_case column name; suffixes ``_x`` on collision with `used`
    (pass RESERVED_COLS | already-mapped to avoid clobbering the PK — SmartSpeed
    CSVs really do have a literal ``ID`` column)."""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip()).strip("_").lower()
    s = re.sub(r"_+", "_", s)
    if not s or s[0].isdigit():
        s = "c_" + s
    s = s[:max_len]
    if used is not None:
        while s in used:
            s += "_x"
        used.add(s)
    return s


def infer_sql_type(series) -> str:
    """DOUBLE when ≥95% of non-empty values parse numeric; else a VARCHAR sized
    to the data (utf8mb4 VARCHAR costs 4 bytes/char against the 65KB row cap);
    fully-empty columns get VARCHAR(100)."""
    import pandas as pd
    vals = series.dropna().astype(str).str.strip()
    vals = vals[vals != ""]
    if len(vals) == 0:
        return "VARCHAR(100)"
    numeric = pd.to_numeric(vals, errors="coerce")
    if numeric.notna().mean() >= 0.95:
        return "DOUBLE"
    maxlen = int(vals.str.len().max())
    if maxlen <= 30:
        return "VARCHAR(40)"
    if maxlen <= 80:
        return "VARCHAR(100)"
    if maxlen <= 250:
        return "VARCHAR(255)"
    return "TEXT"


def build_ddl(table: str, data_cols: dict[str, str]) -> str:
    """CREATE TABLE IF NOT EXISTS with the standard identity/meta head
    (row_uid unique key, sams_id/sams_name/match_* columns) + typed data columns."""
    cols = ",\n  ".join(f"`{c}` {t} NULL" for c, t in data_cols.items())
    return f"""CREATE TABLE IF NOT EXISTS {table} (
  id INT AUTO_INCREMENT PRIMARY KEY,
  row_uid VARCHAR(120) NOT NULL,
  event_uuid VARCHAR(80) NOT NULL,
  group_uuid VARCHAR(80) NULL,
  record_date DATE NULL,
  source_name VARCHAR(255) NULL,
  entered_by VARCHAR(255) NULL,
  sams_id INT NULL,
  sams_name VARCHAR(255) NULL,
  match_verdict VARCHAR(30) NULL,
  match_confidence INT NULL,
  {cols},
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_row (row_uid),
  KEY idx_sams (sams_id),
  KEY idx_date (record_date)
)"""


def deterministic_id(*parts: Any) -> str:
    """Stable uuid5 over the natural key (e.g. athlete_key|date|level) so re-runs
    UPSERT instead of duplicating — the DASH_Anthro pattern."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(str(p) for p in parts)))


# ---------------------------------------------------------------- validation

def validate_ranges(df, rules: dict[str, tuple[float, float]], *, null_out: bool = True):
    """Null (or just report) physically-impossible values per column.

    rules: ``{"height_cm": (100, 230), "weight_kg": (25, 160), ...}``.
    Returns a report DataFrame [column, n_checked, n_out_of_range, examples].
    """
    import pandas as pd
    report = []
    for col, (lo, hi) in rules.items():
        if col not in df.columns:
            continue
        num = pd.to_numeric(df[col], errors="coerce")
        bad = num.notna() & ((num < lo) | (num > hi))
        report.append({"column": col, "n_checked": int(num.notna().sum()),
                       "n_out_of_range": int(bad.sum()),
                       "examples": num[bad].head(5).tolist()})
        if null_out and bad.any():
            df.loc[bad, col] = None
    return pd.DataFrame(report)


def flag_future_dates(df, date_col: str):
    """D/M/Y vs M/D/Y ambiguity shows up as dates in the future — surface them
    at dry-run time (the anthro lesson). Returns the offending rows."""
    import pandas as pd
    d = pd.to_datetime(df[date_col], errors="coerce")
    return df[d > pd.Timestamp.now()]


# ---------------------------------------------------------------- decisions

class DecisionLedger:
    """Durable human yes/no/override/drop decisions, keyed by source name.

    CSV columns: source_name, sams_id, decision (yes|no|drop), decided_on.
    ``yes`` with sams_id = confirmed link (also covers manual overrides);
    ``no`` = confirmed not-linkable; ``drop`` = exclude the rows entirely
    (group-average lines, dummies). Re-runs never re-ask a decided name.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._map: dict[str, tuple[int | None, str]] = {}
        if self.path.exists():
            import pandas as pd
            for _, r in pd.read_csv(self.path).iterrows():
                sid = int(r["sams_id"]) if pd.notna(r.get("sams_id")) else None
                self._map[str(r["source_name"])] = (sid, str(r["decision"]).strip().lower())

    def __contains__(self, name: str) -> bool:
        return name in self._map

    def get(self, name: str) -> tuple[int | None, str] | None:
        """(sams_id, decision) or None if undecided."""
        return self._map.get(name)

    def record(self, name: str, sams_id: int | None, decision: str,
               decided_on: str) -> None:
        """Append one decision and persist. decided_on: YYYY-MM-DD (caller supplies
        the date — keeps the ledger reproducible)."""
        import pandas as pd
        decision = decision.strip().lower()
        assert decision in ("yes", "no", "drop"), decision
        self._map[name] = (sams_id, decision)
        row = pd.DataFrame([{"source_name": name, "sams_id": sams_id,
                             "decision": decision, "decided_on": decided_on}])
        header = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row.to_csv(self.path, mode="a", header=header, index=False)


# ---------------------------------------------------------------- resolution ladder

def resolve_names_ladder(names: list[str], *,
                         exact_map: dict[str, dict] | None = None,
                         mrn_lookup: dict[str, str] | None = None,
                         dob_lookup: dict[str, str] | None = None,
                         sport_lookup: dict[str, str] | None = None,
                         drop_names: set[str] | None = None,
                         ledger: DecisionLedger | None = None,
                         identifiers: list[dict] | None = None,
                         roster: list[dict] | None = None,
                         use_match_api: bool | None = None) -> dict[str, dict]:
    """The proven 4-step ladder (Smartabase migration, 2026-06-10), generalised.

    1. ``exact_map``: source name -> identifiers row (e.g. a pre-built
       ``smartabase_name`` index) — exact join, free.
    2. ``mrn_lookup``: source name -> MRN string, joined to
       ``identifiers[sams_mrn]`` — exact, no fuzzy.
    3. ``ledger``: durable human decisions — never re-ask.
    4. :func:`aspire_data.identity.resolve_to_sams` with dob/sport hints.

    ``identifiers``: athlete_identifiers rows (caller fetches once);
    ``drop_names``: dummy accounts to mark verdict='dummy'.
    Returns {name: {sams_id, sams_name, verdict, confidence, ...candidate evidence}}.
    """
    from .identity import resolve_to_sams

    exact_map = exact_map or {}
    mrn_lookup = mrn_lookup or {}
    dob_lookup = dob_lookup or {}
    sport_lookup = sport_lookup or {}
    drop_names = drop_names or set()
    by_mrn = {str(r["sams_mrn"]).strip(): r for r in (identifiers or [])
              if r.get("sams_mrn")}

    out: dict[str, dict] = {}
    need: list[dict] = []
    for n in names:
        if n in drop_names:
            out[n] = {"sams_id": None, "sams_name": None, "verdict": "dummy", "confidence": 0}
            continue
        ident = exact_map.get(n)
        if ident and ident.get("sams_player_id"):
            out[n] = {"sams_id": int(ident["sams_player_id"]),
                      "sams_name": ident.get("sams_name"),
                      "verdict": "exact", "confidence": 100}
            continue
        mrn = mrn_lookup.get(n)
        ident = by_mrn.get(str(mrn).strip()) if mrn else None
        if ident and ident.get("sams_player_id"):
            out[n] = {"sams_id": int(ident["sams_player_id"]),
                      "sams_name": ident.get("sams_name"),
                      "verdict": "mrn_exact", "confidence": 100}
            continue
        if ledger is not None and n in ledger:
            sid, decision = ledger.get(n)
            if decision == "drop":
                out[n] = {"sams_id": None, "sams_name": None, "verdict": "dummy", "confidence": 0}
            else:
                out[n] = {"sams_id": sid, "sams_name": None,
                          "verdict": "manual_confirmed" if sid else "manual_rejected",
                          "confidence": 100}
            continue
        need.append({"name": n, "key": n,
                     **({"dob": dob_lookup[n]} if dob_lookup.get(n) else {}),
                     **({"sport": sport_lookup[n]} if sport_lookup.get(n) else {})})

    if need:
        for r in resolve_to_sams(need, roster=roster, use_match_api=use_match_api):
            c = r.get("candidate") or {}
            out[r["key"]] = {
                "sams_id": r["player_id"],
                "sams_name": c.get("sams_name") if r["player_id"] else None,
                "verdict": r["verdict"], "confidence": r["confidence"],
                "best_candidate": c.get("sams_name"),
                "candidate_player_id": c.get("player_id"),
                "name_score": c.get("name_score"), "dob_exact": c.get("dob_exact"),
                "dob_swapped": c.get("dob_swapped"),
                "candidate_sport": c.get("sams_sport"),
            }
    return out


def fetch_identifiers(api=None) -> list[dict]:
    """All athlete_identifiers rows (paged). Caller indexes as needed
    (e.g. by smartabase_name for the exact_map, by sams_mrn is built-in)."""
    if api is None:
        from .sports_api import SportsApi
        api = SportsApi()
    rows, offset = [], 0
    while True:
        page = api.table("athlete_identifiers", limit=500, offset=offset)
        rows.extend(page)
        if len(page) < 500:
            break
        offset += 500
    return rows


# ---------------------------------------------------------------- writes

def replace_children(api, table: str, key_col: str, key_val, rows: list[dict], *,
                     api_key: str | None = None) -> int:
    """Replace a parent's child rows NON-destructively: insert the new generation,
    then delete the previous one (by ``max(id)`` watermark). A failed insert raises
    BEFORE any delete, so the existing rows survive — unlike delete-then-insert,
    which wipes children if the re-insert fails. Returns rows inserted.

    For an AUTO_INCREMENT-keyed child table (raw breaths, per-stage rows…) owned by
    a parent ``key_col = key_val`` (e.g. ``test_uuid``). ``api`` is a
    :class:`aspire_data.sports_api.SportsApi`.
    """
    from aspire_data.sports_api import sql_literal
    api_key = api_key or os.environ["SPORTS_WRITE_API_KEY"]
    keyq = sql_literal(key_val)
    existing = api.table(table, where=f"{key_col} = {keyq}",
                         order_by="id", desc=True, limit=1)
    watermark = int(existing[0]["id"]) if existing and existing[0].get("id") is not None else 0
    if rows:
        api.tool_write("bulk_insert", table_name=table, records=rows,
                       on_duplicate="error", api_key=api_key)
    # Only after a successful insert do we remove the previous generation.
    api.tool_write("execute_write_sql",
                   sql=f"DELETE FROM {table} WHERE {key_col} = {keyq} AND id <= {watermark}",
                   api_key=api_key)
    return len(rows)


def chunked_upsert(api, table: str, records: list[dict], *,
                   key_columns: list[str], api_key: str | None = None,
                   target_chunk_cells: int = 20000) -> int:
    """Idempotent upsert via the Sports API write tool, chunk-sized by width
    (wide tables get smaller batches). Returns the number of records sent.
    Every record must carry the same key set incl. all NOT NULL columns."""
    if not records:
        return 0
    api_key = api_key or os.environ["SPORTS_WRITE_API_KEY"]
    ncols = max(1, len(records[0]))
    chunk = max(50, target_chunk_cells // ncols)
    for i in range(0, len(records), chunk):
        api.tool_write("upsert_records", table_name=table,
                       records=records[i:i + chunk],
                       key_columns=key_columns, api_key=api_key)
    return len(records)
