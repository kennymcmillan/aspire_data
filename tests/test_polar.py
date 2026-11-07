"""aspire_data.polar: zone time from a per-second trace + the no-link guard. Hermetic."""
from __future__ import annotations

import pytest

from aspire_data import _common, polar


@pytest.fixture(autouse=True)
def _fresh_caches():
    _common.reset_caches()
    yield
    _common.reset_caches()


def test_lab_zones_split_at_lt1_lt2():
    # 1 Hz, 60 s each at 140 / 175 / 190 bpm; LT1 170, LT2 185
    t = list(range(181))
    hr = [140] * 60 + [175] * 60 + [190] * 61
    z1, z2, z3 = polar.zone_minutes(t, hr, [170, 185])
    assert (z1, z2, z3) == (1.0, 1.0, 1.0)


def test_edge_value_counts_in_higher_zone():
    assert polar.zone_minutes([0, 1], [185, 185], [170, 185]) == [0.0, 0.0, 1 / 60]


def test_2hz_weighs_same_as_1hz_and_gaps_capped():
    half = [i * 0.5 for i in range(121)]                      # 60 s at 2 Hz
    assert polar.zone_minutes(half, [150] * 121, [170, 185])[0] == pytest.approx(1.0)
    # a 10-minute dropout counts as MAX_SAMPLE_GAP_S, not 600 s
    assert polar.zone_minutes([0, 600, 601], [150, 150, 150], [170, 185])[0] == \
        pytest.approx((polar.MAX_SAMPLE_GAP_S + 1) / 60)


def test_strap_off_and_missing_hr_count_nowhere():
    assert sum(polar.zone_minutes([0, 1, 2, 3], [0, None, 12, 150], [170, 185])) == 0.0


def test_maxhr_edges_polar_default():
    assert polar.maxhr_edges(200) == [120, 140, 160, 180]
    assert polar.maxhr_edges(None) is None
    # 5 zones from 4 edges; Z1 holds everything under 60 %
    assert len(polar.zone_minutes([0, 1], [100, 100], polar.maxhr_edges(200))) == 5


def test_no_polar_link_fetches_nothing(monkeypatch):
    monkeypatch.setattr(polar, "resolve_ids", lambda **_: {"sams_player_id": 1, "polar_id": ""})
    called = []
    monkeypatch.setattr(polar, "_get", lambda *a, **k: called.append(a) or {})
    assert polar.polar_sessions(player_id=1) == {"matched": False}
    assert called == []


def test_session_trace_unpacks_fields(monkeypatch):
    monkeypatch.setattr(polar, "_get", lambda *a, **k: {
        "session": {"player_session_id": "x"},
        "samples": {"fields": ["t_s", "distance", "hr"], "values": [[0.0, None, 120], [1.0, None, 121]]}})
    tr = polar.session_trace("x")
    assert tr["t_s"] == [0.0, 1.0] and tr["hr"] == [120, 121]
