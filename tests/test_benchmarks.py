"""v0.19.0 - aspire_data.benchmarks.percentile_of_mark.

The inverse of standard_bands: given a real mark + age, where does it fall
within the historical Power-of-10 percentile norms? Hermetic - every test
passes a small `norms=` DataFrame so nothing touches the network.
"""
from __future__ import annotations

import pandas as pd

from aspire_data.benchmarks import percentile_of_mark


# One Long Jump band (centre 15.5) and one 800m band, plus extra LJ bands for
# the age-selection test. Columns mirror aspire_data_event_percentiles
# (event, age_bin, p0..p100); we only need a handful of p-columns.
def _norms():
    return pd.DataFrame(
        [
            # Long Jump - higher is better. p0 worst .. p100 best.
            {"event": "Long Jump", "age_bin": "15-16",
             "p0": 4.00, "p25": 5.00, "p50": 5.50, "p75": 6.00, "p100": 7.00},
            {"event": "Long Jump", "age_bin": "13-14",
             "p0": 3.00, "p50": 4.50, "p100": 6.00},
            {"event": "Long Jump", "age_bin": "17-18",
             "p0": 4.50, "p50": 6.00, "p100": 7.50},
            # 800m - lower (faster) is better. p100 holds the fastest time.
            {"event": "800m", "age_bin": "15-16",
             "p0": 140.0, "p25": 128.0, "p50": 120.0, "p75": 112.0, "p100": 100.0},
        ]
    )


# ---------- field event (higher is better) ----------

def test_mark_at_p50_returns_50():
    assert percentile_of_mark("Long Jump", 5.50, age=15.5, norms=_norms()) == 50.0


def test_mark_between_bands_interpolates():
    # 5.75 sits halfway between p50 (5.50) and p75 (6.00) -> 62.5
    assert percentile_of_mark("Long Jump", 5.75, age=15.5, norms=_norms()) == 62.5


def test_field_mark_better_than_best_clamps_to_100():
    assert percentile_of_mark("Long Jump", 8.00, age=15.5, norms=_norms()) == 100.0


def test_field_mark_worse_than_worst_clamps_to_0():
    assert percentile_of_mark("Long Jump", 3.00, age=15.5, norms=_norms()) == 0.0


# ---------- track event (lower is better) ----------

def test_track_median_returns_50():
    assert percentile_of_mark("800m", 120.0, age=15.5, norms=_norms()) == 50.0


def test_faster_time_is_above_median():
    # 116s sits halfway between p50 (120) and p75 (112) -> 62.5 (faster = better)
    assert percentile_of_mark("800m", 116.0, age=15.5, norms=_norms()) == 62.5


def test_track_faster_than_best_clamps_to_100():
    assert percentile_of_mark("800m", 95.0, age=15.5, norms=_norms()) == 100.0


def test_track_slower_than_worst_clamps_to_0():
    assert percentile_of_mark("800m", 145.0, age=15.5, norms=_norms()) == 0.0


# ---------- age-band selection ----------

def test_age_picks_nearest_band():
    norms = _norms()
    # Same 6.00m mark, different age -> different band -> different percentile.
    young = percentile_of_mark("Long Jump", 6.00, age=13.0, norms=norms)
    older = percentile_of_mark("Long Jump", 6.00, age=18.0, norms=norms)
    assert young == 100.0   # 6.00 is the best (p100) in the 13-14 band
    assert older == 50.0    # 6.00 is the median (p50) in the 17-18 band


# ---------- fail-soft ----------

def test_event_with_no_norm_returns_none():
    assert percentile_of_mark("4x400m Relay", 200.0, age=16.0, norms=_norms()) is None


def test_empty_norms_returns_none():
    assert percentile_of_mark("Long Jump", 5.5, age=15.5, norms=pd.DataFrame()) is None


def test_non_numeric_mark_returns_none():
    assert percentile_of_mark("Long Jump", "bad", age=15.5, norms=_norms()) is None


# ============ best_pb_by_ageband + age_band_centre ============

from aspire_data.benchmarks import age_band_centre, best_pb_by_ageband


# Integer-centred bands matching the live aspire_data_event_percentiles table
# ('12.5 - 13.5' -> centre 13). Used for the with_percentile test.
def _int_norms():
    return pd.DataFrame(
        [
            {"event": "Long Jump", "age_bin": "12.5 - 13.5",
             "p0": 4.00, "p50": 5.50, "p100": 7.00},
            {"event": "Long Jump", "age_bin": "13.5 - 14.5",
             "p0": 4.50, "p50": 6.00, "p100": 7.50},
        ]
    )


def _results():
    # dob 2010-01-01; LJ marks. 2023 dates -> band 13, 2024 date -> band 14.
    return [
        {"Start_Date": "2023-01-01", "Event_standard": "Long Jump", "Result_numerical": 5.20},
        {"Start_Date": "2023-04-01", "Event_standard": "Long Jump", "Result_numerical": 5.50},
        {"Start_Date": "2024-01-01", "Event_standard": "Long Jump", "Result_numerical": 6.00},
        {"Start_Date": "2023-06-01", "Event_standard": "200m", "Result_numerical": 28.0},
    ]


def test_age_band_centre_rounds_half_up():
    assert age_band_centre(12.5) == 13.0     # lower edge inclusive
    assert age_band_centre(13.49) == 13.0
    assert age_band_centre(13.5) == 14.0     # next band
    assert age_band_centre(9.0) == 9.0
    assert age_band_centre(None) is None


def test_best_pb_per_band_field_event_takes_max():
    out = best_pb_by_ageband(_results(), "2010-01-01", event="Long Jump")
    bands = {r["age_band"]: r for r in out}
    assert set(bands) == {13.0, 14.0}
    assert bands[13.0]["mark"] == 5.50      # best (max) of 5.20, 5.50
    assert bands[13.0]["n"] == 2            # two results in band 13
    assert bands[14.0]["mark"] == 6.00
    assert bands[14.0]["n"] == 1


def test_event_filter_excludes_other_events():
    # 200m row must not leak into a Long Jump query
    out = best_pb_by_ageband(_results(), "2010-01-01", event="Long Jump")
    assert all(r["mark"] in (5.50, 6.00) for r in out)


def test_track_event_takes_min():
    rows = [
        {"Start_Date": "2023-01-01", "Event_standard": "200m", "Result_numerical": 28.0},
        {"Start_Date": "2023-05-01", "Event_standard": "200m", "Result_numerical": 27.2},
    ]
    out = best_pb_by_ageband(rows, "2010-01-01", event="200m")
    assert len(out) == 1
    assert out[0]["mark"] == 27.2          # fastest (min) wins


def test_with_percentile_attaches_band_percentile():
    out = best_pb_by_ageband(_results(), "2010-01-01", event="Long Jump",
                             with_percentile=True, norms=_int_norms())
    bands = {r["age_band"]: r for r in out}
    # band 13 best 5.50 == p50 of the 12.5-13.5 norm -> 50th; band 14 best 6.00 == p50 -> 50th
    assert bands[13.0]["percentile"] == 50.0
    assert bands[14.0]["percentile"] == 50.0


def test_bad_dob_returns_empty():
    assert best_pb_by_ageband(_results(), None, event="Long Jump") == []
