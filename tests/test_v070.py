"""v0.7.0 coverage — shared clients, TTL caches, write guard, motherduck.

All hermetic: mock_httpx replaces httpx.Client; _common caches are reset
around every test (the cache keys include id(httpx.Client) so a fresh
mock class per test also isolates the client cache, but result caches
like identifiers' TTL store data, not clients — they need the reset).
"""
from __future__ import annotations

import pytest

from aspire_data import _common


@pytest.fixture(autouse=True)
def _fresh_caches():
    _common.reset_caches()
    yield
    _common.reset_caches()


# ---------- _common shared client ----------

def test_sports_client_is_cached(mock_httpx):
    c1 = _common.sports_client()
    c2 = _common.sports_client()
    assert c1 is c2
    assert len(mock_httpx.instances) == 1


def test_sports_client_respects_insecure_tls(mock_httpx, monkeypatch):
    c1 = _common.sports_client()
    monkeypatch.setenv("INSECURE_API_TLS", "true")
    c2 = _common.sports_client()
    assert c1 is not c2  # verify flag changed → new client


def test_base_requires_env(monkeypatch):
    monkeypatch.delenv("SPORTS_API_URL", raising=False)
    with pytest.raises(RuntimeError, match="SPORTS_API_URL"):
        _common._base()


# ---------- identifiers TTL cache ----------

def test_resolve_ids_cached(mock_httpx):
    from aspire_data import identifiers
    _common.sports_client()  # materialise the shared client
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body={"data": [{"sams_player_id": 2930,
                                           "whoop_id": "77"}]})
    r1 = identifiers.resolve_ids(player_id=2930)
    r2 = identifiers.resolve_ids(player_id=2930)
    assert r1 == r2
    assert r1["whoop_id"] == "77"
    assert len(cli.calls) == 1  # second hit served from TTL cache


def test_resolve_ids_caches_misses(mock_httpx):
    from aspire_data import identifiers
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body={"data": []})
    assert identifiers.resolve_ids(mrn="999") is None
    assert identifiers.resolve_ids(mrn="999") is None
    assert len(cli.calls) == 1


# ---------- connect.py cached convenience clients ----------

def test_client_for_reuses_connect_client(mock_httpx):
    from aspire_data import connect
    connect._client_cache.clear()
    connect.jobs_get("job-1")
    connect.jobs_get("job-2")
    # one ConnectClient → one underlying httpx.Client
    assert len(mock_httpx.instances) == 1
    assert len(mock_httpx.instances[0].calls) == 2


def test_client_for_missing_guid(monkeypatch):
    from aspire_data import connect
    connect._client_cache.clear()
    monkeypatch.delenv("JOBS_API_GUID", raising=False)
    with pytest.raises(RuntimeError, match="JOBS_API_GUID"):
        connect.jobs_get("x")


# ---------- SportsApi write guard + REST table ----------

def _sports_api(mock_httpx):
    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    return api, mock_httpx.instances[-1]


def test_tool_raw_returns_envelope(mock_httpx):
    api, cli = _sports_api(mock_httpx)
    cli.set_response(json_body={"ok": True,
                                "result": {"success": True, "rows_affected": 3}})
    body = api.tool_raw("execute_write_sql", sql="UPDATE x")
    assert body["result"]["rows_affected"] == 3


def test_tool_write_raises_on_inner_failure(mock_httpx):
    from aspire_data.sports_api import SportsApiWriteError
    api, cli = _sports_api(mock_httpx)
    cli.set_response(json_body={"ok": True,
                                "result": {"success": False,
                                            "error": "duplicate key"}})
    with pytest.raises(SportsApiWriteError, match="duplicate key"):
        api.tool_write("bulk_insert", table_name="t", records=[])


def test_tool_write_ok_returns_inner_result(mock_httpx):
    api, cli = _sports_api(mock_httpx)
    cli.set_response(json_body={"result": {"success": True, "rows_affected": 1}})
    out = api.tool_write("execute_write_sql", sql="INSERT ...")
    assert out["rows_affected"] == 1


def test_table_rest_surface(mock_httpx):
    api, cli = _sports_api(mock_httpx)
    cli.set_response(json_body={"data": [{"id": 1}, {"id": 2}]})
    rows = api.table("Athletics_Combined", where="country='QAT'",
                     order_by="event_date", desc=True, limit=500, offset=1000)
    assert rows == [{"id": 1}, {"id": 2}]
    method, path, kwargs = cli.calls[0]
    assert method == "GET"
    assert path == "/api/v1/table/Athletics_Combined"
    p = kwargs["params"]
    assert p["limit"] == 500 and p["offset"] == 1000 and p["desc"] == "true"


# ---------- supplements caching + write invalidation ----------

def _supp_response(records):
    return {"result": {"success": True, "data": {"records": records}}}


def test_fetch_receipts_cached(mock_httpx):
    from aspire_data import supplements
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_supp_response([{"product_id": 1, "quantity": 5}]))
    r1 = supplements.fetch_receipts()
    r2 = supplements.fetch_receipts()
    assert r1 == r2
    assert len(cli.calls) == 1
    supplements.fetch_receipts(fresh=True)
    assert len(cli.calls) == 2


def test_assign_guard_bypasses_read_cache(mock_httpx, monkeypatch):
    """The over-issue guard must do FRESH reads even with a warm cache.
    (The mock returns the same body for receipts and assignments, so
    on-hand nets to 0 and the guard fires — proving it re-fetched.)"""
    from aspire_data import supplements
    from aspire_data.supplements import OverIssueError
    monkeypatch.setenv("SPORTS_WRITE_API_KEY", "stub-write-key")
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_supp_response([{"product_id": 1, "quantity": 2}]))
    supplements.fetch_receipts()          # warm the cache
    n_before = len(cli.calls)
    with pytest.raises(OverIssueError):
        supplements.assign(sams_player_id=2930, product_id=1, quantity=1,
                           assigned_by="test")
    # fresh receipts + fresh assignments — cache deliberately ignored
    assert len(cli.calls) == n_before + 2


def test_assign_write_invalidates_read_cache(mock_httpx, monkeypatch):
    from aspire_data import supplements
    monkeypatch.setenv("SPORTS_WRITE_API_KEY", "stub-write-key")
    _common.sports_client()
    cli = mock_httpx.instances[-1]
    cli.set_response(json_body=_supp_response([{"product_id": 1, "quantity": 5}]))
    supplements.fetch_receipts()          # warm the cache (1 call)
    n_before = len(cli.calls)
    # allow_negative skips the guard → only the INSERT goes out
    supplements.assign(sams_player_id=2930, product_id=1, quantity=1,
                       assigned_by="test", allow_negative=True)
    assert len(cli.calls) == n_before + 1
    # the write cleared the cache → next read hits the API again
    supplements.fetch_receipts()
    assert len(cli.calls) == n_before + 2


# ---------- motherduck ----------

def test_motherduck_importable_without_duckdb():
    import aspire_data.motherduck as md
    assert callable(md.duckdb_conn)


def test_motherduck_requires_token(monkeypatch):
    from aspire_data.motherduck import duckdb_conn
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="MOTHERDUCK_TOKEN"):
        duckdb_conn()


# ---------- aspire-data status probes ----------

def test_render_probe_really_probes(mock_httpx):
    from aspire_data import __main__ as cli_mod
    ok, msg = cli_mod._probe_render_api()
    assert ok is True
    assert "/health OK" in msg
    # an actual HTTP call happened (the old stub made none)
    assert any(c[1] == "/health" for inst in mock_httpx.instances
               for c in inst.calls)


# ---------- v0.8.1: raw REST passthrough ----------

def test_raw_get_passthrough(mock_httpx):
    api, cli = _sports_api(mock_httpx)
    cli.set_response(json_body={"competitions": [{"id": 9}]})
    out = api.get("/api/fencing/competitions/search", q="GP", year=2026)
    assert out["competitions"][0]["id"] == 9
    method, path, kwargs = cli.calls[0]
    assert (method, path) == ("GET", "/api/fencing/competitions/search")
    assert kwargs["params"] == {"q": "GP", "year": 2026}


def test_raw_post_passthrough(mock_httpx):
    api, cli = _sports_api(mock_httpx)
    cli.set_response(json_body={"matched": True})
    out = api.post("/api/service/match", json={"name": "Noufal"})
    assert out == {"matched": True}
    method, path, kwargs = cli.calls[0]
    assert (method, path) == ("POST", "/api/service/match")
