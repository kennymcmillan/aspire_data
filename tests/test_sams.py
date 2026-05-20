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
