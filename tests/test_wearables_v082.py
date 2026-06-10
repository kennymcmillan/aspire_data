"""v0.8.2 — firstbeat fix + nutrition-recall helpers promoted from aspire-nutrition.

Covers the bug fix (firstbeat_summary used an unimported `httpx`/`_base`/`_verify`
→ NameError at call time, uncaught because nothing tested it) plus the two
helpers lifted out of the app: firstbeat_ee_by_slot (AM/PM bucketing with the
AM-default fix) and the bulk identifier helpers.

Hermetic — mock_httpx replaces httpx.Client; caches reset around each test.
The single mocked response carries BOTH `data` (identifiers) and `sessions`
(firstbeat) keys, so resolve_ids and the sessions GET each read their own slice.
"""
from __future__ import annotations

from datetime import date

import pytest

from aspire_data import _common


@pytest.fixture(autouse=True)
def _fresh_caches():
    _common.reset_caches()
    yield
    _common.reset_caches()


def _both(fid="5", sessions=None):
    return {"data": [{"sams_player_id": 2930, "firstbeat_id": fid,
                      "sams_name": "Test Athlete"}],
            "sessions": sessions or []}


# ---------- firstbeat_summary: the NameError regression ----------

def test_firstbeat_summary_no_longer_raises_nameerror(mock_httpx):
    """Before the fix this raised NameError(httpx) the moment an id resolved."""
    from aspire_data import firstbeat
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_both(sessions=[
        {"date": "2026-06-08", "durationMinutes": 60, "trimp": 40,
         "calories": 500, "aerobicTE": 3.0, "acwr": 1.1, "startTime": "16:00"},
    ]))
    s = firstbeat.firstbeat_summary(player_id=2930, today=date(2026, 6, 9))
    assert s["matched"] and s["has_data"]
    assert s["last7"]["kcal"] == 500
    assert s["last7"]["load"] == 40


def test_firstbeat_summary_unmatched_when_no_id(mock_httpx):
    from aspire_data import firstbeat
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body={"data": [{"sams_player_id": 2930}]})  # no firstbeat_id
    assert firstbeat.firstbeat_summary(player_id=2930) == {"matched": False}


# ---------- firstbeat_ee_by_slot: AM/PM bucketing + AM default ----------

def test_ee_by_slot_buckets_and_defaults_to_am(mock_httpx):
    from aspire_data import firstbeat
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_both(sessions=[
        {"date": "2026-06-08", "calories": 300, "startTime": "16:00"},  # PM
        {"date": "2026-06-08", "calories": 200},                        # no time → AM
        {"date": "2026-06-08", "calories": 50,  "startTime": "07:30"},  # AM
    ]))
    out = firstbeat.firstbeat_ee_by_slot(player_id=2930,
                                         start="2026-06-01", end="2026-06-09")
    assert out[("2026-06-08", "PM")] == 300
    assert out[("2026-06-08", "AM")] == 250   # 200 (blank→AM) + 50 (07:30)


def test_ee_by_slot_empty_without_window(mock_httpx):
    from aspire_data import firstbeat
    _common.sports_client()
    mock_httpx.instances[-1].set_response(json_body=_both())
    assert firstbeat.firstbeat_ee_by_slot(player_id=2930) == {}


@pytest.mark.parametrize("t,slot", [
    ("16:00", "PM"), ("07:30", "AM"), ("2026-06-08T13:05:00", "PM"),
    ("2026-06-08T09:00", "AM"), ("", "AM"), (None, "AM"), ("garbage", "AM"),
])
def test_ampm_parsing(t, slot):
    from aspire_data.firstbeat import _ampm
    assert _ampm(t) == slot


# ---------- bulk identifier helpers ----------

def test_all_identifiers_cached(mock_httpx):
    from aspire_data import identifiers
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body={"data": [
        {"sams_mrn": "1520063", "sams_player_id": 2930},
        {"sams_mrn": "20025105", "sams_player_id": 2940},
    ]})
    rows = identifiers.all_identifiers()
    assert len(rows) == 2
    n = len(cli.calls)
    identifiers.all_identifiers()          # served from the 1h cache
    assert len(cli.calls) == n


def test_identifiers_by_mrn_maps_and_skips_blank(mock_httpx):
    from aspire_data import identifiers
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body={"data": [
        {"sams_mrn": "1520063", "sams_player_id": 2930},
        {"sams_mrn": "", "sams_player_id": 9},          # blank MRN → skipped
        {"sams_mrn": None, "sams_player_id": 8},        # null MRN → skipped
    ]})
    m = identifiers.identifiers_by_mrn()
    assert set(m) == {"1520063"}
    assert m["1520063"]["sams_player_id"] == 2930


# ---------- whoop_summary (coverage moved up from aspire-nutrition) ----------

def test_whoop_summary_has_data(mock_httpx):
    """One mocked row satisfies resolve_ids AND each _table read (recovery /
    strain / sleep all read their own fields off the same body)."""
    from aspire_data import whoop
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body={"data": [{
        "whoop_id": "77", "sams_player_id": 2930, "whoop_name": "Test",
        "recorded_date": "2026-06-08", "recovery_score": 80,
        "hrv_rmssd_milli": 95, "resting_heart_rate": 48,
        "strain": 12.5, "calories": 2500,
        "total_sleep_mins": 440, "sleep_performance": 90,
        "deep_sleep_mins": 90, "rem_sleep_mins": 110,
        "light_sleep_mins": 230, "awake_mins": 10, "start_time": "2026-06-08T22:00",
    }]})
    s = whoop.whoop_summary(player_id=2930)
    assert s["matched"] and s["has_data"]
    assert s["whoop_user_id"] == 77
    assert s["today"]["recovery"] == 80
    assert s["today"]["strain"] == 12.5
    assert s["today"]["sleep_mins"] == 440
    assert s["sleep_stages"]["deep"] == 90


def test_whoop_summary_unmatched_no_id(mock_httpx):
    from aspire_data import whoop
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body={"data": [{"sams_player_id": 2930}]})  # no whoop_id
    assert whoop.whoop_summary(player_id=2930) == {"matched": False}
