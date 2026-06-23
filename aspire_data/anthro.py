"""Anthropometry recall — ISAK measurement summary for an athlete.

Reads the Oracle ``anthro_records`` table (one row per measurement session,
keyed by SAMS ``player_id``) via the Sports API and returns the latest session
snapshot + a stature/body-mass growth series + the maturation block.

The derived block (BMI, sum-of-skinfolds, Durnin-Womersley body density / %BF,
fat-free mass, Heath-Carter somatotype) is computed here so every consumer gets
the same numbers — the same math DASH_Anthro uses, promoted to the shared lib so
the formulas live in exactly one place.

CONFIG (env): SPORTS_API_URL, INSECURE_API_TLS (optional)

USAGE
    from aspire_data.anthro import anthro_summary
    s = anthro_summary(player_id=2909)
    s["latest"]["bmi"], s["latest"]["somatotype"], s["growth"]
"""
from __future__ import annotations

__all__ = [
    "anthro_summary", "AnthroError",
    "compute_calculated", "heath_carter", "somatotype_string", "get_result",
]

import json
import logging
import math
from datetime import date as _date
from typing import Any

from aspire_data.sports_api import SportsApi

log = logging.getLogger("aspire_data.anthro")


class AnthroError(Exception):
    pass


# ── ISAK derivation math (ported verbatim from DASH_Anthro lib/) ────────────

def get_result(triple) -> float | None:
    """Final result for a site from its M1/M2/M3 trials: median of 3, mean of 2,
    else the single value. Mirrors the capture form's getResult."""
    if triple is None:
        return None
    if not isinstance(triple, dict):
        return _f(triple)
    v1, v2, v3 = _f(triple.get("m1")), _f(triple.get("m2")), _f(triple.get("m3"))
    if v1 is not None and v2 is not None and v3 is not None:
        return sorted([v1, v2, v3])[1]
    if v1 is not None and v2 is not None:
        return (v1 + v2) / 2
    return v1


def _f(v) -> float | None:
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def calc_age(dob: str, dom: str) -> int | None:
    """Whole-year age at the measurement date; None on a malformed/implausible
    date (negative or >100 — usually a dob/dom swap or a 2-digit-year typo)."""
    try:
        b = _date.fromisoformat(str(dob))
        d = _date.fromisoformat(str(dom))
    except (ValueError, TypeError):
        return None
    age = d.year - b.year
    if (d.month, d.day) < (b.month, b.day):
        age -= 1
    return age if 0 <= age <= 100 else None


def body_density_dw(sum4: float, age: int) -> float:
    """Durnin-Womersley body density from the sum of 4 skinfolds + age."""
    logS = math.log10(sum4)
    if age < 20:
        return 1.162 - 0.063 * logS
    if age < 30:
        return 1.1631 - 0.0632 * logS
    if age < 40:
        return 1.1422 - 0.0544 * logS
    if age < 50:
        return 1.162 - 0.07 * logS
    return 1.1715 - 0.0779 * logS


SF_KEYS_8 = ["sf_triceps", "sf_subscapular", "sf_biceps", "sf_iliacCrest",
             "sf_supraspinale", "sf_abdominal", "sf_frontThigh", "sf_medialCalf"]
SF_KEYS_4 = ["sf_triceps", "sf_biceps", "sf_subscapular", "sf_iliacCrest"]


def compute_calculated(measurements: dict, dob: str | None = None,
                       dom: str | None = None) -> dict:
    """Derived anthropometry for one session. BMI + skinfold sums are
    age-independent (computed even without a DOB); %BF/FFM/FM need age via the
    Durnin-Womersley chain, so they stay None when the age is unknown."""
    age = calc_age(dob, dom) if (dob and dom) else None

    mass = get_result(measurements.get("bodyMass"))
    stature = get_result(measurements.get("stature"))
    bmi = round(mass / (stature / 100) ** 2, 1) if (mass and stature) else None

    sf = {k: get_result(measurements.get(k)) for k in SF_KEYS_8}
    sum8 = round(sum(sf[k] for k in SF_KEYS_8), 1) if all(sf[k] is not None for k in SF_KEYS_8) else None
    sum4 = round(sum(sf[k] for k in SF_KEYS_4), 1) if all(sf[k] is not None for k in SF_KEYS_4) else None
    density = round(body_density_dw(sum4, age), 4) if (sum4 is not None and age is not None) else None
    pct_bf = round((4.95 / density - 4.5) * 100, 1) if density is not None else None
    ffm = round(mass * (1 - pct_bf / 100), 1) if (mass is not None and pct_bf is not None) else None
    fm = round(mass * (pct_bf / 100), 1) if (mass is not None and pct_bf is not None) else None

    return {"age": age, "bmi": bmi, "sumOf8": sum8, "sumOf4": sum4,
            "bodyDensity": density, "percentBodyFat": pct_bf,
            "fatFreeMass": ffm, "fatMass": fm}


def heath_carter(measurements: dict) -> tuple[float, float, float] | None:
    """Heath-Carter (endo, meso, ecto) somatotype; None if any input missing."""
    tri = get_result(measurements.get("sf_triceps"))
    sub = get_result(measurements.get("sf_subscapular"))
    sup = get_result(measurements.get("sf_supraspinale"))
    cal = get_result(measurements.get("sf_medialCalf"))
    hum = get_result(measurements.get("b_humerus"))
    fem = get_result(measurements.get("b_femur"))
    arm_flex = get_result(measurements.get("g_armFlexed"))
    calf_g = get_result(measurements.get("g_calf"))
    stature = get_result(measurements.get("stature"))
    mass = get_result(measurements.get("bodyMass"))
    if any(v is None for v in [tri, sub, sup, cal, hum, fem, arm_flex, calf_g, stature, mass]):
        return None

    sf_sum3 = (tri + sub + sup) * (170.18 / stature)
    endo = -0.7182 + 0.1451 * sf_sum3 - 0.00068 * sf_sum3 ** 2 + 0.0000014 * sf_sum3 ** 3
    meso = (0.858 * hum + 0.601 * fem + 0.188 * (arm_flex - tri / 10)
            + 0.161 * (calf_g - cal / 10) - 0.131 * stature + 4.5)
    hwr = stature / (mass ** (1 / 3))
    if hwr >= 40.75:
        ecto = 0.732 * hwr - 28.58
    elif hwr >= 38.25:
        ecto = 0.463 * hwr - 17.63
    else:
        ecto = 0.1
    return (max(0.1, endo), max(0.1, meso), max(0.1, ecto))


def somatotype_string(endo: float, meso: float, ecto: float) -> str:
    return f"{endo:.1f}-{meso:.1f}-{ecto:.1f}"


# ── Recall ──────────────────────────────────────────────────────────────────

def _parse_json(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return {}


def _session(row: dict) -> dict:
    """Parse one anthro_records row into a derived session dict."""
    meas = _parse_json(row.get("measurements"))
    dob, dom = row.get("date_of_birth"), row.get("date_of_measure")
    try:
        calc = compute_calculated(meas, dob, dom)
    except Exception:  # noqa: BLE001 — never let a bad row crash the list
        calc = {}
    try:
        hc = heath_carter(meas)
        calc["somatotype"] = somatotype_string(*hc) if hc else None
    except Exception:  # noqa: BLE001
        calc.setdefault("somatotype", None)

    return {
        "date": dom,
        "level": row.get("level"),
        "age": calc.get("age"),
        "body_mass": get_result(meas.get("bodyMass")),
        "stature": get_result(meas.get("stature")),
        "sitting_height": get_result(meas.get("sittingHeight")),
        "arm_span": get_result(meas.get("armSpan")),
        "bmi": calc.get("bmi"),
        "sum8": calc.get("sumOf8"),
        "sum4": calc.get("sumOf4"),
        "percent_bf": calc.get("percentBodyFat"),
        "ffm": calc.get("fatFreeMass"),
        "fat_mass": calc.get("fatMass"),
        "somatotype": calc.get("somatotype"),
        "maturation": _parse_json(row.get("maturation")) or None,
    }


def anthro_summary(*, player_id=None, mrn=None, limit: int = 2000) -> dict:
    """Recall ISAK anthropometry for an athlete (keyed by SAMS player_id; mrn
    fallback). Returns ``{matched: False}`` only when there's no identity to
    query on, ``{matched: True, has_data: False}`` when the athlete has no
    sessions, else the latest snapshot + a growth series, oldest->newest."""
    if player_id not in (None, "", "None"):
        try:
            where = f"player_id = {int(player_id)}"
        except (TypeError, ValueError):
            return {"matched": False}
    elif mrn not in (None, "", "None"):
        where = "mrn = '%s'" % str(mrn).replace("'", "")
    else:
        return {"matched": False}

    try:
        rows = SportsApi().tool("query_table", table_name="anthro_records",
                                where_clause=where, limit=limit)
    except Exception as e:  # noqa: BLE001 — fail soft to an unmatched dict
        log.info("anthro recall failed for %s: %s", player_id or mrn, e)
        return {"matched": False}

    if not rows:
        return {"matched": True, "has_data": False}

    name = next((r.get("athlete_name") for r in rows if r.get("athlete_name")), None)
    sessions = sorted((_session(r) for r in rows),
                      key=lambda s: str(s.get("date") or ""))

    growth = [{"date": s["date"], "stature": s["stature"],
               "body_mass": s["body_mass"], "bmi": s["bmi"]}
              for s in sessions if s["stature"] is not None or s["body_mass"] is not None]

    latest = sessions[-1]
    # The maturation (PHV) block from the most recent session that carries one.
    maturation = next((s["maturation"] for s in reversed(sessions) if s.get("maturation")), None)

    return {
        "matched": True, "has_data": True,
        "athlete_name": name,
        "n_sessions": len(sessions),
        "latest": latest,
        "growth": growth,
        "maturation": maturation,
    }
