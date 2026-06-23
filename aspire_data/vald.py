"""VALD recall — Oracle ``vald_*`` reads for any Aspire app (ForceDecks + SmartSpeed).

One tested home for the VALD-from-Oracle read logic that development_dashboard,
endurance-dashboard, DASH_VALD and vald-vercel each re-implement. Promoted from
``endurance-dashboard/data/vald_oracle.py`` (the first full set of fetchers),
generalised, and stripped of app concerns (no app cache, no live-API overlay,
no pandas). Apps add their own caching on top.

WHY ORACLE, NOT THE LIVE API: the ``vald-oracle`` pipeline mirrors VALD cloud
into Oracle MySQL (ForceDecks ``vald_result``, SmartSpeed ``vald_smartspeed(_result)``,
ForceFrame ``vald_forceframe``, NordBord ``vald_nordbord``) on a twice-daily GHA
cron, backfilled to 2018. The live API shares its quota with prod vald-vercel, so
app reads must come from Oracle. See the ``tool-vald`` skill.

DATA SHAPES
    vald_result            long, one row per metric x limb x trial. Date column
                           ``recorded_date`` (DATE string). ``value`` is a string.
                           ``limb`` = result scope ('Trial' bilateral / 'Left' /
                           'Right' / 'Asym'); ``trial_limb`` = which LEG a single-leg
                           trial used ('Left'/'Right', NULL for bilateral).
    vald_smartspeed_result long, one row per ``field``. Date column ``test_date``.
                           The 10/5 Rebound Jump Test lives here. ``field`` is the
                           bare name (e.g. ``flightTimeOverContractionTime``), value
                           a string. Contact/flight values are ALREADY ms despite
                           the '...Seconds' name; height is metres.

AGGREGATION happens here in pure Python (row volumes per athlete/metric are small),
so the package keeps its no-pandas core. Identity resolution is deterministic via
``athlete_identifiers`` (never name matching).

CONFIG (env): SPORTS_API_URL, INSECURE_API_TLS (laptop fallback only)

USAGE
    from aspire_data.vald import vald_summary, cmj_history, rjt_history
    s   = vald_summary(player_id=2930)                  # SAMS-resolved snapshot
    jh  = cmj_history("76EA37CD-...-840897")            # CMJ jump height series
    rjt = rjt_history("76EA37CD-...-840897", field="tf_tc")
"""
from __future__ import annotations

__all__ = [
    "vald_summary",
    "metric_history", "cmj_history", "rjt_history",
    "acute_chronic", "asymmetry_history", "squad_metric",
    "VALDError", "CMJ_DEFAULT", "RJT_FIELDS",
]

import re
from datetime import date, timedelta

from aspire_data import _common
from aspire_data._common import _num
from aspire_data.identifiers import device_id, resolve_ids
from aspire_data.sports_api import sql_literal

_GUID_RE = re.compile(r"^[0-9A-Fa-f-]{8,40}$")

CMJ_DEFAULT = "Jump Height (Imp-Mom)"

# SmartSpeed 10/5 RJT fields, stored bare in vald_smartspeed_result.field.
# Tuple = (raw field name, session-best aggregation). Contact is lower-is-better
# (min); the rest higher-is-better (max). Tf/Tc (the reactive ratio) is
# flightTimeOverContractionTime, NOT the `rsi` field (see tool-vald skill).
RJT_FIELDS = {
    "tf_tc":   ("flightTimeOverContractionTime", "max"),
    "contact": ("contactTimeSeconds", "min"),   # value is ms
    "flight":  ("flightTimeSeconds", "max"),     # value is ms
    "height":  ("heightMeters", "max"),          # metres
    "rsi":     ("rsi", "max"),                   # VALD jump-height/contact variant
}


class VALDError(Exception):
    """A VALD Oracle read failed at the transport layer."""


def _safe_guid(guid) -> str:
    if not isinstance(guid, str) or not _GUID_RE.match(guid):
        raise ValueError(f"unsafe VALD id: {guid!r}")
    return guid


def _q_guid(guid) -> str:
    """Validated, UPPER-cased, SQL-quoted vald_id. Oracle stores vald_id
    upper-cased, so we upper the literal and compare directly (no UPPER() in
    SQL, which the table route may not parse)."""
    return sql_literal(_safe_guid(guid).upper())


def _table(name: str, *, where: str | None = None, limit: int = 20000) -> list[dict]:
    """Read base-table rows via the Sports API GET route. Raises VALDError on
    transport failure; an empty result is a normal []."""
    params: dict = {"limit": limit}
    if where:
        params["where"] = where
    try:
        r = _common.get(f"/api/v1/table/{name}", params=params, timeout=30.0)
        r.raise_for_status()
        return r.json().get("data") or []
    except Exception as e:  # noqa: BLE001
        raise VALDError(str(e)) from e


def _session_best(rows, date_key: str, agg: str = "max") -> list[dict]:
    """Per-session best value + trial count from long rows.
    Returns [{session_date, value, n}] sorted by date ascending."""
    by_date: dict[str, dict] = {}
    for row in rows:
        v = _num(row.get("value"))
        if v is None:
            continue
        d = str(row.get(date_key))[:10]
        cur = by_date.get(d)
        if cur is None:
            by_date[d] = {"value": v, "n": 1}
        else:
            cur["n"] += 1
            if (agg == "min" and v < cur["value"]) or (agg != "min" and v > cur["value"]):
                cur["value"] = v
    return [{"session_date": d, "value": by_date[d]["value"], "n": by_date[d]["n"]}
            for d in sorted(by_date)]


# --- ForceDecks (vald_result) ------------------------------------------------

def metric_history(vald_id, test_type, metric_name, *, limb: str = "Trial",
                   limit: int = 20000) -> list[dict]:
    """Per-session best for one (test_type, metric_name) on ForceDecks
    ``vald_result``. Returns [{session_date, value, n}]."""
    where = (f"vald_id = {_q_guid(vald_id)} "
             f"AND test_type = {sql_literal(test_type)} "
             f"AND metric_name = {sql_literal(metric_name)} "
             f"AND limb = {sql_literal(limb)}")
    return _session_best(_table("vald_result", where=where, limit=limit),
                         "recorded_date")


def cmj_history(vald_id, *, metric: str = CMJ_DEFAULT,
                limit: int = 20000) -> list[dict]:
    """CMJ per-session best for ``metric`` (default Jump Height Imp-Mom)."""
    return metric_history(vald_id, "CMJ", metric, limb="Trial", limit=limit)


def acute_chronic(vald_id, *, metric: str = CMJ_DEFAULT, test_type: str = "CMJ",
                  acute_days: int = 7, chronic_days: int = 28) -> list[dict]:
    """Daily mean + trailing acute (7d) / chronic (28d) rolling means + ACWR for
    one metric. HANA had FORCEDECK_ACUTE_CHRONIC pre-built; Oracle does not, so
    we compute the same shape. Returns
    [{date, dailymean, acute, chronic, acwr}] sorted ascending."""
    where = (f"vald_id = {_q_guid(vald_id)} "
             f"AND test_type = {sql_literal(test_type)} "
             f"AND metric_name = {sql_literal(metric)} AND limb = 'Trial'")
    rows = _table("vald_result", where=where)
    sums: dict[str, list] = {}
    for row in rows:
        v = _num(row.get("value"))
        if v is None:
            continue
        d = str(row.get("recorded_date"))[:10]
        if len(d) == 10:
            sums.setdefault(d, []).append(v)
    try:
        daily = sorted((date.fromisoformat(d), sum(vs) / len(vs))
                       for d, vs in sums.items())
    except ValueError:
        return []
    out = []
    for d, dm in daily:
        a_lo = d - timedelta(days=acute_days - 1)
        c_lo = d - timedelta(days=chronic_days - 1)
        a_vals = [m for (dd, m) in daily if a_lo <= dd <= d]
        c_vals = [m for (dd, m) in daily if c_lo <= dd <= d]
        acute = sum(a_vals) / len(a_vals) if a_vals else None
        chronic = sum(c_vals) / len(c_vals) if c_vals else None
        out.append({
            "date": d.isoformat(),
            "dailymean": round(dm, 3),
            "acute": round(acute, 3) if acute is not None else None,
            "chronic": round(chronic, 3) if chronic is not None else None,
            "acwr": round(acute / chronic, 3) if (acute and chronic) else None,
        })
    return out


def asymmetry_history(vald_id, test_type, metric_name, *,
                      limit: int = 20000) -> list[dict]:
    """Per-session single-leg asymmetry from ``trial_limb``. Single-leg tests
    (SLISOT, SLJ, ...) tag each trial Left/Right. Per session we take the Left
    session-max and Right session-max, then
    asym_pct = (R - L) / mean(L, R) * 100 (positive = right-dominant).
    Returns [{session_date, left, right, asym_pct}] (only sessions with both legs)."""
    where = (f"vald_id = {_q_guid(vald_id)} "
             f"AND test_type = {sql_literal(test_type)} "
             f"AND metric_name = {sql_literal(metric_name)} "
             f"AND trial_limb IN ('Left', 'Right')")
    rows = _table("vald_result", where=where, limit=limit)
    by_date: dict[str, dict] = {}
    for row in rows:
        v = _num(row.get("value"))
        leg = row.get("trial_limb")
        if v is None or leg not in ("Left", "Right"):
            continue
        d = str(row.get("recorded_date"))[:10]
        slot = by_date.setdefault(d, {})
        if leg not in slot or v > slot[leg]:
            slot[leg] = v
    out = []
    for d in sorted(by_date):
        left, right = by_date[d].get("Left"), by_date[d].get("Right")
        if left is None or right is None:
            continue
        mean = (left + right) / 2
        out.append({"session_date": d, "left": left, "right": right,
                    "asym_pct": round((right - left) / mean * 100, 2) if mean else None})
    return out


def squad_metric(vald_ids, test_type, metric_name, *, limb: str = "Trial",
                 limit: int = 200000) -> dict[str, list]:
    """Per-session best for ONE metric across MANY athletes in ONE query.
    Returns {vald_id: [{session_date, value, n}]} keyed by the caller's ids
    (empty list for athletes with no data). Powers squad heatmaps and the
    adaptive-range population pull."""
    guids = list(vald_ids or [])
    if not guids:
        return {}
    upper_to_orig = {_safe_guid(g).upper(): g for g in guids}
    in_list = ", ".join(sql_literal(u) for u in upper_to_orig)
    where = (f"vald_id IN ({in_list}) "
             f"AND test_type = {sql_literal(test_type)} "
             f"AND metric_name = {sql_literal(metric_name)} "
             f"AND limb = {sql_literal(limb)}")
    rows = _table("vald_result", where=where, limit=limit)
    grouped: dict[str, list] = {g: [] for g in guids}
    by_athlete: dict[str, list] = {}
    for row in rows:
        orig = upper_to_orig.get(str(row.get("vald_id")).upper())
        if orig is not None:
            by_athlete.setdefault(orig, []).append(row)
    for orig, arows in by_athlete.items():
        grouped[orig] = _session_best(arows, "recorded_date")
    return grouped


# --- SmartSpeed (vald_smartspeed_result) -------------------------------------

def rjt_history(vald_id, *, field: str = "tf_tc",
                test_name: str = "10/5 Rebound Jump Test",
                limit: int = 20000) -> list[dict]:
    """SmartSpeed 10/5 Rebound Jump Test per-session best from
    ``vald_smartspeed_result``. ``field`` is a key of RJT_FIELDS
    (tf_tc, contact, flight, height, rsi) or a raw SmartSpeed field name.
    Contact takes a MIN session-best, the rest MAX. Returns
    [{session_date, value, n}].

    NOTE: contact/flight values are already ms despite the '...Seconds' name;
    height is metres. Tf/Tc is flightTimeOverContractionTime, NOT `rsi`."""
    raw, agg = RJT_FIELDS.get(field, (field, "max"))
    where = (f"vald_id = {_q_guid(vald_id)} "
             f"AND test_name = {sql_literal(test_name)} "
             f"AND field = {sql_literal(raw)}")
    return _session_best(_table("vald_smartspeed_result", where=where, limit=limit),
                         "test_date", agg=agg)


# --- SAMS-resolved recall (the headline) -------------------------------------

def vald_summary(*, player_id=None, mrn=None, n_recent: int = 10) -> dict:
    """Recall an athlete's VALD picture, resolved via athlete_identifiers.

    Returns ``{matched: False}`` when there is no vald_id mapping;
    ``{matched: True, has_data: False, ...}`` when mapped but no tests found;
    otherwise the latest CMJ + RJT snapshot plus short trailing series."""
    row = resolve_ids(player_id=player_id, mrn=mrn)
    vid = device_id(row, "vald_id")
    if vid is None:
        return {"matched": False}
    try:
        vid = _safe_guid(str(vid))
    except ValueError:
        return {"matched": False}
    name = (row.get("vald_name") or row.get("sams_name")) if row else None

    cmj_jh = cmj_history(vid, metric=CMJ_DEFAULT)
    cmj_pp = cmj_history(vid, metric="Peak Power / BM")
    rjt = rjt_history(vid, field="tf_tc")

    if not cmj_jh and not cmj_pp and not rjt:
        return {"matched": True, "has_data": False,
                "athlete_name": name, "vald_id": vid}

    last = lambda s: (s[-1] if s else None)
    lj, lp, lr = last(cmj_jh), last(cmj_pp), last(rjt)
    return {
        "matched": True, "has_data": True,
        "athlete_name": name, "vald_id": vid,
        "today": {
            "cmj_jump_height_cm": lj["value"] if lj else None,
            "cmj_jump_height_date": lj["session_date"] if lj else None,
            "cmj_peak_power_bm": lp["value"] if lp else None,
            "rjt_tf_tc": lr["value"] if lr else None,
            "rjt_date": lr["session_date"] if lr else None,
        },
        "cmj_series": [{"session_date": s["session_date"], "value": s["value"]}
                       for s in cmj_jh[-n_recent:]],
        "rjt_series": [{"session_date": s["session_date"], "value": s["value"]}
                       for s in rjt[-n_recent:]],
        "n_cmj_sessions": len(cmj_jh),
        "n_rjt_sessions": len(rjt),
    }
