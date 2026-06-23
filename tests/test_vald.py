"""v0.18.0 — aspire_data.vald, promoted from endurance-dashboard/data/vald_oracle.py.

Pure-Python VALD-from-Oracle reads (ForceDecks vald_result + SmartSpeed
vald_smartspeed_result). Hermetic: mock_httpx replaces httpx.Client; caches
reset around each test. Reads use GET /api/v1/table/{name} -> {"data": [...]}.
"""
from __future__ import annotations

import pytest

from aspire_data import _common

GUID = "76EA37CD-D2D2-4D4C-A4B4-7C7F4B840897"


@pytest.fixture(autouse=True)
def _fresh_caches():
    _common.reset_caches()
    yield
    _common.reset_caches()


def _data(rows):
    return {"data": rows}


# ---------- _session_best (pure unit) ----------

def test_session_best_takes_max_and_counts_trials():
    from aspire_data.vald import _session_best
    rows = [
        {"recorded_date": "2026-02-01", "value": "40.0"},
        {"recorded_date": "2026-02-01", "value": "42.5"},
        {"recorded_date": "2026-02-08", "value": "41.0"},
        {"recorded_date": "2026-02-08", "value": "bad"},   # dropped (non-numeric)
    ]
    out = _session_best(rows, "recorded_date")
    assert out == [
        {"session_date": "2026-02-01", "value": 42.5, "n": 2},
        {"session_date": "2026-02-08", "value": 41.0, "n": 1},
    ]


def test_session_best_min_for_contact_time():
    from aspire_data.vald import _session_best
    rows = [{"test_date": "2026-02-01", "value": "180"},
            {"test_date": "2026-02-01", "value": "170"}]
    out = _session_best(rows, "test_date", agg="min")
    assert out == [{"session_date": "2026-02-01", "value": 170.0, "n": 2}]


# ---------- metric_history / cmj_history (vald_result) ----------

def test_metric_history_returns_session_best(mock_httpx):
    from aspire_data import vald
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_data([
        {"vald_id": GUID, "recorded_date": "2026-02-01", "value": "40.0"},
        {"vald_id": GUID, "recorded_date": "2026-02-01", "value": "42.5"},
        {"vald_id": GUID, "recorded_date": "2026-02-08", "value": "41.0"},
    ]))
    out = vald.cmj_history(GUID)
    assert [r["value"] for r in out] == [42.5, 41.0]
    # WHERE was built with the upper-cased guid + CMJ filter
    where = cli.calls[-1][2]["params"]["where"]
    assert GUID.upper() in where and "test_type = 'CMJ'" in where


def test_cmj_history_empty_when_no_rows(mock_httpx):
    from aspire_data import vald
    _common.sports_client()
    mock_httpx.instances[-1].set_response(json_body=_data([]))
    assert vald.cmj_history(GUID) == []


# ---------- acute_chronic (rolling) ----------

def test_acute_chronic_computes_rolling_and_acwr(mock_httpx):
    from aspire_data import vald
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_data([
        {"vald_id": GUID, "recorded_date": "2026-02-01", "value": "40"},
        {"vald_id": GUID, "recorded_date": "2026-02-01", "value": "44"},  # daily mean 42
        {"vald_id": GUID, "recorded_date": "2026-02-04", "value": "46"},  # within 7d window
    ]))
    out = vald.acute_chronic(GUID)
    assert out[0]["dailymean"] == 42.0
    # second day: acute = mean(42, 46) = 44 over the 7-day window
    assert out[1]["acute"] == 44.0
    assert out[1]["acwr"] == 1.0   # acute == chronic when all points inside both windows


# ---------- asymmetry_history (trial_limb) ----------

def test_asymmetry_history_pivots_left_right(mock_httpx):
    from aspire_data import vald
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_data([
        {"vald_id": GUID, "recorded_date": "2026-03-01", "trial_limb": "Left",  "value": "900"},
        {"vald_id": GUID, "recorded_date": "2026-03-01", "trial_limb": "Right", "value": "1100"},
        {"vald_id": GUID, "recorded_date": "2026-03-08", "trial_limb": "Left",  "value": "1000"},
        # 2026-03-08 has no Right -> excluded
    ]))
    out = vald.asymmetry_history(GUID, "SLISOT", "Peak Vertical Force")
    assert len(out) == 1
    assert out[0]["left"] == 900.0 and out[0]["right"] == 1100.0
    assert out[0]["asym_pct"] == 20.0   # (1100-900)/1000*100


# ---------- squad_metric (multi-athlete, case-insensitive ids) ----------

def test_squad_metric_groups_by_athlete_case_insensitive(mock_httpx):
    from aspire_data import vald
    g2 = "11111111-2222-3333-4444-555555555555"
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_data([
        {"vald_id": GUID,       "recorded_date": "2026-02-01", "value": "40"},
        {"vald_id": g2.upper(), "recorded_date": "2026-02-02", "value": "50"},
    ]))
    # caller passes one lower-cased id to prove the upper-normalised match
    out = vald.squad_metric([GUID.lower(), g2], "CMJ", "Jump Height (Imp-Mom)")
    assert set(out) == {GUID.lower(), g2}
    assert out[GUID.lower()][0]["value"] == 40.0
    assert out[g2][0]["value"] == 50.0


# ---------- rjt_history (vald_smartspeed_result) ----------

def test_rjt_history_reads_smartspeed(mock_httpx):
    from aspire_data import vald
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_data([
        {"vald_id": GUID, "test_date": "2026-04-01", "field": "flightTimeOverContractionTime", "value": "2.6"},
        {"vald_id": GUID, "test_date": "2026-04-01", "field": "flightTimeOverContractionTime", "value": "2.9"},
    ]))
    out = vald.rjt_history(GUID, field="tf_tc")
    assert out == [{"session_date": "2026-04-01", "value": 2.9, "n": 2}]
    where = cli.calls[-1][2]["params"]["where"]
    assert "field = 'flightTimeOverContractionTime'" in where
    assert "10/5 Rebound Jump Test" in where


# ---------- vald_summary (SAMS-resolved) ----------

class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return {"data": self._rows}

    def raise_for_status(self):
        return self


def test_vald_summary_matched_with_data(mock_httpx):
    from aspire_data import vald
    _common.sports_client()
    cli = mock_httpx.instances[-1]

    def branch_get(path, **kw):
        if "athlete_identifiers" in path:
            return _Resp([{"sams_player_id": 2930, "vald_id": GUID,
                           "sams_name": "Test Athlete"}])
        if "vald_smartspeed_result" in path:
            return _Resp([{"vald_id": GUID, "test_date": "2026-04-01",
                           "field": "flightTimeOverContractionTime", "value": "2.8"}])
        # vald_result (CMJ reads)
        return _Resp([{"vald_id": GUID, "recorded_date": "2026-02-01", "value": "45.0"}])
    cli.get = branch_get

    s = vald.vald_summary(player_id=2930)
    assert s["matched"] and s["has_data"]
    assert s["vald_id"] == GUID
    assert s["athlete_name"] == "Test Athlete"
    assert s["today"]["cmj_jump_height_cm"] == 45.0
    assert s["today"]["rjt_tf_tc"] == 2.8


def test_vald_summary_unmatched_when_no_vald_id(mock_httpx):
    from aspire_data import vald
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_data([{"sams_player_id": 2930}]))  # no vald_id
    assert vald.vald_summary(player_id=2930) == {"matched": False}
