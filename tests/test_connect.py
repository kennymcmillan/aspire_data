"""ConnectClient + the 4 Aspire-API convenience wrappers."""
from __future__ import annotations

import pytest


def test_connect_client_constructs_with_env(mock_httpx):
    from aspire_data.connect import ConnectClient
    c = ConnectClient(guid="some-guid")
    assert c.guid == "some-guid"
    assert "connect.example.com" in c.content_url
    assert "Authorization" in mock_httpx.instances[-1].headers
    assert mock_httpx.instances[-1].headers["Authorization"].startswith("Key ")


def test_connect_client_explicit_args_override_env(mock_httpx):
    from aspire_data.connect import ConnectClient
    c = ConnectClient(guid="g", key="explicit-key", base_url="https://other.example.com")
    assert "other.example.com" in c.content_url
    assert mock_httpx.instances[-1].headers["Authorization"] == "Key explicit-key"


def test_connect_client_get_post_delete(mock_httpx):
    from aspire_data.connect import ConnectClient
    c = ConnectClient(guid="g")
    c.get("/foo")
    c.post("/bar", json={"a": 1})
    c.delete("/baz")
    methods = [call[0] for call in mock_httpx.instances[-1].calls]
    assert methods == ["GET", "POST", "DELETE"]


def test_connect_client_health(mock_httpx):
    from aspire_data.connect import ConnectClient
    c = ConnectClient(guid="g")
    mock_httpx.instances[-1].set_response(json_body={"status": "ok"})
    out = c.health()
    assert out == {"status": "ok"}


def test_connect_base_url_required(monkeypatch):
    monkeypatch.delenv("CONNECT_BASE_URL", raising=False)
    from aspire_data.connect import _base
    with pytest.raises(RuntimeError, match="CONNECT_BASE_URL"):
        _base()


def test_connect_api_key_required(monkeypatch):
    monkeypatch.delenv("CONNECT_API_KEY", raising=False)
    from aspire_data.connect import _key
    with pytest.raises(RuntimeError, match="CONNECT_API_KEY"):
        _key()


def test_hana_sql_calls_via_connect(mock_httpx):
    from aspire_data.connect import hana_sql
    mock_httpx.instances  # warm
    # Need to capture the client created INSIDE hana_sql
    mock_httpx.instances.clear()
    hana_sql("SELECT 1", params={"x": 1}, row_limit=100)
    client = mock_httpx.instances[-1]
    assert client.calls[0][0] == "POST"
    assert client.calls[0][1] == "/sql"
    body = client.calls[0][2]["json"]
    assert body["sql"] == "SELECT 1"
    assert body["params"] == {"x": 1}
    assert body["row_limit"] == 100


def test_render_pdf_uses_connect(mock_httpx):
    from aspire_data.connect import render_pdf
    mock_httpx.instances.clear()
    render_pdf("<h1>hi</h1>", css=".h1{color:red}")
    client = mock_httpx.instances[-1]
    assert client.calls[0][0] == "POST"
    assert client.calls[0][1] == "/render/pdf"


def test_notify_send_constructs_payload(mock_httpx):
    from aspire_data.connect import notify_send
    mock_httpx.instances.clear()
    notify_send("telegram:kenny", text="hello", title="t", level="info")
    client = mock_httpx.instances[-1]
    body = client.calls[0][2]["json"]
    assert body == {"target": "telegram:kenny", "level": "info",
                    "text": "hello", "title": "t"}


def test_jobs_submit_returns_id(mock_httpx):
    from aspire_data.connect import jobs_submit
    mock_httpx.instances.clear()
    # Mock the response BEFORE calling — the client is created inside jobs_submit
    import aspire_data.connect as _c
    from aspire_data.connect import ConnectClient
    real_init = ConnectClient.__init__
    def _patched_init(self, *a, **kw):
        real_init(self, *a, **kw)
        # Now the FakeClient inside is the latest
        if mock_httpx.instances:
            mock_httpx.instances[-1].set_response(json_body={"id": "job-42"})
    ConnectClient.__init__ = _patched_init
    try:
        out = jobs_submit("hetzner_proxy", {"path": "/sports/x"})
        assert out == "job-42"
    finally:
        ConnectClient.__init__ = real_init
