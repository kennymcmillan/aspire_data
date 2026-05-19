"""Posit Connect admin REST client — content lookups, job logs, deploys.

Useful when debugging a deployed app or building deployment tooling.

CONFIG (env)

    CONNECT_BASE_URL   https://<your-connect-host>
    CONNECT_API_KEY    <your Connect key>

USAGE

    from aspire_data.posit import ConnectAdminClient
    pc = ConnectAdminClient()
    content = pc.get_content("<content-guid>")
    jobs = pc.list_jobs(content["guid"], count=5)
    log = pc.get_job_log(content["guid"], jobs[0]["key"])
    print(log)
"""
from __future__ import annotations

import os

import httpx


def _base() -> str:
    url = os.environ.get("CONNECT_BASE_URL", "").rstrip("/")
    if not url:
        raise RuntimeError(
            "CONNECT_BASE_URL not set — set the URL of your Posit Connect server.")
    return url


class ConnectAdminClient:
    def __init__(self, base_url: str | None = None, key: str | None = None,
                 timeout: float = 30.0):
        base = (base_url or _base()).rstrip("/")
        self.base_url = f"{base}/__api__/v1"
        self.key = key or os.environ["CONNECT_API_KEY"]
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Key {self.key}"},
            timeout=timeout,
        )

    # ---- content ----
    def get_content(self, guid: str) -> dict:
        return self._client.get(f"/content/{guid}").raise_for_status().json()

    def list_content(self, *, owner_guid: str | None = None) -> list[dict]:
        params = {"owner_guid": owner_guid} if owner_guid else None
        return self._client.get("/content", params=params).raise_for_status().json()

    def patch_content(self, guid: str, **fields) -> dict:
        """Update content settings — e.g. min_processes, idle_timeout."""
        return self._client.patch(f"/content/{guid}", json=fields).raise_for_status().json()

    # ---- jobs ----
    def list_jobs(self, guid: str, count: int = 10) -> list[dict]:
        return self._client.get(f"/content/{guid}/jobs",
                                  params={"count": count}).raise_for_status().json()

    def get_job_log(self, guid: str, job_key: str) -> str:
        """Connect returns a JSON envelope; we extract the flat log text."""
        r = self._client.get(f"/content/{guid}/jobs/{job_key}/log")
        r.raise_for_status()
        body = r.json()
        # Each entry: {source: "stdout"|"stderr", timestamp, data}
        lines = []
        for e in (body.get("entries") or []):
            lines.append(f"[{e.get('source')}] {e.get('data')}")
        return "\n".join(lines)

    def close(self) -> None:
        self._client.close()
