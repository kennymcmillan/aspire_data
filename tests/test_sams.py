"""SamsClient — auth headers, search, MRN lookup, cache."""
from __future__ import annotations

import pytest


def test_sams_constructs_with_env(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    assert s.base_url == "https://sams.example.com"
    headers = mock_httpx.instances[-1].headers
    assert headers["ClientId"] == "stub-sams-id"
    assert headers["ClientSecret"] == "stub-sams-secret"


def test_sams_base_url_required(monkeypatch):
    monkeypatch.delenv("SAMS_BASE_URL", raising=False)
    from aspire_data.sams import SamsClient, SamsError
    with pytest.raises(SamsError, match="SAMS_BASE_URL"):
        SamsClient()


def test_sams_search_path(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    mock_httpx.instances[-1].set_response(
        json_body=[{"mrn": "20040861", "playerId": 42, "full_name": "X"}])
    out = s.search("van Niekerk")
    assert len(out) == 1
    assert mock_httpx.instances[-1].calls[0][1] == "/api/ExternalApps/player/search"


def test_get_athlete_by_mrn_exact_match(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    mock_httpx.instances[-1].set_response(
        json_body=[
            {"mrn": "99999999", "playerId": 1, "full_name": "Other"},
            {"mrn": "20040861", "playerId": 42, "full_name": "Target"},
        ])
    # First search returns the 2 rows; then it fetches /player/42
    ctx = s.get_athlete_by_mrn("20040861")
    # The second call (after search) is the context lookup
    paths = [c[1] for c in mock_httpx.instances[-1].calls]
    assert "/api/ExternalApps/player/search" in paths
    assert "/api/ExternalApps/player/42" in paths


def test_get_athlete_by_mrn_returns_none_when_no_exact(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    mock_httpx.instances[-1].set_response(
        json_body=[{"mrn": "99999999", "playerId": 1, "full_name": "Different"}])
    assert s.get_athlete_by_mrn("20040861") is None


def test_mrn_cache_hits(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    s._mrn_cache["pre-cached-mrn"] = {"full_name": "Cached"}
    out = s.get_athlete_by_mrn("pre-cached-mrn")
    assert out == {"full_name": "Cached"}


def test_default_sports_dict_present():
    from aspire_data.sams import DEFAULT_SPORTS
    assert DEFAULT_SPORTS[1] == "Athletics"
    assert "Padel" in DEFAULT_SPORTS.values()


# ---- picker-shape methods (v0.13: Search endpoints) ----

def test_search_athletes_picker_shape(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    mock_httpx.instances[-1].set_response(json_body=[
        {"playerId": 42, "fullName": "Amir Omuash", "mrn": "20040861",
         "sportId": 1, "isActive": True},
        {"playerId": 9, "fullName": "Inactive Guy", "isActive": False},
    ])
    hits = s.search_athletes("a")
    assert len(hits) == 1                       # inactive filtered out
    assert hits[0] == {
        "player_id": 42, "full_name": "Amir Omuash", "arabic_name": None,
        "mrn": "20040861", "sport_id": 1, "sport": "Athletics",
        "photo_url": None, "is_active": True,
    }


def test_list_training_plans_uses_search_endpoint(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    inst = mock_httpx.instances[-1]
    inst.set_response(json_body=[{"trainingPlanId": 100}])
    s.list_training_plans(1, "2026-06-15")
    assert inst.calls[0][1] == "/api/ExternalApps/TrainingPlans/Search"


def test_get_plan_roster_builds_hits(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    inst = mock_httpx.instances[-1]
    inst.set_response(json_body=[
        {"playerId": 7, "fullName": "Runner", "mrn": "M7", "sportId": 1, "isActive": True},
        {"fullName": "No id — dropped"},   # no playerId -> filtered
    ])
    roster = s.get_plan_roster(100)
    assert inst.calls[0][1] == "/api/ExternalApps/TrainingPlanPlayer/Search"
    assert len(roster) == 1
    assert roster[0]["player_id"] == 7 and roster[0]["full_name"] == "Runner"


def test_athlete_card_mapped_shape(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    inst = mock_httpx.instances[-1]
    inst.set_response_sequence(
        {"json_body": {"playerId": 2841, "fullName": "Abdalla Zaytoun",
                       "mrn": "M1", "dateOfBirth": "2013-10-01",
                       "gender": "Male", "sportId": 1, "isActive": True}},
        {"json_body": []},   # enrollment periods (no enrichment)
    )
    card = s.athlete_card(2841)
    assert card["player_id"] == 2841
    assert card["full_name"] == "Abdalla Zaytoun"
    assert card["date_of_birth"] == "2013-10-01"
    assert card["age"] is not None
    assert card["sex"] == "Male"
    assert card["sport"] == "Athletics"
    assert inst.calls[0][1] == "/api/ExternalApps/player/2841/details"


# ---- 5xx / transport retry (urllib3-Retry semantics restored) ----

def test_get_retries_5xx_then_succeeds(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient(retry_backoff=0)
    inst = mock_httpx.instances[-1]
    inst.set_response_sequence(
        {"status_code": 503, "content": b"upstream sad"},
        {"status_code": 502, "content": b"still sad"},
        {"status_code": 200, "json_body": [{"playerId": 1}]},
    )
    out = s.search("x")
    assert out == [{"playerId": 1}]
    assert len(inst.calls) == 3


def test_get_4xx_never_retries(mock_httpx):
    from aspire_data.sams import SamsClient, SamsError
    import pytest
    s = SamsClient(retry_backoff=0)
    inst = mock_httpx.instances[-1]
    inst.set_response_sequence({"status_code": 404, "content": b"nope"})
    with pytest.raises(SamsError, match="404"):
        s._get("/api/ExternalApps/player/0")
    assert len(inst.calls) == 1


def test_get_raises_after_retries_exhausted(mock_httpx):
    from aspire_data.sams import SamsClient, SamsError
    import pytest
    s = SamsClient(retries=2, retry_backoff=0)
    inst = mock_httpx.instances[-1]
    inst.set_response(status_code=500, content=b"perma-broken")
    with pytest.raises(SamsError, match="failed after 3 attempts"):
        s._get("/api/ExternalApps/player/search")
    assert len(inst.calls) == 3


def test_get_retries_transport_errors(mock_httpx):
    import httpx
    from aspire_data.sams import SamsClient
    s = SamsClient(retry_backoff=0)
    inst = mock_httpx.instances[-1]
    real_get, state = inst.get, {"n": 0}

    def flaky_get(path, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("boom")
        return real_get(path, **kw)

    inst.get = flaky_get
    inst.set_response(json_body={"ok": True})
    assert s._get("/api/ExternalApps/player/1") == {"ok": True}
    assert state["n"] == 2
