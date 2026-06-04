"""WHOOP recall — read-only recovery / sleep / strain summary for an athlete.

Resolves the athlete to a `whoop_user_id` via `athlete_identifiers`
(deterministic — no name matching), then reads `Whoop_Recovery`,
`Whoop_Sleep`, `Whoop_Daily_Strain` from the Sports API generic table
endpoint. Returns today's snapshot + 7/30-day summaries + daily series.

CONFIG (env): SPORTS_API_URL, INSECURE_API_TLS (optional)

USAGE
    from aspire_data.whoop import whoop_summary
    s = whoop_summary(player_id=2930)     # or mrn="1520063"
"""
from __future__ import annotations

__all__ = ["whoop_summary", "recovery_zone_color", "WhoopError"]

import os
from typing import Any

import httpx

from aspire_data.identifiers import device_id, resolve_ids

REC_GREEN, REC_YELLOW, REC_RED = "#16a34a", "#fbb800", "#dc2626"


class WhoopError(Exception):
    pass


def _base() -> str:
    url = os.environ.get("SPORTS_API_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("SPORTS_API_URL not set — set your Sports API base URL.")
    return url


def _verify() -> bool:
    return os.environ.get("INSECURE_API_TLS", "false").lower() not in ("1", "true", "yes")


def recovery_zone_color(score: float | None) -> str:
    if score is None:
        return "#94a3b8"
    if score >= 67:
        return REC_GREEN
    if score >= 34:
        return REC_YELLOW
    return REC_RED


def _table(name: str, *, where: str | None = None, order_by: str | None = None,
           desc: bool = False, limit: int = 100) -> list[dict]:
    params: dict[str, Any] = {"limit": limit}
    if where:
        params["where"] = where
    if order_by:
        params["order_by"] = order_by
        params["desc"] = "true" if desc else "false"
    try:
        r = httpx.get(f"{_base()}/api/v1/table/{name}", params=params,
                      timeout=20.0, verify=_verify())
        r.raise_for_status()
        return r.json().get("data") or []
    except Exception as e:  # noqa: BLE001
        raise WhoopError(str(e)) from e


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def whoop_summary(*, player_id=None, mrn=None, limit_days: int = 30) -> dict:
    """Recall WHOOP data for an athlete (resolved via athlete_identifiers).
    Returns {matched: False} when there's no WHOOP mapping/data."""
    row = resolve_ids(player_id=player_id, mrn=mrn)
    wid = device_id(row, "whoop_id")
    if wid is None:
        return {"matched": False}
    try:
        uid = int(wid)
    except (TypeError, ValueError):
        return {"matched": False}
    name = (row.get("whoop_name") or row.get("sams_name")) if row else None

    rec = _table("Whoop_Recovery", where=f"whoop_user_id = {uid}",
                 order_by="recorded_date", desc=True, limit=limit_days)
    strain = _table("Whoop_Daily_Strain", where=f"whoop_user_id = {uid}",
                    order_by="recorded_date", desc=True, limit=limit_days)
    sleep = _table("Whoop_Sleep", where=f"whoop_user_id = {uid}",
                   order_by="start_time", desc=True, limit=limit_days)

    if not rec and not strain and not sleep:
        return {"matched": True, "has_data": False,
                "athlete_name": name, "whoop_user_id": uid}

    rec_scores = [_num(r.get("recovery_score")) for r in rec]
    strain_vals = [_num(s.get("strain")) for s in strain]
    sleep_mins = [_num(s.get("total_sleep_mins")) for s in sleep]
    sleep_perf = [_num(s.get("sleep_performance")) for s in sleep]

    lr, ls, lsl = (rec[0] if rec else {}), (strain[0] if strain else {}), (sleep[0] if sleep else {})

    def _spark(vals, n=14):
        return [v for v in reversed(vals) if v is not None][-n:]

    return {
        "matched": True, "has_data": True,
        "athlete_name": name, "whoop_user_id": uid,
        "today": {
            "date": lr.get("recorded_date"),
            "recovery": _num(lr.get("recovery_score")),
            "hrv": _num(lr.get("hrv_rmssd_milli")),
            "rhr": lr.get("resting_heart_rate"),
            "strain": _num(ls.get("strain")), "calories": ls.get("calories"),
            "sleep_mins": _num(lsl.get("total_sleep_mins")),
            "sleep_perf": _num(lsl.get("sleep_performance")),
        },
        "sleep_stages": {
            "deep": _num(lsl.get("deep_sleep_mins")),
            "rem": _num(lsl.get("rem_sleep_mins")),
            "light": _num(lsl.get("light_sleep_mins")),
            "awake": _num(lsl.get("awake_mins")),
        },
        "recovery_series": _spark(rec_scores),
        "strain_series": _spark(strain_vals),
        "sleep_series": _spark(sleep_mins),
        "avg7": {"recovery": _avg(rec_scores[:7]), "strain": _avg(strain_vals[:7]),
                 "sleep_mins": _avg(sleep_mins[:7]), "sleep_perf": _avg(sleep_perf[:7])},
        "avg30": {"recovery": _avg(rec_scores), "strain": _avg(strain_vals),
                  "sleep_mins": _avg(sleep_mins)},
        "n_days": len(rec),
    }
