"""athlete_record: section reads by blocks=, parallel load, lazy fill, failures degrade."""
from __future__ import annotations

import threading

import httpx
import pytest

from aspire_data import athlete_record as ar

CORE = {"sport": "athletics", "display_name": "Test Athlete", "built_at": "2026-10-09 22:32:36",
        "staleness_days": 18, "summary": {"text": "Short summary.", "model": "m", "prompt_version": "v5"},
        "payload": {"identity": {"sport": "athletics", "name": "Test Athlete"},
                    "card": {"line": "One line."},
                    "athletics": {"context": {"standing": ["s"]}, "honours": [{"h": 1}], "peers": {"p": 1},
                                  "career": {"career_high": {"rank": 3}}}}}
RESULTS = {"payload": {"athletics": {"results_flat": [{"date": "2025-01-01"}, {"date": "2026-09-23"}]}}}
REPORT = {"sport": "athletics", "composed": {"narrative": "Long narrative.", "claims": [{"c": 1}]}}


class FakeApi:
    def __init__(self, fail=()):
        self.calls, self.fail, self.lock = [], set(fail), threading.Lock()

    def get(self, path, **params):
        with self.lock:
            self.calls.append((path, params.get("blocks")))
        if path.endswith("/report"):
            if "report" in self.fail:
                raise httpx.ConnectError("down")
            return REPORT
        blocks = params["blocks"]
        if blocks in self.fail:
            raise httpx.ConnectError("down")
        return {"sport.results": RESULTS}.get(blocks, CORE)


@pytest.fixture
def api():
    return FakeApi()     # a new client per test = its own cache keys


def test_page_open_reads_core_and_report_only(api):
    rec = ar.athlete_record(api, 123)
    assert sorted(c[1] or "report" for c in api.calls) == sorted([ar.SECTIONS["core"], "report"])
    assert rec.short_summary == "Short summary." and rec.long_summary == "Long narrative."
    assert rec.headline_line == "One line." and rec.name == "Test Athlete"
    assert rec.context == {"standing": ["s"]} and rec.honours == [{"h": 1}] and rec.career["career_high"]["rank"] == 3


def test_results_load_lazily_newest_first(api):
    rec = ar.athlete_record(api, 123)
    n = len(api.calls)
    assert [r["date"] for r in rec.results] == ["2026-09-23", "2025-01-01"]
    assert api.calls[n:] == [("/api/records/athlete/123", "sport.results")]


def test_sections_are_cached_across_records(api):
    ar.athlete_record(api, 7)
    ar.athlete_record(api, 7)
    assert len(api.calls) == 2


def test_failed_section_degrades_and_is_not_cached():
    api = FakeApi(fail={"report"})
    rec = ar.athlete_record(api, 11)
    assert rec.long_summary is None and rec.errors == ["report"] and rec.short_summary == "Short summary."
    api.fail.clear()
    assert ar.athlete_record(api, 11).long_summary == "Long narrative."


def test_headline_line_from_spread_card(api):
    spread = {**CORE, "payload": {**{k: v for k, v in CORE["payload"].items() if k != "card"},
                                  "card_version": 2, "headline": {"line": "Spread line."}}}
    api.get = lambda path, **p: REPORT if path.endswith("/report") else spread
    assert ar.athlete_record(api, 5).headline_line == "Spread line."


def test_unknown_section_raises(api):
    with pytest.raises(ValueError):
        ar.AthleteRecord(api, 1).load(["nope"])
