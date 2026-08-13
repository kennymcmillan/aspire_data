"""SportsApi — the parameters/result-data-records quirk."""
from __future__ import annotations

import pytest


def test_sports_api_constructs_with_env(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    assert api.base_url == "https://sports.example.com"


def test_sports_api_url_required(monkeypatch):
    monkeypatch.delenv("SPORTS_API_URL", raising=False)
    from aspire_data.sports_api import SportsApi
    with pytest.raises(RuntimeError, match="SPORTS_API_URL"):
        SportsApi()


def test_tool_wraps_request_in_parameters_envelope(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    api.tool("search_athlete_anywhere", q="van Niekerk", limit=5)
    client = mock_httpx.instances[-1]
    method, path, kwargs = client.calls[0]
    assert method == "POST"
    assert path == "/api/tools/search_athlete_anywhere"
    # CRITICAL: body must be wrapped in 'parameters'
    assert kwargs["json"] == {"parameters": {"q": "van Niekerk", "limit": 5}}


def test_tool_unwraps_result_data_records(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    rows = [{"name": "Alice"}, {"name": "Bob"}]
    mock_httpx.instances[-1].set_response(
        json_body={"ok": True, "result": {"data": {"records": rows}}})
    out = api.tool("any_tool")
    assert out == rows


def test_tool_returns_data_when_no_records_key(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    mock_httpx.instances[-1].set_response(
        json_body={"result": {"data": {"total": 42}}})
    out = api.tool("count_things")
    assert out == {"total": 42}


def test_api_key_header_when_set(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    assert "X-API-Key" in mock_httpx.instances[-1].headers
    assert mock_httpx.instances[-1].headers["X-API-Key"] == "stub-sports-key"


def test_api_no_key_header_when_unset(mock_httpx, monkeypatch):
    monkeypatch.delenv("SPORTS_API_KEY", raising=False)
    from aspire_data.sports_api import SportsApi
    SportsApi()
    assert "X-API-Key" not in mock_httpx.instances[-1].headers


# --------------------------------------------------------------------------
# Subject records — plain REST, not the tool envelope
# --------------------------------------------------------------------------

def test_record_hits_the_rest_path_with_flat_on(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    api.record(10)
    method, path, kwargs = mock_httpx.instances[-1].calls[0]
    assert method == "GET"
    assert path == "/api/records/athlete/10"
    assert kwargs["params"] == {"flat": True, "include_payload": True}


def test_record_can_address_a_team(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    api.record("QAT-epee-men-senior", subject_type="team")
    _, path, _ = mock_httpx.instances[-1].calls[0]
    assert path == "/api/records/team/QAT-epee-men-senior"


def test_none_params_are_dropped_not_sent_as_null(mock_httpx):
    # A literal ?sport=None would be matched against the column as the string
    # "None" and quietly return nothing.
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    api.subjects(q="tamimi")
    _, path, kwargs = mock_httpx.instances[-1].calls[0]
    assert path == "/api/records/subjects"
    assert "sport" not in kwargs["params"]
    assert kwargs["params"]["q"] == "tamimi"


def test_staleness_passes_the_window_through(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    api.staleness(min_days=180, max_days=730)
    _, path, kwargs = mock_httpx.instances[-1].calls[0]
    assert path == "/api/records/staleness"
    assert kwargs["params"]["max_days"] == 730
