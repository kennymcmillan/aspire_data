"""SAMS (Aspire Sports Management System) — picker drill-down + cached lookups.

Replaces the ~400-line `app/data/sams.py` that every Aspire athlete-aware
app re-implements. Same `ClientId` + `ClientSecret` auth, same 1h TTL
caches, same parallel sport-roster fan-out.

CONFIG (env)

    SAMS_BASE_URL        the Aspire-internal SAMS host
    SAMS_CLIENT_ID
    SAMS_CLIENT_SECRET

USAGE

    from aspire_data.sams import SamsClient
    sams = SamsClient()                                    # env-driven
    rows = sams.search("van Niekerk")                      # fuzzy
    ctx  = sams.get_athlete_by_mrn("20040861")             # exact MRN
    plans = sams.list_training_plans(sport_id=1, date="2026-05-19")
    roster = sams.list_sport_roster(sport_id=1, days_back=60)

NOTES

    SAMS doesn't expose a 'players-by-sport' endpoint, so
    list_sport_roster() walks the last N days of training plans and
    dedupes — the same trick the nutrition app uses. Concurrency is
    handled internally via a ThreadPoolExecutor (10 workers).
"""
from __future__ import annotations

__all__ = ['SamsClient', 'SamsError', 'DEFAULT_SPORTS', 'first_target_event']

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import httpx
from cachetools import TTLCache


# Default sport-id → name map (override by passing sports= to constructor).
DEFAULT_SPORTS = {
    1: "Athletics",  2: "Fencing",   3: "Padel",
    4: "Squash",     5: "Table Tennis",
    6: "Swimming",   7: "Shooting",
}


def first_target_event(raw: str | None) -> str | None:
    """Pick the first usable event from SAMS `targetEventNames`.

    The field is comma-separated (`"100m, 200m"`, `"Foil, Epee"`,
    `"Hammer Throw"`). "TBD" tokens are skipped.

    Returns `None` if the input is empty or only "TBD" tokens.
    """
    if not raw:
        return None
    parts = [x.strip() for x in str(raw).split(",")]
    return next((x for x in parts if x and x.upper() != "TBD"), None)


class SamsError(RuntimeError):
    """Raised on SAMS 4xx/5xx — carries the response detail."""


class SamsClient:
    def __init__(self, base_url: str | None = None,
                 client_id: str | None = None,
                 client_secret: str | None = None,
                 sports: dict[int, str] | None = None,
                 timeout: float = 20.0,
                 cache_ttl: int = 3600,        # 1 h athlete context cache
                 roster_cache_ttl: int = 1800,  # 30 min sport-roster cache
                 max_workers: int = 10):
        self.base_url = (base_url or os.environ.get("SAMS_BASE_URL", "")).rstrip("/")
        if not self.base_url:
            raise SamsError("SAMS_BASE_URL not set")
        self.client_id     = client_id     or os.environ["SAMS_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["SAMS_CLIENT_SECRET"]
        self.sports = sports or DEFAULT_SPORTS

        self._client = httpx.Client(
            base_url=self.base_url, timeout=timeout,
            headers={
                "ClientId":     self.client_id,
                "ClientSecret": self.client_secret,
                "Accept":       "application/json",
                "User-Agent":   "aspire_data/0.1",
            },
        )
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                         thread_name_prefix="sams")

        # caches
        self._mrn_cache:         TTLCache = TTLCache(maxsize=2000, ttl=cache_ttl)
        self._context_cache:     TTLCache = TTLCache(maxsize=2000, ttl=cache_ttl)
        self._plans_cache:       TTLCache = TTLCache(maxsize=200,  ttl=600)
        self._roster_cache:      TTLCache = TTLCache(maxsize=400,  ttl=600)
        self._sport_cache:       TTLCache = TTLCache(maxsize=20,   ttl=roster_cache_ttl)
        self._enrollments_cache: TTLCache = TTLCache(maxsize=1,    ttl=cache_ttl)

    # ---- low-level GET ----
    def _get(self, path: str, params: dict | None = None):
        r = self._client.get(path, params=params)
        if r.status_code >= 400:
            raise SamsError(f"SAMS {r.status_code} on {path}: {r.text[:200]}")
        return r.json()

    # ---- search / lookup ----
    def search(self, q: str) -> list[dict]:
        """Fuzzy search players by name / MRN / partial. Returns raw rows."""
        return self._get("/api/ExternalApps/player/search", params={"q": q}) or []

    def get_athlete_context(self, player_id: int, *, enrich: bool = False) -> dict | None:
        """Full athlete record (name, sport, age, photo, etc.) by player_id.

        When ``enrich=True``, merges current `PlayerEnrollmentPeriods` data
        onto the returned dict — adds ``sport_id``, ``sport``, ``discipline_id``,
        ``discipline``, ``target_event`` (first non-TBD token of
        ``targetEventNames``), ``target_event_raw``, ``player_type``,
        ``coach_name``. This is the authoritative source for sport +
        event because the player/details endpoint doesn't reliably surface
        sportId for multi-sport athletes.
        """
        key = int(player_id)
        cache_key = (key, bool(enrich))
        if cache_key in self._context_cache:
            return self._context_cache[cache_key]
        try:
            ctx = self._get(f"/api/ExternalApps/player/{key}")
        except SamsError:
            ctx = None
        if ctx and enrich:
            try:
                enr = self.get_current_enrollment(key)
            except SamsError:
                enr = {}
            if enr:
                sid = enr.get("sportId")
                if sid is not None:
                    ctx["sport_id"] = int(sid)
                    ctx["sport"] = (enr.get("sportName")
                                    or self.sports.get(int(sid))
                                    or ctx.get("sport"))
                ctx["discipline_id"] = enr.get("disciplineId")
                ctx["discipline"]    = enr.get("disciplineName")
                ctx["target_event"]     = first_target_event(enr.get("targetEventNames"))
                ctx["target_event_raw"] = enr.get("targetEventNames")
                ctx["player_type"] = enr.get("playerTypeName")
                ctx["coach_name"]  = enr.get("coachName")
        self._context_cache[cache_key] = ctx
        return ctx

    # ---- enrollment periods (sport / discipline / target event) ----
    def get_all_enrollment_periods(self) -> list[dict]:
        """Every `PlayerEnrollmentPeriods` row across the academy.

        Heavy (~415 KB / 1000+ rows) but stable, so cached for the same TTL
        as the athlete context cache. Used by :meth:`get_current_enrollment`.
        """
        if "all" in self._enrollments_cache:
            return self._enrollments_cache["all"]
        rows = self._get("/api/ExternalApps/PlayerEnrollmentPeriods") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or []
        self._enrollments_cache["all"] = rows
        return rows

    def get_current_enrollment(self, player_id: int) -> dict:
        """The most-relevant current enrollment for one player.

        SAMS allows multiple concurrent (endDate=None) enrollments across
        sports. Picks (1) the row flagged ``isPrimary``, falling back to
        (2) the row with the most-recent ``startDate``. Returns ``{}`` if
        the player has no current enrollment.
        """
        pid = int(player_id)
        current = [
            p for p in self.get_all_enrollment_periods()
            if p.get("playerId") == pid and p.get("endDate") in (None, "")
        ]
        if not current:
            return {}
        primary = next((p for p in current if p.get("isPrimary")), None)
        if primary:
            return primary
        return max(current, key=lambda p: (p.get("startDate") or ""))

    def get_athlete_by_mrn(self, mrn: str | int) -> dict | None:
        """SAMS has no 'by MRN' endpoint, so we fuzzy-search and pick
        the exact-MRN match. 1h cached."""
        key = str(mrn or "").strip()
        if not key:
            return None
        if key in self._mrn_cache:
            return self._mrn_cache[key]
        try:
            rows = self.search(key)
        except SamsError:
            self._mrn_cache[key] = None
            return None
        hit = next((r for r in rows if str(r.get("mrn") or "").strip() == key), None)
        if not hit or not hit.get("playerId"):
            self._mrn_cache[key] = None
            return None
        ctx = self.get_athlete_context(int(hit["playerId"]))
        self._mrn_cache[key] = ctx
        return ctx

    # ---- training plans + rosters ----
    def list_training_plans(self, sport_id: int, training_date: str) -> list[dict]:
        key = (int(sport_id), training_date)
        if key in self._plans_cache:
            return self._plans_cache[key]
        out = self._get(
            "/api/ExternalApps/training-plans",
            params={"sportId": sport_id, "trainingDate": training_date},
        ) or []
        self._plans_cache[key] = out
        return out

    def get_plan_roster(self, training_plan_id: int) -> list[dict]:
        key = int(training_plan_id)
        if key in self._roster_cache:
            return self._roster_cache[key]
        out = self._get(
            f"/api/ExternalApps/training-plans/{key}/roster"
        ) or []
        self._roster_cache[key] = out
        return out

    def list_sport_roster(self, sport_id: int, *,
                           days_back: int = 60) -> list[dict]:
        """All unique athletes for a sport over the last N days.

        Parallel fan-out: (a) plan-list per day → unique plan-ids,
        (b) roster per plan → dedupe by player_id. ~1.5 s wall-clock
        for a 60-day window.
        """
        key = (int(sport_id), int(days_back))
        if key in self._sport_cache:
            return self._sport_cache[key]

        today = date.today()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(days_back)]

        plan_ids: set[int] = set()
        plan_futs = [self._pool.submit(self.list_training_plans,
                                        int(sport_id), d) for d in dates]
        for fut in as_completed(plan_futs, timeout=60):
            try:
                plans = fut.result() or []
            except Exception:  # noqa: BLE001
                continue
            for p in plans:
                if p.get("training_plan_id"):
                    plan_ids.add(int(p["training_plan_id"]))

        if not plan_ids:
            self._sport_cache[key] = []
            return []

        seen: set[int] = set()
        athletes: list[dict] = []
        roster_futs = [self._pool.submit(self.get_plan_roster, pid)
                       for pid in plan_ids]
        for fut in as_completed(roster_futs, timeout=60):
            try:
                roster = fut.result() or []
            except Exception:  # noqa: BLE001
                continue
            for athlete in roster:
                pid = athlete.get("player_id")
                if not pid or pid in seen:
                    continue
                seen.add(int(pid))
                athletes.append(athlete)

        athletes.sort(key=lambda a: (a.get("full_name") or "").lower())
        self._sport_cache[key] = athletes
        return athletes

    def close(self) -> None:
        self._client.close()
        self._pool.shutdown(wait=False)
