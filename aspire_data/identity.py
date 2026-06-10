"""DOB-first athlete name → SAMS player_id resolution.

The lesson from real migrations (DASH_Anthro anthropometry): a long historical
name ("Mohammed Aly Abdelmonem Monsef Noufal") tanks the fuzzy name-score, so the
single best name match is frequently the WRONG person ("Mohammed Ali", a different
younger athlete who shares the common first name), while the correct athlete
("Mohamed Noufal") scores lower. An **exact DOB + a shared distinctive (surname)
token** is decisive where the name-score is not. Common first names
(Mohammed/Ahmed/Ali/Hassan…) carry almost no identity signal.

So `resolve_to_sams` pools BOTH the top name matches AND every roster athlete with
the exact DOB, then scores each candidate on DOB + distinctive-token overlap +
sport agreement.

    from aspire_data.identity import resolve_to_sams
    results = resolve_to_sams([
        {"name": "Mohammed Aly Abdelmonem Monsef Noufal", "dob": "2005-08-14", "sport": "Jumps"},
        {"name": "Anas Meerghani Ali Fadil", "dob": "2004-01-21"},
    ])
    # each result: {"key","name","player_id"(auto only),"verdict","confidence","candidate"}

By default the active SAMS roster is fetched via :class:`aspire_data.sams.SamsClient`;
pass ``roster=[...]`` to match offline / in tests. Verdicts:
  auto    exact DOB + distinctive token (+sport ok), or name≥92 with DOB≤1y  → player_id set
  reject  sport conflict (and DOB not exact), or age gap >2y (wrong person)
  review  worth a human glance — candidate returned, player_id stays None
  no_match no candidate at all
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from rapidfuzz import fuzz, process

# Common first names carry little identity signal — a shared FIRST name alone must
# never auto-link; a shared surname does.
COMMON_FIRST = {
    "mohammed", "mohamed", "mohammad", "muhammad", "ahmed", "ahmad", "ahmat",
    "ali", "abdullah", "abdallah", "abdalla", "abderrahman", "abdelrahman",
    "abdul", "abdel", "abdo", "hassan", "hussein", "hissein", "omar", "oumar",
    "ibrahim", "ismail", "khalid", "khaled", "saleh", "salah", "youssef", "yousef",
    "seid", "said", "seif", "nasser", "sultan", "mahamat", "mahmoud", "yusuf",
}
_DROP_TOK = {"al", "el", "bin", "bint", "ben", "abu", "bou", "ibn", "the", "and"}
_SPORT_CHECKS = [
    ("padel", "Padel"), ("fenc", "Fencing"), ("squash", "Squash"),
    ("swim", "Swimming"), ("table tennis", "Table Tennis"), ("shoot", "Shooting"),
    ("golf", "Golf"), ("gymnast", "Gymnastics"), ("motor", "Motor Sports"),
]
_ATHLETICS = ("athletic", "sprint", "endurance", "jump", "throw", "hurdl", "distance")

ROSTER_NAME_FLOOR = 55   # rapidfuzz floor to even consider a name candidate
_RANK = {"auto": 3, "review": 2, "reject": 1}


def name_tokens(s: str | None) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", (s or "").lower())
            if len(t) >= 3 and t not in _DROP_TOK}


def is_placeholder_dob(dob: str | None) -> bool:
    """Jan-1 dates are almost always placeholders — don't trust as an exact match."""
    return bool(dob) and str(dob)[5:10] == "01-01"


def _norm_sport(s: str | None) -> str | None:
    """Normalise a SAMS/historical sport-or-program label to a real sport, or None
    (program/squad labels carry no sport info → don't penalise)."""
    if not s:
        return None
    t = s.strip().lower()
    for needle, sport in _SPORT_CHECKS:
        if needle in t:
            return sport
    if any(k in t for k in _ATHLETICS):
        return "Athletics"
    return None


def sport_agrees(a: str | None, b: str | None) -> bool | None:
    """True=agree, False=conflict, None=can't tell (a side is a program label)."""
    x, y = _norm_sport(a), _norm_sport(b)
    if x is None or y is None:
        return None
    return x == y


def fetch_roster(client=None) -> list[dict]:
    """Active SAMS roster as [{player_id, full_name, dob, sport, mrn, photo_url}].

    SAMS ignores the search ``?q=`` param and returns the full unfiltered roster,
    so an empty query yields everyone in one call.
    """
    if client is None:
        from .sams import SamsClient
        client = SamsClient()
    resp = client.search("")
    rows = resp.get("items", []) if isinstance(resp, dict) else (resp or [])
    out = []
    for it in rows:
        pid, name = it.get("playerId"), it.get("fullName") or ""
        if pid and name:
            out.append({
                "player_id": int(pid), "full_name": name,
                "dob": it.get("dateOfBirth"), "sport": it.get("currentSportName"),
                "mrn": str(it["mrn"]) if it.get("mrn") is not None else None,
                "photo_url": it.get("profileImageUrl"),
            })
    return out


def match_pairs(pairs: list[tuple[str, str]], *, base_url: str | None = None,
                client=None) -> dict[tuple[str, str], int]:
    """Score (name_a, name_b) pairs with the Sports API `:8080` match engine.

    The engine is 8-layer (exact / normalised / alias / phonetic / Jaro-Winkler …)
    and handles Arabic transliteration variants (Mohamed↔Muhammad, Osama↔Ousame)
    far better than a bare token ratio. Returns ``{(a, b): score}`` (0–100), chunked
    at 100 pairs/request. URL = ``{SPORTS_API_URL}/api/service/match/batch`` (env, no
    hardcoded host). Raises if SPORTS_API_URL is unset and no base_url is passed.
    """
    import httpx
    base = (base_url or os.environ.get("SPORTS_API_URL") or "").rstrip("/")
    if not base:
        raise ValueError("SPORTS_API_URL not set — pass base_url= to use the match API")
    url = f"{base}/api/service/match/batch"
    out: dict[tuple[str, str], int] = {}
    owns = client is None
    client = client or httpx.Client(timeout=120)
    try:
        for i in range(0, len(pairs), 100):
            chunk = pairs[i:i + 100]
            r = client.post(url, json={"pairs": [{"name_a": a, "name_b": b} for a, b in chunk]})
            r.raise_for_status()
            data = r.json()
            for res in (data.get("results") if isinstance(data, dict) else data) or []:
                out[(res["name_a"], res["name_b"])] = int(res.get("score") or 0)
    finally:
        if owns:
            client.close()
    return out


def resolve_to_sams(athletes: list[dict], *, roster: list[dict] | None = None,
                    client=None, use_match_api: bool | None = None,
                    match_base_url: str | None = None) -> list[dict]:
    """Resolve historical athletes to SAMS player_ids, DOB-first.

    athletes: list of ``{"name": str, "dob"?: "YYYY-MM-DD", "sport"?: str, "key"?: str}``.
    Returns one result dict per input (same order):
        {key, name, player_id, verdict, confidence, candidate}
    where ``player_id`` is set only for an ``auto`` verdict, and ``candidate`` is the
    best SAMS row considered (for human review) or None.

    Scoring: rapidfuzz does cheap local BLOCKING (the candidate pool); the **`:8080`
    match engine is the authoritative name scorer** (better on Arabic transliteration
    variants — bare token-ratio is what misses long-name matches). ``use_match_api``
    None = use it when SPORTS_API_URL / match_base_url is configured (falling back to
    rapidfuzz if the call fails); True = require it; False = rapidfuzz only (offline).
    """
    roster = roster if roster is not None else fetch_roster(client)
    roster_names = [r["full_name"] for r in roster]
    by_name = {r["full_name"]: r for r in roster}
    dob_index: dict[str, list[dict]] = defaultdict(list)
    for r in roster:
        if r.get("dob"):
            dob_index[str(r["dob"])[:10]].append(r)

    # phase 1 — candidate pool per athlete: rapidfuzz blocking + every exact-DOB athlete
    pools: list[tuple[dict, dict[str, dict]]] = []
    for a in athletes:
        name = a["name"]
        a_dob = str(a["dob"])[:10] if a.get("dob") else None
        pool: dict[str, dict] = {}
        for rn, _sc, _i in process.extract(name, roster_names,
                                           scorer=fuzz.token_set_ratio,
                                           limit=4, score_cutoff=ROSTER_NAME_FLOOR):
            pool[rn] = by_name[rn]
        if a_dob and not is_placeholder_dob(a_dob):
            for r in dob_index.get(a_dob, []):
                pool[r["full_name"]] = r
        pools.append((a, pool))

    # phase 2 — authoritative name scores from the :8080 engine (default when configured)
    want_api = (use_match_api if use_match_api is not None
                else bool(match_base_url or os.environ.get("SPORTS_API_URL")))
    api_score: dict[tuple[str, str], int] = {}
    if want_api:
        all_pairs = [(a["name"], rn) for a, pool in pools for rn in pool]
        try:
            api_score = match_pairs(all_pairs, base_url=match_base_url)
        except Exception:               # noqa: BLE001 — graceful fallback to rapidfuzz
            if use_match_api is True:
                raise
            api_score = {}

    def name_score(a_name: str, rn: str) -> int:
        s = api_score.get((a_name, rn))
        return s if s is not None else int(fuzz.token_set_ratio(a_name, rn))

    # phase 3 — verdict per athlete
    results = []
    for a, pool in pools:
        name = a["name"]
        key = a.get("key") or name
        a_dob = str(a["dob"])[:10] if a.get("dob") else None
        a_tokens = name_tokens(name)
        a_sport = a.get("sport")

        best = None
        for rn, r in pool.items():
            ns = name_score(name, rn)
            shared = a_tokens & name_tokens(rn)
            distinct = {t for t in shared if t not in COMMON_FIRST}
            s_dob = str(r["dob"])[:10] if r.get("dob") else None
            dob_exact = bool(a_dob and s_dob and a_dob == s_dob
                             and not is_placeholder_dob(a_dob))
            dob_gap = (abs(int(s_dob[:4]) - int(a_dob[:4]))
                       if (a_dob and s_dob) else None)
            sp_ok = sport_agrees(a_sport, r.get("sport"))

            if sp_ok is False and not dob_exact:
                verdict = "reject"
            elif dob_exact and sp_ok is not False and (distinct or ns >= 85):
                verdict = "auto"
            elif ns >= 92 and (dob_gap is None or dob_gap <= 1) and sp_ok is not False:
                verdict = "auto"
            elif ns >= 88 and dob_gap == 0 and sp_ok is not False:
                verdict = "auto"
            elif dob_gap is not None and dob_gap > 2:
                verdict = "reject"
            elif dob_exact or ns >= 80 or (distinct and (dob_gap or 0) <= 1):
                verdict = "review"
            else:
                verdict = "reject"

            conf = min(100, ns + (18 if dob_exact else 0) + (6 if distinct else 0))
            cand = {
                "player_id": r["player_id"], "sams_name": rn, "sams_dob": s_dob,
                "sams_sport": r.get("sport"), "name_score": ns, "dob_exact": dob_exact,
                "dob_gap": dob_gap, "shared_tokens": " ".join(sorted(shared)),
                "sport_ok": sp_ok, "mrn": r.get("mrn"), "photo_url": r.get("photo_url"),
                "verdict": verdict, "confidence": conf, "_rank": _RANK[verdict],
            }
            if best is None or (cand["_rank"], cand["confidence"]) > (best["_rank"], best["confidence"]):
                best = cand

        if best is None:
            results.append({"key": key, "name": name, "player_id": None,
                            "verdict": "no_match", "confidence": 0, "candidate": None})
        else:
            candidate = {k: v for k, v in best.items() if k != "_rank"}
            results.append({
                "key": key, "name": name,
                "player_id": best["player_id"] if best["verdict"] == "auto" else None,
                "verdict": best["verdict"], "confidence": best["confidence"],
                "candidate": candidate,
            })
    return results
