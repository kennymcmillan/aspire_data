"""Firstbeat recall — read-only training-load summary for an athlete.

Resolves the athlete to a `firstbeat_id` via `athlete_identifiers`
(deterministic — no name matching), then reads recent sessions from the
Sports API `/api/firstbeat/sessions`. Surfaces last-week activity: sessions,
duration, estimated energy expenditure (calories), training load (TRIMP),
intensity (aerobic Training Effect), and ACWR.

CONFIG (env): SPORTS_API_URL, INSECURE_API_TLS (optional)

USAGE
    from aspire_data.firstbeat import firstbeat_summary
    s = firstbeat_summary(player_id=2930)   # or mrn="1520063"
"""
from __future__ import annotations

__all__ = ["firstbeat_summary", "acwr_zone_color", "FirstbeatError"]

from datetime import date, datetime, timedelta

from aspire_data import _common
from aspire_data._common import _num
from aspire_data.identifiers import device_id, resolve_ids


class FirstbeatError(Exception):
    pass


def acwr_zone_color(acwr: float | None) -> str:
    if acwr is None:
        return "#94a3b8"
    if 0.8 <= acwr <= 1.3:
        return "#16a34a"
    if acwr > 1.5 or acwr < 0.5:
        return "#dc2626"
    return "#fbb800"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pdate(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def firstbeat_summary(*, player_id=None, mrn=None, days: int = 28,
                      today: date | None = None) -> dict:
    """Recall Firstbeat training load. `today` overrides the window anchor
    (handy for tests / reproducible reports)."""
    row = resolve_ids(player_id=player_id, mrn=mrn)
    fid = device_id(row, "firstbeat_id")
    if fid is None:
        return {"matched": False}
    name = (row.get("firstbeat_name") or row.get("sams_name")) if row else None

    to_d = today or date.today()
    fr_d = to_d - timedelta(days=days)
    try:
        r = httpx.get(f"{_base()}/api/firstbeat/sessions",
                      params={"athlete_id": str(fid), "from_date": fr_d.isoformat(),
                              "to_date": to_d.isoformat(), "limit": 200},
                      timeout=20.0, verify=_verify())
        r.raise_for_status()
        sessions = r.json().get("sessions") or []
    except Exception as e:  # noqa: BLE001
        raise FirstbeatError(str(e)) from e

    if not sessions:
        return {"matched": True, "has_data": False, "firstbeat_id": fid, "name": name}

    rows = []
    for s in sessions:
        d = _pdate(s.get("date"))
        if not d:
            continue
        rows.append({"date": d, "dur": _num(s.get("durationMinutes")) or 0,
                     "trimp": _num(s.get("trimp")) or 0, "kcal": _num(s.get("calories")) or 0,
                     "aeTE": _num(s.get("aerobicTE")), "acwr": _num(s.get("acwr"))})
    rows.sort(key=lambda x: x["date"], reverse=True)

    def _agg(subset):
        aete = [r["aeTE"] for r in subset if r["aeTE"] is not None]
        return {"n": len(subset), "minutes": round(sum(r["dur"] for r in subset)),
                "kcal": round(sum(r["kcal"] for r in subset)),
                "load": round(sum(r["trimp"] for r in subset)),
                "intensity": round(sum(aete) / len(aete), 1) if aete else None}

    d7 = to_d - timedelta(days=7)
    last7 = [r for r in rows if r["date"] >= d7]
    latest_acwr = next((r["acwr"] for r in rows if r["acwr"] is not None), None)
    by_day: dict = {}
    for r in rows:
        by_day[r["date"]] = by_day.get(r["date"], 0) + r["trimp"]
    series = [round(by_day.get(to_d - timedelta(days=i), 0)) for i in range(13, -1, -1)]

    return {"matched": True, "has_data": True, "firstbeat_id": fid, "name": name,
            "last7": _agg(last7), "last28": _agg(rows), "acwr": latest_acwr,
            "load_series": series,
            "latest_date": rows[0]["date"].isoformat() if rows else None}
