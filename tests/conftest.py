"""Shared pytest fixtures.

Every test runs against STUB env vars and MOCKED httpx — never hits
a real backend. Real-environment integration is deliberately out of
scope here (CI runs against ephemeral mocked clients).
"""
from __future__ import annotations

import os
import pytest


# Default stub env so every client constructor finds non-empty values.
_STUB_ENV = {
    "CONNECT_BASE_URL":   "https://connect.example.com",
    "CONNECT_API_KEY":    "stub-connect-key",
    "HANA_API_GUID":      "stub-hana-guid",
    "RENDER_API_GUID":    "stub-render-guid",
    "JOBS_API_GUID":      "stub-jobs-guid",
    "NOTIFY_API_GUID":    "stub-notify-guid",
    "ASPIRE_KB_API_GUID": "stub-kb-guid",

    "SPORTS_API_URL":     "https://sports.example.com",
    "SPORTS_API_KEY":     "stub-sports-key",

    "SAMS_BASE_URL":      "https://sams.example.com",
    "SAMS_CLIENT_ID":     "stub-sams-id",
    "SAMS_CLIENT_SECRET": "stub-sams-secret",

    "HETZNER_PROXY_BASE": "https://hetzner-proxy.example.com",
    "HETZNER_PROXY_KEY":  "stub-hetzner-key",

    "AIVEN_PG_URL":    "postgres://u:p@pg.example.com:5432/db?sslmode=require",
    "AIVEN_MYSQL_URL": "mysql://u:p@my.example.com:16439/db",

    "ORACLE_MYSQL_URL": "mysql://u:p@oracle.example.com:3306/db",
    "ORACLE_PG_URL":    "postgres://u:p@oracle.example.com:5432/db",

    "HANA_HOST":      "hana.example.com",
    "HANA_PORT":      "30015",
    "HANA_USER":      "stub-hana-user",
    "HANA_PASSWORD":  "stub-hana-pwd",

    "MOTHERDUCK_TOKEN": "stub-md-token",

    # Disable truststore so tests don't try to load a real cert chain
    "ASPIRE_DATA_NO_TRUSTSTORE": "1",
}


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    """Auto-apply for every test. Individual tests can monkeypatch.delenv
    to test the missing-env-var error paths."""
    for k, v in _STUB_ENV.items():
        monkeypatch.setenv(k, v)
    yield


@pytest.fixture
def mock_httpx(monkeypatch):
    """Replace httpx.Client with a recording mock so client classes
    can be constructed + called without hitting the network.

    The mock returns 200 with a {} body for every request. Tests that
    need richer responses can override individual methods.
    """
    import httpx

    class FakeResponse:
        def __init__(self, status_code: int = 200, json_body: dict | list | None = None,
                      content: bytes = b"", headers: dict | None = None):
            self.status_code = status_code
            self._json = {} if json_body is None else json_body
            self.content = content
            self.text = content.decode("utf-8", errors="replace")
            self.headers = headers or {"content-type": "application/json"}
            self.is_success = 200 <= status_code < 300

        def json(self):
            return self._json

        def raise_for_status(self):
            if not self.is_success:
                raise httpx.HTTPStatusError(
                    f"{self.status_code}", request=None, response=self,
                )
            return self

    class FakeClient:
        instances: list[FakeClient] = []

        def __init__(self, *args, **kwargs):
            self.base_url    = kwargs.get("base_url", "")
            self.headers     = kwargs.get("headers", {})
            self.timeout     = kwargs.get("timeout", None)
            self.calls       = []          # [(method, path, kwargs), ...]
            self._next_response = FakeResponse()
            FakeClient.instances.append(self)

        def set_response(self, **kwargs):
            self._next_response = FakeResponse(**kwargs)

        def set_response_sequence(self, *kwargs_list):
            """Queue responses returned in order; falls back to _next_response
            when the queue is empty (for retry tests)."""
            self._response_queue = [FakeResponse(**k) for k in kwargs_list]

        def get(self, path, **kwargs):
            self.calls.append(("GET", path, kwargs))
            if getattr(self, "_response_queue", None):
                return self._response_queue.pop(0)
            return self._next_response

        def post(self, path, **kwargs):
            self.calls.append(("POST", path, kwargs))
            return self._next_response

        def delete(self, path, **kwargs):
            self.calls.append(("DELETE", path, kwargs))
            return self._next_response

        def patch(self, path, **kwargs):
            self.calls.append(("PATCH", path, kwargs))
            return self._next_response

        def close(self):
            pass

    FakeClient.instances = []
    monkeypatch.setattr(httpx, "Client", FakeClient)
    return FakeClient
