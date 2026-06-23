"""v0.17.0 — anthro + skeletal recall clients.

Two layers:
  - the clinical derive math (pure functions, no network): BMI / Durnin-Womersley
    %BF / Heath-Carter somatotype, the FELS maturity offset + Early/Normal/Late
    banding, and the % adult-height -> PHV band. These lock the formulas promoted
    from DASH_Anthro so they can't silently drift.
  - the recall parsing (anthro_summary / skeletal_summary) with SportsApi stubbed
    so no network is touched: latest-session selection, growth series, string-cell
    parsing, stored-status preference, and the matched/has_data empty states.
"""
from __future__ import annotations

import pytest

from aspire_data import anthro, skeletal


# ── pure ISAK math ──────────────────────────────────────────────────────────

def _t(v):
    return {"m1": v}


def test_get_result_median_mean_single():
    assert anthro.get_result({"m1": 10, "m2": 12, "m3": 11}) == 11   # median of 3
    assert anthro.get_result({"m1": 10, "m2": 12}) == 11             # mean of 2
    assert anthro.get_result({"m1": 10}) == 10                       # single
    assert anthro.get_result(None) is None


def test_compute_calculated_bmi_without_skinfolds():
    """Mass + stature give BMI even with no skinfolds; %BF stays None (the Aspire
    model carries no iliac-crest skinfold, which the DW sum-of-4 requires)."""
    meas = {"bodyMass": _t(64.2), "stature": _t(179.5)}
    c = anthro.compute_calculated(meas, "2008-02-09", "2025-10-20")
    assert c["bmi"] == 19.9          # 64.2 / 1.795^2
    assert c["sumOf4"] is None and c["percentBodyFat"] is None


def test_compute_calculated_full_body_fat_chain():
    """All 8 skinfolds + a DOB -> Durnin-Womersley %BF + FFM compute."""
    meas = {"bodyMass": _t(70.0), "stature": _t(180.0)}
    for k in anthro.SF_KEYS_8:
        meas[k] = _t(8.0)            # 8 sites x 8 mm -> sum8 = 64, sum4 = 32
    c = anthro.compute_calculated(meas, "2008-01-01", "2026-01-01")  # age 18
    assert c["sumOf8"] == 64.0 and c["sumOf4"] == 32.0
    assert c["percentBodyFat"] is not None and 0 < c["percentBodyFat"] < 30
    assert c["fatFreeMass"] is not None and c["fatFreeMass"] < 70.0


def test_heath_carter_and_somatotype_string():
    meas = {"bodyMass": _t(70.0), "stature": _t(180.0),
            "sf_triceps": _t(8.0), "sf_subscapular": _t(9.0),
            "sf_supraspinale": _t(7.0), "sf_medialCalf": _t(6.0),
            "b_humerus": _t(7.0), "b_femur": _t(9.5),
            "g_armFlexed": _t(32.0), "g_calf": _t(36.0)}
    hc = anthro.heath_carter(meas)
    assert hc is not None and len(hc) == 3
    s = anthro.somatotype_string(*hc)
    assert s.count("-") == 2 and all(p.replace(".", "").isdigit() for p in s.split("-"))


# ── maturity / PHV classification ────────────────────────────────────────────

def test_maturity_offset_is_fels_minus_age():
    assert skeletal.maturity_offset(13.94, 14.03) == -0.09
    assert skeletal.maturity_offset(None, 14.0) is None


def test_maturity_status_bands():
    assert skeletal.maturity_status_from_offset(-1.0) == "Late"
    assert skeletal.maturity_status_from_offset(-0.5) == "Normal"
    assert skeletal.maturity_status_from_offset(0.9) == "Normal"
    assert skeletal.maturity_status_from_offset(1.0) == "Early"
    assert skeletal.maturity_status_from_offset(None) is None


def test_phv_bands_from_pct_aph():
    assert skeletal.phv_status_from_pct_aph(84.9) == "Pre PHV"
    assert skeletal.phv_status_from_pct_aph(85) == "Approaching PHV"
    assert skeletal.phv_status_from_pct_aph(90) == "Circa PHV"
    assert skeletal.phv_status_from_pct_aph(96) == "Post PHV"
    assert skeletal.phv_status_from_pct_aph(None) is None


# ── recall parsing (SportsApi stubbed) ───────────────────────────────────────

class _StubApi:
    def __init__(self, rows):
        self._rows = rows

    def tool(self, name, **kw):
        return self._rows


def _stub(module, rows, monkeypatch):
    monkeypatch.setattr(module, "SportsApi", lambda *a, **k: _StubApi(rows))


def test_anthro_summary_latest_and_growth(monkeypatch):
    rows = [
        {"player_id": 2909, "athlete_name": "A", "date_of_measure": "2019-04-15",
         "date_of_birth": "2008-04-14", "level": "L1",
         "measurements": '{"bodyMass": {"m1": 39.4}, "stature": {"m1": 156.2}}'},
        {"player_id": 2909, "athlete_name": "A", "date_of_measure": "2024-09-17",
         "date_of_birth": "2008-04-14", "level": "L1",
         "measurements": '{"bodyMass": {"m1": 48.0}, "stature": {"m1": 164.1}}'},
    ]
    _stub(anthro, rows, monkeypatch)
    s = anthro.anthro_summary(player_id=2909)
    assert s["matched"] and s["has_data"] and s["n_sessions"] == 2
    assert s["latest"]["date"] == "2024-09-17"          # newest wins
    assert s["latest"]["bmi"] == 17.8                    # 48 / 1.641^2
    assert len(s["growth"]) == 2
    assert s["growth"][0]["date"] == "2019-04-15"        # oldest first


def test_anthro_summary_empty_and_no_identity(monkeypatch):
    _stub(anthro, [], monkeypatch)
    assert anthro.anthro_summary(player_id=999) == {"matched": True, "has_data": False}
    assert anthro.anthro_summary() == {"matched": False}


def test_skeletal_summary_parses_strings_and_picks_latest(monkeypatch):
    rows = [
        {"sams_id": 2909, "sams_name": "A", "record_date": "2019-01-13",
         "current_age": "12.0", "fels": None, "maturity_status": "Late",
         "predicted_adult_height_reached": "85.0"},
        {"sams_id": 2909, "sams_name": "A", "record_date": "2022-04-27",
         "current_age": "14.03", "fels": "13.94", "g_p2": "13.54", "tw3": "12.77",
         "height_prediction": "166.5", "predicted_adult_height_reached": "90.0",
         "maturity_status": "Normal", "phv_predicted_height_status": "Circa PHV",
         "maturity_status_interim": "-0.09"},
    ]
    _stub(skeletal, rows, monkeypatch)
    s = skeletal.skeletal_summary(player_id=2909)
    assert s["matched"] and s["has_data"] and s["n_assessments"] == 2
    L = s["latest"]
    assert L["date"] == "2022-04-27"                     # newest wins
    assert L["fels"] == 13.94 and L["current_age"] == 14.03   # strings -> floats
    assert L["maturity_status"] == "Normal"              # stored value preferred
    assert L["phv_status"] == "Circa PHV"
    assert L["predicted_adult_height"] == 166.5 and L["pct_aph"] == 90.0


def test_skeletal_summary_falls_back_when_status_blank(monkeypatch):
    """No stored maturity/PHV -> recompute from FELS-ChA + %APH."""
    rows = [{"sams_id": 5, "record_date": "2023-01-01", "current_age": "12.0",
             "fels": "13.5", "predicted_adult_height_reached": "92.0"}]
    _stub(skeletal, rows, monkeypatch)
    L = skeletal.skeletal_summary(player_id=5)["latest"]
    assert L["maturity_offset"] == 1.5 and L["maturity_status"] == "Early"
    assert L["phv_status"] == "Circa PHV"


def test_skeletal_summary_empty(monkeypatch):
    _stub(skeletal, [], monkeypatch)
    assert skeletal.skeletal_summary(player_id=5) == {"matched": False, "has_data": False}
    assert skeletal.skeletal_summary() == {"matched": False}
