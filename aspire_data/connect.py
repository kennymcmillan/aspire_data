"""Posit Connect API clients — the 4 Aspire FastAPIs + Connect admin REST.

Every Aspire app talks to at least one of these:

    hana-api     SQL passthrough + curated _SYS_BIC views
    render-api   pandoc + WeasyPrint (HTML→PDF, MD→DOCX, etc.)
    jobs-api     submit-and-poll dispatcher for long-running work
    notify-api   fan-out to Telegram + webhooks

USAGE
=====

Generic client wrapping ALL Connect content (any GUID):

    from aspire_data.connect import ConnectClient
    cli = ConnectClient(guid="<content-guid>")     # uses $CONNECT_API_KEY
    cli.get("/diary/templates")                     # → dict / list
    cli.post("/diary/upload", json={"...": ...})    # → dict

Convenience wrappers for the 4 Aspire APIs (one-liner):

    from aspire_data.connect import hana_sql, render_pdf, jobs_submit, notify_send
    rows = hana_sql("SELECT TOP 5 ATHLETE FROM SAMS_VIEW WHERE ROWNUM<5")
    pdf  = render_pdf("<h1>Hello</h1>", css=".h1{color:red}")
    jid  = jobs_submit("hetzner_proxy", {"path": "/fip/calendar"})
    notify_send("telegram:kenny", text="Build done")

CONFIG (env, public-safe — set in your .env)

    CONNECT_BASE_URL   https://<your-connect-host>   (env-driven)
    CONNECT_API_KEY    <your Connect key>
    HANA_API_GUID      <your hana-api GUID>
    RENDER_API_GUID    <your render-api GUID>
    JOBS_API_GUID      <your jobs-api GUID>
    NOTIFY_API_GUID    <your notify-api GUID>

The 4 GUID env vars are required for the convenience wrappers; the
generic ConnectClient just needs the guid passed in.
"""
from __future__ import annotations

__all__ = ['ConnectClient', 'hana_sql', 'hana_view', 'render_pdf', 'render_doc', 'jobs_submit', 'jobs_get', 'jobs_wait', 'notify_send']

import os
import threading
from typing import Any

import httpx


def _base() -> str:
    url = os.environ.get("CONNECT_BASE_URL", "").rstrip("/")
    if not url:
        raise RuntimeError(
            "CONNECT_BASE_URL not set — set the URL of your Posit Connect server.")
    return url


def _key() -> str:
    k = os.environ.get("CONNECT_API_KEY")
    if not k:
        raise RuntimeError(
            "CONNECT_API_KEY not set — needed for any Connect-backed call.")
    return k


class ConnectClient:
    """HTTP client for any Connect-hosted content (FastAPI, Dash, Shiny).

    auth via the standard `Authorization: Key <key>` header. SSL
    handled by aspire_data.ssl_fix on import.
    """

    def __init__(self, guid: str, key: str | None = None,
                 base_url: str | None = None, timeout: float = 30.0):
        self.guid = guid
        self.base_url = (base_url or _base()).rstrip("/")
        self.content_url = f"{self.base_url}/content/{guid}"
        self.key = key or _key()
        self._client = httpx.Client(
            base_url=self.content_url,
            headers={"Authorization": f"Key {self.key}"},
            timeout=timeout,
        )

    # ----- core verbs -----
    def get(self, path: str, **kwargs) -> Any:
        r = self._client.get(path, **kwargs)
        r.raise_for_status()
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content

    def post(self, path: str, **kwargs) -> Any:
        r = self._client.post(path, **kwargs)
        r.raise_for_status()
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content

    def delete(self, path: str, **kwargs) -> Any:
        r = self._client.delete(path, **kwargs)
        r.raise_for_status()
        return r.json() if r.is_success else None

    # ----- raw, for streaming / non-JSON -----
    def raw_get(self, path: str, **kwargs) -> httpx.Response:
        return self._client.get(path, **kwargs)

    def raw_post(self, path: str, **kwargs) -> httpx.Response:
        return self._client.post(path, **kwargs)

    def health(self) -> dict:
        """Generic /health probe (every FastAPI we deploy has one)."""
        return self.get("/health")

    def close(self) -> None:
        self._client.close()


# ============================================================
# Convenience wrappers for the 4 Aspire FastAPIs on Connect
# ============================================================

_client_cache: dict[tuple, ConnectClient] = {}
_client_lock = threading.Lock()


def _client_for(env_var: str) -> ConnectClient:
    """Cached per (httpx class, env var, guid, base) — the convenience
    wrappers used to open a fresh TCP+TLS connection on EVERY call
    (worst in jobs_get poll loops) and leaked the socket. Keying on the
    httpx.Client class id keeps test mocks isolated per test."""
    guid = os.environ.get(env_var)
    if not guid:
        raise RuntimeError(f"{env_var} not set — needed for this Aspire API call.")
    key = (id(httpx.Client), env_var, guid, os.environ.get("CONNECT_BASE_URL", ""))
    with _client_lock:
        cli = _client_cache.get(key)
        if cli is None:
            cli = ConnectClient(guid)
            _client_cache[key] = cli
    return cli


# ---- hana-api -----

def hana_sql(sql: str, *, params: dict | None = None,
             row_limit: int | None = None) -> list[dict]:
    """Run a SELECT/WITH against SAP HANA via the hana-api FastAPI.

    The endpoint is read-only and SQL-injects-by-construction guards
    against anything but SELECT/WITH statements.
    """
    cli = _client_for("HANA_API_GUID")
    body: dict[str, Any] = {"sql": sql}
    if params is not None: body["params"] = params
    if row_limit is not None: body["row_limit"] = row_limit
    return cli.post("/sql", json=body).get("rows", [])


def hana_view(view_name: str, **filters) -> list[dict]:
    """Convenience: fetch from a curated _SYS_BIC view through the
    hana-api `/views/{view}` endpoint."""
    cli = _client_for("HANA_API_GUID")
    return cli.get(f"/views/{view_name}", params=filters).get("rows", [])


# ---- render-api -----

def render_pdf(html: str, *, css: str | None = None,
               base_url: str | None = None) -> bytes:
    """Render HTML + optional CSS to PDF via render-api / WeasyPrint."""
    cli = _client_for("RENDER_API_GUID")
    body: dict[str, Any] = {"html": html}
    if css is not None: body["css"] = css
    if base_url is not None: body["base_url"] = base_url
    return cli.raw_post("/render/pdf", json=body).content


def render_doc(content: str, *, from_fmt: str = "markdown",
               to_fmt: str = "docx") -> bytes:
    """Pandoc-backed conversion (md/html/rst → docx/epub/rtf/odt/latex)."""
    cli = _client_for("RENDER_API_GUID")
    body = {"content": content, "from_fmt": from_fmt, "to_fmt": to_fmt}
    return cli.raw_post("/render", json=body).content


# ---- jobs-api -----

def jobs_submit(handler: str, payload: dict, *,
                notify_target: str | None = None) -> str:
    """Submit a job to jobs-api. Returns the job id (poll with jobs_get)."""
    cli = _client_for("JOBS_API_GUID")
    body = {"handler": handler, "payload": payload}
    if notify_target:
        body["notify"] = {"target": notify_target}
    return cli.post("/jobs", json=body)["id"]


def jobs_get(job_id: str) -> dict:
    """Poll a job's state. Status is one of queued|running|ok|fail."""
    cli = _client_for("JOBS_API_GUID")
    return cli.get(f"/jobs/{job_id}")


def jobs_wait(job_id: str, *, timeout: float = 300, poll_s: float = 2.0) -> dict:
    """Block until the job leaves the running/queued state."""
    import time
    cli = _client_for("JOBS_API_GUID")
    t0 = time.monotonic()
    while True:
        out = cli.get(f"/jobs/{job_id}")
        if out.get("status") in ("ok", "fail"):
            return out
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"job {job_id} still {out.get('status')} after {timeout}s")
        time.sleep(poll_s)


# ---- notify-api -----

def notify_send(target: str, *, text: str | None = None,
                title: str | None = None, level: str = "info") -> dict:
    """Send a notification. target is e.g. `telegram:kenny`,
    `webhook:slack-alerts`, or a raw https URL."""
    cli = _client_for("NOTIFY_API_GUID")
    body: dict[str, Any] = {"target": target, "level": level}
    if text:  body["text"] = text
    if title: body["title"] = title
    return cli.post("/notify", json=body)
