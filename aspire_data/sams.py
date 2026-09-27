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

__all__ = ['SamsClient', 'SamsError', 'DEFAULT_SPORTS', 'first_target_event',
           'resolve_photo_url', 'fetch_photo', 'fetch_photos', 'PHOTO_USER_AGENT']

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx
from cachetools import TTLCache


# Default sport-id → name map (override by passing sports= to constructor).
DEFAULT_SPORTS = {
    1: "Athletics",  2: "Fencing",   3: "Padel",
    4: "Squash",     5: "Table Tennis",
    6: "Swimming",   7: "Shooting",
}


def _to_date(v):
    from datetime import date as _date, datetime as _dt
    if not v:
        return None
    if isinstance(v, _date):
        return v
    if isinstance(v, str):
        try:
            return _dt.strptime(v.split("T")[0], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _flatten(v, key="name"):
    if isinstance(v, dict):
        return v.get(key) or v.get("description") or None
    return v


def _age_from_dob(dob):
    if not dob:
        return None
    from datetime import date as _date
    today = _date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


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


# ── Athlete photos ───────────────────────────────────────────────────────────
# SAMS profileImageUrl comes in three shapes (checked across 164 athletes, 2026-09-27):
#   1. a public blob URL (most athletes)              -> used as is
#   2. an https URL on the SAMS web host /uploads/    -> used as is, BUT the host answers 403 to the
#      python-requests default user-agent, so a server-side fetch (PDF, email) must name itself
#   3. a bare file name, e.g. "123.jpg?t=224"         -> lives in {SAMS web root}/uploads/player-images/
#   4. a relative path "uploads/player-images/123.jpg?t=224" -> same folder
# Browsers load all three; server-side renderers (PDF/email) should use fetch_photo(s), which never
# raise: a missing photo means initials, never a failed report.
PHOTO_USER_AGENT = "aspire_data (athlete photos)"
_PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_photo_cache: TTLCache = TTLCache(maxsize=2000, ttl=6 * 3600)    # successes only


def resolve_photo_url(raw, *, base_url: str | None = None) -> str:
    """Normalise a SAMS photo value to a fetchable https URL, or '' (initials).

    base_url: the SAMS API base (e.g. SamsClient.base_url); defaults to SAMS_BASE_URL. Only its
    scheme + host are used, to place bare file names under /uploads/player-images/."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.startswith("https://"):
        return s
    for prefix in ("/uploads/player-images/", "uploads/player-images/"):     # shape 4: relative path
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    name = s.split("?", 1)[0]
    if "://" in s or "/" in name or "\\" in name or not name.lower().endswith(_PHOTO_EXTS):
        return ""                                          # http://, paths, javascript:, junk
    root = urlsplit(base_url or os.environ.get("SAMS_BASE_URL", ""))
    if root.scheme != "https" or not root.netloc:
        return ""
    return f"https://{root.netloc}/uploads/player-images/{s}"


def fetch_photo(url: str, *, timeout: float = 4.0) -> bytes | None:
    """Image bytes for a resolved photo URL, or None. Named user-agent; never raises; successes
    are cached 6 h (failures are not, so a transient error retries next time)."""
    if not url or not str(url).startswith("https://"):
        return None
    hit = _photo_cache.get(url)
    if hit is not None:
        return hit
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": PHOTO_USER_AGENT, "Accept": "image/*"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/") or not r.content:
        return None
    _photo_cache[url] = r.content
    return r.content


def fetch_photos(urls, *, max_workers: int = 8, deadline: float = 8.0) -> dict[str, bytes | None]:
    """{url: bytes | None} for many URLs in parallel. Anything unfinished at `deadline` seconds is
    None, so a slow host delays a report by at most `deadline`, never fails it."""
    todo = list(dict.fromkeys(u for u in urls if u))
    if not todo:
        return {}
    ex = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sams-photo")
    futs = {ex.submit(fetch_photo, u): u for u in todo}
    done, _ = wait(futs, timeout=deadline)
    got = {futs[f]: f.result() for f in done}
    ex.shutdown(wait=False, cancel_futures=True)
    return {u: got.get(u) for u in todo}


class SamsClient:
    def __init__(self, base_url: str | None = None,
                 client_id: str | None = None,
                 client_secret: str | None = None,
                 sports: dict[int, str] | None = None,
                 timeout: float = 20.0,
                 cache_ttl: int = 3600,        # 1 h athlete context cache
                 roster_cache_ttl: int = 1800,  # 30 min sport-roster cache
                 max_workers: int = 10,
                 retries: int = 3,             # extra attempts on 5xx/transport errors
                 retry_backoff: float = 0.5):  # 0.5s, 1s, 2s between attempts
        self.base_url = (base_url or os.environ.get("SAMS_BASE_URL", "")).rstrip("/")
        if not self.base_url:
            raise SamsError("SAMS_BASE_URL not set")
        self.client_id     = client_id     or os.environ["SAMS_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["SAMS_CLIENT_SECRET"]
        self.sports = sports or DEFAULT_SPORTS
        self.retries = max(0, int(retries))
        self.retry_backoff = retry_backoff

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
        """All SAMS traffic is GET (idempotent), so 5xx and transport errors
        retry with exponential backoff — urllib3.Retry semantics, which the
        old per-app requests Sessions used to provide. 4xx never retries."""
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(self.retry_backoff * (2 ** (attempt - 1)))
            try:
                r = self._client.get(path, params=params)
            except httpx.TransportError as e:
                last_err = e
                continue
            if r.status_code >= 500:
                last_err = SamsError(f"SAMS {r.status_code} on {path}: {r.text[:200]}")
                continue
            if r.status_code >= 400:
                raise SamsError(f"SAMS {r.status_code} on {path}: {r.text[:200]}")
            return r.json()
        raise SamsError(
            f"SAMS GET {path} failed after {self.retries + 1} attempts: {last_err}"
        ) from last_err

    # ---- search / lookup ----
    def search(self, q: str) -> list[dict]:
        """Fuzzy search players by name / MRN / partial. Returns raw rows."""
        return self._get("/api/ExternalApps/player/search", params={"q": q}) or []

    def _build_hit(self, row: dict) -> dict | None:
        """Normalise a SAMS player/roster row to the picker shape. None if no playerId."""
        pid = row.get("playerId")
        if pid is None:
            return None
        sid = row.get("sportId")
        return {
            "player_id": int(pid),
            "full_name": (row.get("fullName") or row.get("playerName")
                          or row.get("name") or ""),
            "arabic_name": row.get("arabicName"),
            "mrn": str(row["mrn"]) if row.get("mrn") is not None else None,
            "sport_id": int(sid) if sid is not None else None,
            "sport": self.sports.get(int(sid)) if sid is not None else None,
            "photo_url": resolve_photo_url(row.get("imageUrl") or row.get("profileImageUrl"),
                                           base_url=self.base_url) or None,
            "is_active": row.get("isActive"),
        }

    def search_athletes(self, q: str, *, limit: int = 20,
                        active_only: bool = True) -> list[dict]:
        """Name/MRN search returning the picker shape (player_id, full_name, mrn,
        sport, photo_url). Use this for athlete pickers; ``search`` returns raw rows."""
        resp = self._get("/api/ExternalApps/player/search", params={"q": (q or "").strip()})
        if isinstance(resp, list):
            rows = resp
        elif isinstance(resp, dict) and isinstance(resp.get("items"), list):
            rows = resp["items"]
        else:
            rows = []
        if active_only:
            rows = [r for r in rows if r.get("isActive") is True]
        hits = [h for h in (self._build_hit(r) for r in rows[:limit]) if h]
        return hits

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

    def _build_context(self, row: dict) -> dict:
        sid = row.get("sportId")
        dob = _to_date(row.get("dateOfBirth"))
        return {
            "player_id": int(row["playerId"]),
            "mrn": str(row["mrn"]) if row.get("mrn") is not None else None,
            "full_name": row.get("fullName") or "",
            "arabic_name": row.get("arabicName"),
            "date_of_birth": dob.isoformat() if dob else None,
            "age": _age_from_dob(dob),
            "sex": _flatten(row.get("gender")),
            "sport_id": int(sid) if sid is not None else None,
            "sport": self.sports.get(int(sid)) if sid is not None else None,
            "photo_url": resolve_photo_url(row.get("profileImageUrl") or row.get("imageUrl"),
                                           base_url=self.base_url) or None,
            "is_active": row.get("isActive"),
            "pathway": row.get("pathway"),
            "is_target": (row.get("pathway") == "Target") if row.get("pathway") else None,
        }

    def athlete_card(self, player_id: int) -> dict:
        """Full athlete card in the picker/save shape (player_id, full_name, mrn,
        date_of_birth, age, sex, sport, photo_url) enriched with current
        enrollment (sport, discipline, target_event, coach). Uses the richer
        ``/details`` endpoint. Raises SamsError if the player isn't found.
        """
        pid = int(player_id)
        ck = (pid, "card")
        if ck in self._context_cache:
            return self._context_cache[ck]
        details = self._get(f"/api/ExternalApps/player/{pid}/details")
        if not details:
            raise SamsError(f"player_id {pid} not found in SAMS")
        ctx = self._build_context(details)
        try:
            enr = self.get_current_enrollment(pid)
        except SamsError:
            enr = {}
        if enr:
            sid = enr.get("sportId")
            if sid is not None:
                ctx["sport_id"] = int(sid)
                ctx["sport"] = (enr.get("sportName") or self.sports.get(int(sid))
                                or ctx.get("sport"))
            ctx["discipline_id"] = enr.get("disciplineId")
            ctx["discipline"] = _flatten(enr.get("disciplineName"))
            ctx["target_event"] = first_target_event(enr.get("targetEventNames"))
            ctx["target_event_raw"] = enr.get("targetEventNames")
            ctx["player_type"] = enr.get("playerTypeName")
            ctx["coach_name"] = enr.get("coachName")
        self._context_cache[ck] = ctx
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
    # NOTE: SAMS serves these on the *Search* endpoints (TrainingPlans/Search,
    # TrainingPlanPlayer/Search); the older `/training-plans` paths 404. The plan
    # id is `trainingPlanId` (camelCase) and roster rows carry `playerId`.
    def list_training_plans(self, sport_id: int, training_date: str,
                            committee_id: int | None = None) -> list[dict]:
        key = (int(sport_id), training_date,
               int(committee_id) if committee_id else None)
        if key in self._plans_cache:
            return self._plans_cache[key]
        params: dict = {"SportId": int(sport_id), "TrainingDate": training_date}
        if committee_id:
            params["CommitteeId"] = int(committee_id)
        resp = self._get("/api/ExternalApps/TrainingPlans/Search", params=params) or []
        plans = resp if isinstance(resp, list) else (resp.get("items") or [])
        self._plans_cache[key] = plans
        return plans

    def get_plan_roster(self, training_plan_id: int) -> list[dict]:
        """Roster for one plan, in the picker shape ({player_id, full_name, ...})."""
        key = int(training_plan_id)
        if key in self._roster_cache:
            return self._roster_cache[key]
        resp = self._get("/api/ExternalApps/TrainingPlanPlayer/Search",
                         params={"TrainingPlanId": key}) or []
        rows = resp if isinstance(resp, list) else (resp.get("items") or [])
        out = [h for h in (self._build_hit(r) for r in rows) if h]
        self._roster_cache[key] = out
        return out

    def list_sport_roster(self, sport_id: int, *, committee_id: int | None = None,
                           days_back: int = 60) -> list[dict]:
        """All unique active athletes for a sport over the last N days, in the
        picker shape. Walks the last N days of training plans (SAMS has no
        players-by-sport endpoint), dedupes by player_id. ``committee_id`` scopes
        to a level (1=Federation, 2=Aspire Academy, 3=Pre-Academy, 4=External).
        """
        key = (int(sport_id), int(committee_id) if committee_id else None, int(days_back))
        if key in self._sport_cache:
            return self._sport_cache[key]

        today = date.today()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(days_back)]

        plan_ids: set[int] = set()
        plan_futs = [self._pool.submit(self.list_training_plans,
                                        int(sport_id), d, committee_id) for d in dates]
        for fut in as_completed(plan_futs, timeout=60):
            try:
                plans = fut.result() or []
            except Exception:  # noqa: BLE001
                continue
            for p in plans:
                tpid = p.get("trainingPlanId")
                if tpid is not None:
                    plan_ids.add(int(tpid))

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
