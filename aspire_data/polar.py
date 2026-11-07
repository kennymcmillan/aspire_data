"""Polar recall: training sessions + per-second HR, and time in HR zones.

Reads the Sports API `/api/polar/*` routes (Team Pro sessions synced by the VM
into Oracle `polar_sessions` / `polar_samples`). An athlete resolves to a
`polar_id` (= Team Pro player_id) through `athlete_identifiers`, never by name.

Zone time is computed here from the per-second trace, for any zone model:

- lab zones: edges = [LT1 HR, LT2 HR] -> Z1 below LT1, Z2 LT1 to LT2, Z3 at/above LT2
- %Max HR bands: edges = maxhr_edges(max_hr) -> Polar's default 5 sport zones
  (Z1 < 60 %, then 60-70, 70-80, 80-90, Z5 >= 90 % of max HR)

CONFIG (env): SPORTS_API_URL, SPORTS_API_KEY (the polar routes are key-gated).

USAGE
    from aspire_data.polar import polar_sessions, session_trace, zone_minutes
    s = polar_sessions(player_id=2930, from_date="2026-11-01", to_date="2026-11-07")
    tr = session_trace(s["sessions"][0]["player_session_id"])
    z1, z2, z3 = zone_minutes(tr["t_s"], tr["hr"], [171, 186])
"""
from __future__ import annotations

__all__ = ["polar_sessions", "squad_sessions", "session_trace", "zone_minutes",
           "maxhr_edges", "MAXHR_BAND_PCTS", "PolarError"]

from aspire_data import _common
from aspire_data.identifiers import device_id, resolve_ids

# Polar default sport zones: lower edges of Z2..Z5 as a fraction of max HR.
MAXHR_BAND_PCTS = (0.60, 0.70, 0.80, 0.90)
# A gap longer than this between samples is a pause or dropout, not effort.
MAX_SAMPLE_GAP_S = 5.0
# Below this the strap is off or the reading is noise (Polar sends 0 and teens).
MIN_VALID_HR = 30


class PolarError(Exception):
    pass


def _get(path: str, params: dict | None = None) -> dict:
    try:
        r = _common.get(path, params=params, timeout=30.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise PolarError(str(e)) from e


def squad_sessions(from_date: str, to_date: str, *, limit: int = 2000) -> list[dict]:
    """Every synced athlete's session summaries in [from_date, to_date], newest first.
    Each row carries `player_id` (the polar_id); map it to your roster through
    athlete_identifiers.polar_id."""
    return _get("/api/polar/sessions", {"from_date": from_date, "to_date": to_date,
                                        "limit": limit}).get("sessions") or []


def polar_sessions(*, player_id=None, mrn=None, from_date: str | None = None,
                   to_date: str | None = None, limit: int = 500) -> dict:
    """One athlete's session summaries. {"matched": False} when the athlete has no
    polar_id link (fetch nothing: a blank id means nobody is linked)."""
    row = resolve_ids(player_id=player_id, mrn=mrn)
    pid = device_id(row, "polar_id")
    if pid is None:
        return {"matched": False}
    params = {"player_id": str(pid), "limit": limit}
    if from_date:
        params["from_date"] = from_date
    if to_date:
        params["to_date"] = to_date
    return {"matched": True, "polar_id": str(pid),
            "sessions": _get("/api/polar/sessions", params).get("sessions") or []}


def session_trace(player_session_id: str, *, every: int = 1) -> dict:
    """Session summary + HR trace: {"session", "t_s": [...], "hr": [...]}.
    `every` thins to one sample per N (1..60); 1 = full resolution."""
    d = _get(f"/api/polar/sessions/{player_session_id}",
             {"samples": "true", "every": every})
    smp = d.get("samples") or {}
    fields = smp.get("fields") or []
    it, ih = fields.index("t_s") if "t_s" in fields else 0, \
        fields.index("hr") if "hr" in fields else None
    vals = smp.get("values") or []
    return {"session": d.get("session") or {},
            "t_s": [v[it] for v in vals],
            "hr": [v[ih] if ih is not None else None for v in vals]}


def maxhr_edges(max_hr: float | None, pcts=MAXHR_BAND_PCTS) -> list[float] | None:
    """Zone edges in bpm for the %Max HR band model; None without a max HR."""
    return [max_hr * p for p in pcts] if max_hr else None


def zone_minutes(t_s: list, hr: list, edges: list[float]) -> list[float]:
    """Minutes in each zone (len(edges) + 1 zones) from a trace.

    Each sample counts for the time until the next sample, capped at
    MAX_SAMPLE_GAP_S, so 1 Hz and 2 Hz recordings weigh the same and a pause is
    not effort. Samples with no HR or HR < MIN_VALID_HR count in no zone.
    A value equal to an edge falls in the higher zone (HR at LT2 = Z3)."""
    edges = sorted(edges)
    out = [0.0] * (len(edges) + 1)
    for i in range(len(t_s) - 1):
        h = hr[i]
        if h is None or h < MIN_VALID_HR:
            continue
        dt = min(max(t_s[i + 1] - t_s[i], 0.0), MAX_SAMPLE_GAP_S)
        out[sum(h >= e for e in edges)] += dt
    return [s / 60 for s in out]
