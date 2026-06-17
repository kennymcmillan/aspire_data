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

__all__ = ['ConnectAdminClient']

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

    # ---- users (directory) ----
    def get_current_user(self) -> dict:
        """The API KEY OWNER's record (username, first/last name, email, guid,
        user_role). NB: this is NOT the app visitor — inside a deployed Connect
        app the visitor's username is the ``RSTUDIO_USER_NAME`` env var; resolve
        it against :meth:`list_users` to get their email/display name."""
        return self._client.get("/user").raise_for_status().json()

    def list_users(self, *, prefix: str | None = None,
                   page_size: int = 500) -> list[dict]:
        """Every Connect user (auto-paginated). Each row has ``username``,
        ``first_name``, ``last_name``, ``email``, ``user_role``, ``guid``,
        ``locked``, ``confirmed``, .... Pass ``prefix`` to server-side filter by
        the start of username/email/name.

        Powers org-wide "assign to / requested by" pickers (name + email) so apps
        don't hand-roll a user table. Requires a key allowed to enumerate users
        (administrator sees all)."""
        out: list[dict] = []
        page = 1
        while True:
            params: dict = {"page_number": page, "page_size": min(page_size, 500)}
            if prefix:
                params["prefix"] = prefix
            body = self._client.get("/users", params=params).raise_for_status().json()
            results = body.get("results") or []
            out.extend(results)
            total = body.get("total")
            if not results or (total is not None and len(out) >= total):
                break
            page += 1
        return out

    def find_user(self, username_or_email: str) -> dict | None:
        """Resolve one user by exact username or email (case-insensitive) —
        e.g. map a Connect app's ``RSTUDIO_USER_NAME`` to {name, email}."""
        needle = (username_or_email or "").strip().lower()
        if not needle:
            return None
        for u in self.list_users(prefix=username_or_email.split("@")[0] or None):
            if needle in (str(u.get("username", "")).lower(),
                          str(u.get("email", "")).lower()):
                return u
        # prefix may miss (e.g. display-name vs username) — fall back to full scan
        for u in self.list_users():
            if needle in (str(u.get("username", "")).lower(),
                          str(u.get("email", "")).lower()):
                return u
        return None

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
