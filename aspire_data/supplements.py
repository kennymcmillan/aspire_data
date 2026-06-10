"""Supplement inventory client — shared with the `aspire-supplements` app.

Same three Oracle MySQL tables (via the Sports API), so any app can read an
athlete's supplement history and assign stock without re-implementing the
inventory maths or the over-issue guard:

    nutrition_supplement_products      catalogue (one row per product+unit)
    nutrition_supplement_receipts      stock received in
    nutrition_supplement_assignments   stock given out to a SAMS athlete

Current stock is never stored — always derived:
    on_hand(product) = SUM(receipts.quantity) − SUM(assignments.quantity)

CONFIG (env)
    SPORTS_API_URL          required — Sports API base URL
    SPORTS_WRITE_API_KEY    required for assign() (write); reads are open
    INSECURE_API_TLS        optional — "true" on Aspire-laptop fallback

USAGE
    from aspire_data.supplements import athlete_history, products_on_hand, assign
    hist = athlete_history(player_id=2930)
    opts = products_on_hand()                 # catalogue + remaining stock
    assign(sams_player_id=2930, product_id=12, quantity=1,
           athlete_name="…", sport="Fencing", assigned_by="a.popple")
"""
from __future__ import annotations

__all__ = [
    "SupplementError", "OverIssueError", "ASSIGN_REASONS",
    "fetch_products", "fetch_receipts", "fetch_assignments",
    "products_on_hand", "on_hand", "athlete_history", "assign",
]

import os
from datetime import date, datetime
from typing import Any

from cachetools import TTLCache

from aspire_data import _common

PRODUCTS_TABLE = "nutrition_supplement_products"
RECEIPTS_TABLE = "nutrition_supplement_receipts"
ASSIGNMENTS_TABLE = "nutrition_supplement_assignments"

ASSIGN_REASONS = [
    "Daily supplementation", "Post-training recovery", "Pre-training",
    "Competition / travel", "Iron / micronutrient protocol", "Immune support",
    "Rehydration", "Other",
]


class SupplementError(RuntimeError):
    pass


class OverIssueError(SupplementError):
    """Raised when an assignment would take stock below zero."""


# 60s TTL on the three table reads — dropdowns re-render often, stock
# changes rarely. assign() bypasses + invalidates (correctness first).
_read_cache: TTLCache = TTLCache(maxsize=8, ttl=60)
_common.register_cache(_read_cache)


def _post(tool: str, **params: Any) -> dict:
    r = _common.post(f"/api/tools/{tool}", json={"parameters": params},
                     timeout=120.0)
    r.raise_for_status()
    body = r.json()
    inner = body.get("result")
    if isinstance(inner, dict) and inner.get("success") is False:
        raise SupplementError(
            f"{tool}: {inner.get('error') or inner.get('message') or 'failed'}")
    return body


def _query(table: str, where: str = "", limit: int = 100000) -> list[dict]:
    resp = _post("query_table", table_name=table, where_clause=where, limit=limit)
    return ((resp.get("result") or {}).get("data") or {}).get("records") or []


def _sql_literal(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ─── reads ─────────────────────────────────────────────────────────────────
def _cached_query(table: str, *, fresh: bool = False) -> list[dict]:
    if not fresh and table in _read_cache:
        return _read_cache[table]
    rows = _query(table)
    _read_cache[table] = rows
    return rows


def fetch_products(active_only: bool = True, *, fresh: bool = False) -> list[dict]:
    rows = _cached_query(PRODUCTS_TABLE, fresh=fresh)
    return [r for r in rows if _int(r.get("active")) in (1, None)] if active_only else rows


def fetch_receipts(*, fresh: bool = False) -> list[dict]:
    return _cached_query(RECEIPTS_TABLE, fresh=fresh)


def fetch_assignments(*, fresh: bool = False) -> list[dict]:
    return _cached_query(ASSIGNMENTS_TABLE, fresh=fresh)


def on_hand(product_id: int, receipts=None, assignments=None, *,
            fresh: bool = False) -> float:
    """Remaining stock for a product = received − assigned."""
    pid = _int(product_id)
    receipts = receipts if receipts is not None else fetch_receipts(fresh=fresh)
    assignments = assignments if assignments is not None else fetch_assignments(fresh=fresh)
    recv = sum(_num(r.get("quantity")) for r in receipts if _int(r.get("product_id")) == pid)
    asgn = sum(_num(a.get("quantity")) for a in assignments if _int(a.get("product_id")) == pid)
    return recv - asgn


def products_on_hand(active_only: bool = True) -> list[dict]:
    """Catalogue rows annotated with `on_hand` (remaining stock), name+unit
    label, newest stock first. Handy for an assign dropdown."""
    products = fetch_products(active_only=active_only)
    receipts, assignments = fetch_receipts(), fetch_assignments()
    out = []
    for p in products:
        pid = _int(p.get("id"))
        oh = on_hand(pid, receipts, assignments)
        label = " ".join(x for x in [p.get("name"), p.get("brand")] if x)
        if p.get("unit"):
            label += f" ({p['unit']})"
        out.append({**p, "on_hand": oh, "label": label})
    out.sort(key=lambda r: (r.get("on_hand", 0) <= 0, r.get("name") or ""))
    return out


def athlete_history(*, player_id: int) -> list[dict]:
    """An athlete's supplement assignments, newest first, with product name."""
    pid = _int(player_id)
    if pid is None:
        return []
    rows = _query(ASSIGNMENTS_TABLE, where=f"sams_player_id = {pid}")
    pname = {_int(p.get("id")): p.get("name") for p in fetch_products(active_only=False)}
    for r in rows:
        r["product_name"] = pname.get(_int(r.get("product_id")))
    rows.sort(key=lambda r: str(r.get("assigned_at") or ""), reverse=True)
    return rows


# ─── write (over-issue guarded) ─────────────────────────────────────────────
def assign(*, sams_player_id: int, product_id: int, quantity: float,
           athlete_name: str | None = None, sport: str | None = None,
           unit: str | None = None, reason: str | None = None,
           assigned_by: str | None = None, note: str | None = None,
           allow_negative: bool = False) -> dict:
    """Assign supplement stock to an athlete — writes to
    nutrition_supplement_assignments. Blocks over-issue (quantity beyond
    on-hand) unless allow_negative=True. Requires SPORTS_WRITE_API_KEY."""
    if not os.environ.get("SPORTS_WRITE_API_KEY"):
        raise SupplementError("SPORTS_WRITE_API_KEY not set — assignment is read-only.")
    qty = _num(quantity)
    if qty <= 0:
        raise SupplementError("Quantity must be greater than zero.")
    if not allow_negative:
        # fresh=True — the over-issue guard must never trust a cached read
        avail = on_hand(product_id, fresh=True)
        if qty > avail:
            raise OverIssueError(
                f"Only {avail:g} in stock — cannot assign {qty:g}.")

    row = {
        "sams_player_id": _int(sams_player_id), "athlete_name": athlete_name,
        "sport": sport, "product_id": _int(product_id), "quantity": qty,
        "unit": unit, "reason": reason, "assigned_by": assigned_by, "note": note,
    }
    cols = [k for k, v in row.items() if v is not None]
    vals = ", ".join(_sql_literal(row[c]) for c in cols)
    sql = f"INSERT INTO {ASSIGNMENTS_TABLE} ({', '.join(cols)}) VALUES ({vals})"
    out = _post("execute_write_sql", sql=sql,
                api_key=os.environ["SPORTS_WRITE_API_KEY"])
    _read_cache.clear()  # the write changed stock — drop cached reads
    return out
