"""Skeletal-age / maturity recall — bone age + maturity status for an athlete.

Reads the Oracle ``aspire_data_skeletal_age`` table (one row per x-ray
assessment) via the Sports API and returns the latest assessment + full
history. The clinically-meaningful values (maturity status, PHV status,
predicted adult height, % adult predicted height reached, GP/FELS/TW bone ages)
are stored on the row already; this client parses the string cells to numbers,
picks the newest assessment, and recomputes maturity status / PHV band as a
fallback only when the stored value is blank.

The table is keyed by SAMS ``sams_id`` (and durable ``mrn`` for athletes not yet
in SAMS). Consumers pass a SAMS ``player_id``; ``mrn`` is an optional fallback.

CONFIG (env): SPORTS_API_URL, INSECURE_API_TLS (optional)

USAGE
    from aspire_data.skeletal import skeletal_summary
    s = skeletal_summary(player_id=2909)
    s["latest"]["maturity_status"], s["latest"]["predicted_adult_height"]
"""
from __future__ import annotations

__all__ = [
    "skeletal_summary", "SkeletalError",
    "maturity_offset", "maturity_status_from_offset", "phv_status_from_pct_aph",
]

import logging

from aspire_data.sports_api import SportsApi

log = logging.getLogger("aspire_data.skeletal")


class SkeletalError(Exception):
    pass


# ── Maturity classification (ported from DASH_Anthro data/skeletal.py) ──────

def phv_status_from_pct_aph(pct: float | None) -> str | None:
    """% of predicted adult height reached -> PHV band. <85 Pre · 85-89
    Approaching · 90-95 Circa · >=96 Post (Aspire historical-load convention)."""
    if pct is None:
        return None
    if pct < 85:
        return "Pre PHV"
    if pct < 90:
        return "Approaching PHV"
    if pct < 96:
        return "Circa PHV"
    return "Post PHV"


def maturity_offset(fels: float | None, current_age: float | None) -> float | None:
    """Aspire's skeletal maturity offset = FELS skeletal age - chronological age.
    FELS is a recognised criterion method; the GP/FELS/TW scales differ so they
    are never averaged. Undefined when FELS is missing."""
    if fels is None or current_age is None:
        return None
    return round(fels - current_age, 2)


def maturity_status_from_offset(offset: float | None) -> str | None:
    """Maturity offset (years) -> Early / Normal / Late, standard +/-1 year band
    (<= -1 Late · -1..+1 Normal · >= +1 Early)."""
    if offset is None:
        return None
    if offset <= -1:
        return "Late"
    if offset >= 1:
        return "Early"
    return "Normal"


# ── Recall ──────────────────────────────────────────────────────────────────

def _num(v) -> float | None:
    """Lenient float — the stored cells are strings, some with +/- or stray
    mojibake around them; strip to the numeric core."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    s = "".join(c for c in str(v) if c in "0123456789.-")
    try:
        return float(s) if s not in ("", "-", ".", "-.") else None
    except ValueError:
        return None


def _assessment(row: dict) -> dict:
    fels = _num(row.get("fels"))
    cha = _num(row.get("current_age"))
    pct_aph = _num(row.get("predicted_adult_height_reached"))

    offset = _num(row.get("maturity_status_interim"))
    if offset is None:
        offset = maturity_offset(fels, cha)
    status = row.get("maturity_status") or maturity_status_from_offset(offset)
    phv = row.get("phv_predicted_height_status") or phv_status_from_pct_aph(pct_aph)

    return {
        "date": row.get("record_date"),
        "current_age": cha,
        "fels": fels,
        "gp": _num(row.get("g_p2")),
        "tw3": _num(row.get("tw3")),
        "tw2": _num(row.get("tw22")),
        "height_cm": _num(row.get("height_cm")),
        "predicted_adult_height": _num(row.get("height_prediction")),
        "pct_aph": pct_aph,
        "maturity_offset": offset,
        "maturity_status": status,
        "phv_status": phv,
        "notes": row.get("notes") or None,
    }


def skeletal_summary(*, player_id=None, mrn=None, limit: int = 200) -> dict:
    """Recall bone-age / maturity for an athlete (keyed by SAMS sams_id; mrn
    fallback). ``{matched: False}`` when there's no assessment, else the latest
    assessment + the full history (newest first)."""
    where = None
    if player_id not in (None, "", "None"):
        try:
            where = f"sams_id = {int(player_id)}"
        except (TypeError, ValueError):
            where = None
    if where is None and mrn not in (None, "", "None"):
        where = "mrn = '%s'" % str(mrn).replace("'", "")
    if where is None:
        return {"matched": False}

    try:
        rows = SportsApi().tool("query_table", table_name="aspire_data_skeletal_age",
                                where_clause=where, limit=limit)
    except Exception as e:  # noqa: BLE001 — fail soft
        log.info("skeletal recall failed for %s: %s", player_id or mrn, e)
        return {"matched": False}

    if not rows:
        return {"matched": False, "has_data": False}

    name = next((r.get("sams_name") or r.get("source_name")
                 for r in rows if r.get("sams_name") or r.get("source_name")), None)
    history = sorted((_assessment(r) for r in rows),
                     key=lambda a: str(a.get("date") or ""), reverse=True)

    return {
        "matched": True, "has_data": True,
        "athlete_name": name,
        "n_assessments": len(history),
        "latest": history[0],
        "history": history,
    }
