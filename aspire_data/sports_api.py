"""Sports API client — the Oracle VM FastAPI that fronts the sports DBs.

Hides the two quirks every consumer has to discover the hard way:

  1. Request body MUST be wrapped in `{"parameters": {...}}` — any
     other shape (`arguments`, `args`, `input`, `params`) returns 500.
  2. Response shape is `result.data.records`, NOT `result.data`.

Net: callers just say `api.tool("search_athlete_anywhere", q="...")`
and get a list back.

CONFIG (env)

    SPORTS_API_URL    https://qatar-sports-analytics.duckdns.org   (default)
    SPORTS_API_KEY    optional — Sports API itself is unauth'd for read tools

USAGE

    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    rows = api.tool("search_athlete_anywhere", q="van Niekerk", limit=5)
    rows = api.tool("padel_rankings", year=2024, gender="men", limit=20)
"""
from __future__ import annotations

import os
from typing import Any

import httpx


def _base() -> str:
    return os.environ.get(
        "SPORTS_API_URL",
        "https://qatar-sports-analytics.duckdns.org",
    ).rstrip("/")


class SportsApi:
    """Wrapper for the Sports API on Oracle VM — handles the
    `parameters` envelope + `result.data.records` unwrap."""

    def __init__(self, base_url: str | None = None,
                 api_key: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or _base()).rstrip("/")
        headers: dict[str, str] = {}
        # Some tools require auth; ENV not required if your usage is read-only
        api_key = api_key or os.environ.get("SPORTS_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout,
        )

    def tool(self, name: str, **params) -> Any:
        """Call /api/tools/<name> with the `parameters` envelope.
        Returns the unwrapped records (list of dicts).
        Raises httpx.HTTPStatusError on non-2xx."""
        r = self._client.post(f"/api/tools/{name}",
                               json={"parameters": params})
        r.raise_for_status()
        body = r.json()
        # Tool responses use {ok, result: {data: {records: [...]}}}
        result = body.get("result") or {}
        data = result.get("data") or {}
        if isinstance(data, dict) and "records" in data:
            return data["records"]
        return data

    def openapi(self) -> dict:
        """Returns the OpenAPI spec — useful for tool discovery."""
        return self._client.get("/openapi.json").json()

    def list_tools(self) -> list[str]:
        """All exposed tool names, parsed from the OpenAPI spec."""
        spec = self.openapi()
        return [p.split("/")[-1]
                for p in (spec.get("paths") or {})
                if p.startswith("/api/tools/")]

    def tool_schema(self, name: str) -> dict:
        """Schema for one tool's parameters."""
        return self._client.get(f"/api/tools/{name}/schema").json()

    def health(self) -> dict:
        return self._client.get("/health").json()

    def close(self) -> None:
        self._client.close()
