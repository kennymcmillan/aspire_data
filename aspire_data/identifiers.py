"""Athlete identifier resolution — the deterministic SAMS↔device-id map.

The Sports DB `athlete_identifiers` table links a SAMS athlete
(sams_player_id / sams_mrn / sams_name) to every external system id:
whoop_id, firstbeat_id, vald_id, gymaware_id, runscribe_id, etc.

Use this (NOT name matching) to resolve an athlete to a device id. If a
deterministic id is genuinely missing, fill the mapping table — or, only if
name resolution is unavoidable, call the Sports API AI resolver
(`/api/athlete/resolve`), never a local fuzzy match.

Lookups are TTL-cached (10 min) and ride the shared Sports-API client —
this is the hottest path in the package (called once per athlete card
per render by whoop_summary / firstbeat_summary).

CONFIG (env)
    SPORTS_API_URL    https://<your-sports-api-host>
    INSECURE_API_TLS  optional — "true" on Aspire-laptop fallback only

USAGE
    from aspire_data.identifiers import resolve_ids, device_id
    row = resolve_ids(player_id=2930)          # or mrn="1520063"
    whoop_user_id = device_id(row, "whoop_id")
"""
from __future__ import annotations

__all__ = ["resolve_ids", "device_id"]

from typing import Any

from cachetools import TTLCache

from aspire_data import _common

_id_cache: TTLCache = TTLCache(maxsize=2048, ttl=600)
_common.register_cache(_id_cache)


def _one(where: str) -> dict | None:
    if where in _id_cache:
        return _id_cache[where]
    r = _common.get("/api/v1/table/athlete_identifiers",
                    params={"where": where, "limit": 1}, timeout=15.0)
    r.raise_for_status()
    rows = r.json().get("data") or []
    row = rows[0] if rows else None
    _id_cache[where] = row
    return row


def resolve_ids(*, player_id: int | str | None = None,
                mrn: str | int | None = None) -> dict | None:
    """Resolve the athlete_identifiers row by SAMS player_id (preferred) then
    MRN. Returns the full row (all external ids) or None."""
    if player_id not in (None, "", "None"):
        try:
            row = _one(f"sams_player_id = {int(player_id)}")
            if row:
                return row
        except (TypeError, ValueError):
            pass
    if mrn not in (None, "", "None"):
        safe = str(mrn).replace("'", "")
        return _one(f"sams_mrn = '{safe}'")
    return None


def device_id(row: dict | None, field: str) -> Any:
    """Pull a device id from an identifiers row, treating blank/0 as missing."""
    if not row:
        return None
    v = row.get(field)
    if v in (None, "", "0", 0, "None"):
        return None
    return v
