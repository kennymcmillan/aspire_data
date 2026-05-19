"""Hetzner OpenClaw scraper client — proxy-preferred routing.

Two valid paths to the scraper:

  Path 1 (preferred) — Sports API on Oracle catch-all proxies the
    entire Hetzner surface. Works from any machine over HTTPS 443.
    Use this anywhere outside the Oracle VM.

  Path 2 — direct to Hetzner :8089 (UFW-restricted to Oracle VM only).
    Only valid when running ON the Oracle VM or via SSH tunnel.

This client picks Path 1 automatically unless `direct=True`.

CONFIG (env)

    HETZNER_PROXY_BASE   https://<your-proxy-host>/hetzner   (no trailing slash)
    HETZNER_PROXY_KEY    <caller-facing X-API-Key for the proxy>
    HETZNER_DIRECT_BASE  http://<hetzner-host>:8089         (optional, for direct mode)
    HETZNER_DIRECT_KEY   <direct service X-API-Key>         (optional)

USAGE

    from aspire_data.hetzner import HetznerClient
    h = HetznerClient()
    rows = h.scrape("/sports/fip/calendar", method="POST")
    docs = h.scrape("/sniff", method="POST",
                     json={"url": "https://example.com"})
    health = h.health()
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class HetznerClient:
    def __init__(self, *, direct: bool = False, timeout: float = 60.0):
        self.direct = direct
        if direct:
            base = os.environ.get("HETZNER_DIRECT_BASE", "")
            key  = os.environ.get("HETZNER_DIRECT_KEY", "")
            label = "HETZNER_DIRECT_BASE"
        else:
            base = os.environ.get("HETZNER_PROXY_BASE", "")
            key  = os.environ.get("HETZNER_PROXY_KEY", "")
            label = "HETZNER_PROXY_BASE"
        if not base:
            raise RuntimeError(f"{label} not set")
        if not key:
            raise RuntimeError(
                f"{'HETZNER_DIRECT_KEY' if direct else 'HETZNER_PROXY_KEY'} not set"
            )
        self.base_url = base.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url, timeout=timeout,
            headers={"X-API-Key": key, "Accept": "application/json"},
        )

    def scrape(self, path: str, *, method: str = "POST",
               json: dict | None = None,
               params: dict | None = None) -> Any:
        """Hit any Hetzner endpoint. `path` is the part AFTER the
        base URL (e.g. `/sports/fip/calendar`, `/sniff`, `/h2h`).
        Returns the JSON body (parsed)."""
        if method.upper() == "GET":
            r = self._client.get(path, params=params)
        else:
            r = self._client.post(path, json=json, params=params)
        r.raise_for_status()
        return r.json() if "application/json" in r.headers.get("content-type", "") \
            else r.content

    def health(self) -> dict:
        return self._client.get("/health").json()

    def close(self) -> None:
        self._client.close()
