"""Sports API client — the Oracle VM FastAPI that fronts the sports DBs.

Hides the two quirks every consumer has to discover the hard way:

  1. Request body MUST be wrapped in `{"parameters": {...}}` — any
     other shape (`arguments`, `args`, `input`, `params`) returns 500.
  2. Response shape is `result.data.records`, NOT `result.data`.

Net: callers just say `api.tool("search_athlete_anywhere", q="...")`
and get a list back.

CONFIG (env)

    SPORTS_API_URL    https://<your-sports-api-host>
    SPORTS_API_KEY    optional — Sports API itself is unauth'd for read tools

USAGE

    from aspire_data.sports_api import SportsApi
    api = SportsApi()
    rows = api.tool("search_athlete_anywhere", q="van Niekerk", limit=5)
    rows = api.tool("padel_rankings", year=2024, gender="men", limit=20)

    # Subject records (plain REST, not the tool envelope)
    rec  = api.record(10)                    # one athlete's stored record
    idx  = api.subjects(q="tamimi")          # the picker index
    dead = api.staleness(min_days=180, max_days=730)
"""
from __future__ import annotations

__all__ = ['SportsApi', 'SportsApiError', 'SportsApiWriteError', 'sql_literal']

import os
from datetime import date, datetime
from typing import Any

import httpx

from aspire_data._common import _base


def sql_literal(v: Any) -> str:
    """Quote a Python value as a MySQL literal for hand-built WHERE/DELETE clauses
    fed to ``execute_write_sql``. None->NULL, bool->1/0, numbers verbatim,
    date/datetime ISO-quoted, strings escaped (``\\`` and ``'`` doubled).

    NOTE: ``execute_write_sql`` rejects SQL ``--`` line comments — keep DDL clean.
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return f"'{v.isoformat()}'"
    s = str(v).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


class SportsApiError(RuntimeError):
    """Sports API returned an envelope-level failure."""


class SportsApiWriteError(SportsApiError):
    """Write tool returned HTTP 200 but inner result.success == False.

    The Sports API write endpoints fail SILENTLY at the HTTP layer —
    the outer status is just transport. Always check the inner result
    (see feedback_oracle_write_api_quirks)."""


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

    def tool_raw(self, name: str, **params) -> dict:
        """Like tool() but returns the FULL response body (envelope
        included: ok / result.success / result.error / rows_affected).
        Use when the caller needs more than the records list."""
        r = self._client.post(f"/api/tools/{name}",
                               json={"parameters": params})
        r.raise_for_status()
        return r.json()

    def tool_write(self, name: str, **params) -> dict:
        """Call a WRITE tool (execute_write_sql, bulk_insert,
        upsert_records, ...) with the inner-success guard: the Sports
        API returns HTTP 200 even when the write failed, so this
        checks result.success and raises SportsApiWriteError on inner
        failure. Returns the inner result dict on success."""
        body = self.tool_raw(name, **params)
        result = body.get("result") or {}
        if isinstance(result, dict) and result.get("success") is False:
            raise SportsApiWriteError(
                f"{name}: {result.get('error') or result.get('message') or 'failed'}")
        return result if isinstance(result, dict) else {"result": result}

    def table(self, name: str, *, where: str | None = None,
              order_by: str | None = None, desc: bool = False,
              limit: int = 100, offset: int = 0) -> list[dict]:
        """Read rows from the generic REST surface
        `GET /api/v1/table/{name}` — the full-extraction path (no
        20-row preview cap, supports offset pagination). Returns the
        `data` list."""
        params: dict[str, Any] = {"limit": limit}
        if where:
            params["where"] = where
        if order_by:
            params["order_by"] = order_by
            params["desc"] = "true" if desc else "false"
        if offset:
            params["offset"] = offset
        r = self._client.get(f"/api/v1/table/{name}", params=params)
        r.raise_for_status()
        return r.json().get("data") or []

    def get(self, path: str, **params) -> Any:
        """Raw GET passthrough for non-tools Sports API REST routes
        (e.g. /api/fencing/competitions/search, /api/service/match).
        Returns parsed JSON. Keeps auth/base/TLS handling here so apps
        never hand-roll requests for these surfaces."""
        # None means "not asked for". Forwarded, it becomes the literal string
        # "None" in the query and is matched against the column as a value.
        clean = {k: v for k, v in params.items() if v is not None}
        r = self._client.get(path, params=clean or None)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, json: dict | None = None, **kwargs) -> Any:
        """Raw POST passthrough for non-tools REST routes. NOTE: no
        envelope handling — for /api/tools/* use tool()/tool_write()."""
        r = self._client.post(path, json=json, **kwargs)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Subject records — the persisted, self-updating athlete/team record.
    # Plain REST, not the tool envelope, so they get their own methods
    # rather than being forced through `tool()`.
    # ------------------------------------------------------------------

    def record(self, subject_id: str, subject_type: str = "athlete",
               flat: bool = True, include_payload: bool = True) -> dict:
        """One subject's stored record.

        `flat=True` adds `results_flat` to the sport block: every result row in
        one shape, tagged `kind` = match | placing | fixture. Raw rows stay put
        alongside it. Raises httpx.HTTPStatusError with 404 when the subject has
        never been snapshotted — which is NOT the same as having no data.
        """
        return self.get(f"/api/records/{subject_type}/{subject_id}",
                        flat=flat, include_payload=include_payload)

    def subjects(self, q: str | None = None, sport: str | None = None,
                 subject_type: str | None = None, limit: int = 1000,
                 offset: int = 0) -> dict:
        """The index of snapshotted subjects. `total` is a SQL count, not the
        page length — read that, never `len(subjects)`."""
        return self.get("/api/records/subjects", q=q, sport=sport,
                        subject_type=subject_type, limit=limit, offset=offset)

    def staleness(self, min_days: int = 180, max_days: int | None = None,
                  limit: int = 50, sport: str | None = None,
                  subject_type: str | None = None) -> dict:
        """Subjects whose data has gone quiet. Pass `max_days` for RECENTLY
        quiet — without it the permanently dormant head the list for ever."""
        return self.get("/api/records/staleness", min_days=min_days,
                        max_days=max_days, limit=limit, sport=sport,
                        subject_type=subject_type)

    def record_changes(self, hours: int = 168, limit: int = 500) -> dict:
        """Recent change rows. For COUNTS use `record_changes_summary` — this
        endpoint is capped and its length is a page size, not a total."""
        return self.get("/api/records/changes", hours=hours, limit=limit)

    def record_changes_summary(self, hours: int = 168) -> dict:
        """Change counts by type, straight from SQL and uncapped."""
        return self.get("/api/records/changes/summary", hours=hours)

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
