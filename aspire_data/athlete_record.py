"""Athlete record reader: everything the Sports API holds on one athlete, fast, for any app.

The API serves, the app shows. The stored record (rebuilt nightly) plus the report card hold the
short summary, the long narrative, every result, context, honours, peers, career, rankings,
bests, physical testing and media mentions. Every dashboard needs the same pull, so it lives here.

How it stays fast
  - the record is read in SECTIONS (`blocks=`), never whole: a senior athlete is ~750 KB whole, the
    page-open core is 35 KB (0.37 s)
  - sections fetch in parallel (one thread each), so the page-open read costs one round trip
  - each section is ttl-cached per (client, id, section), so every page and tab shares it
  - `prefetch()` warms the heavy tabs (results, bests, testing, media) in the background
  - a failed section comes back empty and is listed in `.errors`; it never raises, never caches

USAGE

    from aspire_data.athlete_record import athlete_record
    rec = athlete_record(api, 123)                # core + report, in parallel
    rec.prefetch()                                # warm the rest while the page paints
    rec.short_summary, rec.long_summary, rec.headline_line
    rec.results                                   # every result row, newest first (results_flat)
    rec.context, rec.honours, rec.peers, rec.career
"""
from __future__ import annotations

__all__ = ["AthleteRecord", "athlete_record", "SECTIONS", "PAGE_OPEN"]

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Optional

import httpx

from aspire_data.cache import TTL_HOURLY, ttl_cache

log = logging.getLogger("aspire_data.athlete_record")

# section -> the record `blocks=` it reads. `sport.<key>` is the athlete's own sport block.
# `report` is not a block: it is GET /api/athlete/{id}/report (rings, ladders, trajectory,
# competitors, composed narrative). Verified live 2026-10-11 on all 8 record sports.
SECTIONS: dict[str, Optional[str]] = {
    "core": "card,coverage,classification,sport.classification,sport.context,sport.honours,sport.peers,sport.career,"
            "sport.current_rankings,sport.rankings",
    "results": "sport.results",                     # carries results_flat (sport.results_flat alone 404s as a block)
    "bests": "sport.personal_bests,sport.season_bests",
    "testing": "testing",
    "knowledge": "knowledge",
    "report": None,
}
PAGE_OPEN = ("core", "report")
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="athlete_record")


@ttl_cache(TTL_HOURLY)
def _section(api, aspire_id: int, section: str) -> dict:
    """One section of one athlete. {} on any failure (and {} is never cached)."""
    blocks = SECTIONS[section]
    try:
        if blocks is None:
            return api.get(f"/api/athlete/{int(aspire_id)}/report") or {}
        return api.get(f"/api/records/athlete/{int(aspire_id)}", blocks=blocks, flat="true") or {}
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("athlete %s section %s failed: %s", aspire_id, section, exc)
        return {}


class AthleteRecord:
    """Section-lazy view over one athlete's stored record + report card.

    Properties read the section they need; a section not loaded yet is fetched on first touch
    (and cached), so a tab only pays for what it shows."""

    def __init__(self, api, aspire_id: int):
        self.api, self.aspire_id = api, int(aspire_id)
        self._data: dict[str, dict] = {}
        self.errors: list[str] = []

    # --- loading ------------------------------------------------------------------
    def load(self, sections: Iterable[str] = PAGE_OPEN) -> "AthleteRecord":
        """Fetch these sections in parallel (already-loaded ones are skipped)."""
        todo = [s for s in dict.fromkeys(sections) if s not in self._data]
        unknown = [s for s in todo if s not in SECTIONS]
        if unknown:
            raise ValueError(f"unknown section(s) {unknown}; known: {list(SECTIONS)}")
        for s, body in zip(todo, _POOL.map(lambda s: _section(self.api, self.aspire_id, s), todo)):
            self._data[s] = body
            if not body:
                self.errors.append(s)
        return self

    def prefetch(self, sections: Iterable[str] = ("results", "bests", "testing", "knowledge")) -> None:
        """Warm sections in the background (fills the shared cache; returns at once)."""
        for s in sections:
            _POOL.submit(_section, self.api, self.aspire_id, s)

    def section(self, name: str) -> dict:
        if name not in self._data:
            self.load([name])
        return self._data[name]

    def _payload(self, name: str) -> dict:
        return self.section(name).get("payload") or {}

    def _sport_block(self, name: str) -> dict:
        return self._payload(name).get(self.sport or "") or {}

    # --- who / freshness -----------------------------------------------------------
    @property
    def exists(self) -> bool:
        return bool(self.section("core"))

    @property
    def identity(self) -> dict:
        return self._payload("core").get("identity") or (self.report.get("identity") or {})

    @property
    def sport(self) -> Optional[str]:
        return self.section("core").get("sport") or self.report.get("sport") or self.identity.get("sport")

    @property
    def name(self) -> Optional[str]:
        return self.section("core").get("display_name") or self.identity.get("name")

    @property
    def built_at(self) -> Optional[str]:
        return self.section("core").get("built_at")

    @property
    def staleness_days(self) -> Optional[int]:
        return self.section("core").get("staleness_days")

    @property
    def coverage(self) -> dict:
        return self.section("core").get("coverage") or {}

    # --- summaries -------------------------------------------------------------------
    @property
    def short_summary(self) -> Optional[str]:
        """The stored short summary (written nightly into the record)."""
        return (self.section("core").get("summary") or {}).get("text") or None

    @property
    def short_summary_meta(self) -> dict:
        s = self.section("core").get("summary") or {}
        return {k: s.get(k) for k in ("built_at", "model", "prompt_version")}

    @property
    def headline_line(self) -> Optional[str]:
        """One line: name, age, events, top honour, level, next target."""
        c = self.card
        return c.get("line") or (c.get("headline") or {}).get("line") or None

    @property
    def card(self) -> dict:
        """The slim headline card. A whole-record read nests it under `card`; a `blocks=card`
        read spreads its keys over the payload top level (live 2026-10-11), so take either."""
        p = self._payload("core")
        return p.get("card") or {k: p[k] for k in ("card_version", "headline", "counts", "present") if k in p}

    @property
    def long_summary(self) -> Optional[str]:
        """The long narrative, composed from record fields by the API (report.composed)."""
        return (self.report.get("composed") or {}).get("narrative") or None

    @property
    def claims(self) -> list[dict]:
        """The traced claims behind the long narrative (each names its field)."""
        return (self.report.get("composed") or {}).get("claims") or []

    # --- sport blocks ---------------------------------------------------------------
    context = property(lambda self: self._sport_block("core").get("context") or {})
    honours = property(lambda self: self._sport_block("core").get("honours") or [])
    peers = property(lambda self: self._sport_block("core").get("peers") or {})
    career = property(lambda self: self._sport_block("core").get("career") or {})
    rankings = property(lambda self: self._sport_block("core").get("rankings") or [])
    current_rankings = property(lambda self: self._sport_block("core").get("current_rankings") or [])
    personal_bests = property(lambda self: self._sport_block("bests").get("personal_bests") or [])
    season_bests = property(lambda self: self._sport_block("bests").get("season_bests") or [])

    @property
    def classification(self) -> dict:
        """Tier / evidence classes, once the record build stores them (payload.classification or
        sport.classification). {} until then: apps fall back to their own calculation."""
        return self._payload("core").get("classification") or self._sport_block("core").get("classification") or {}

    @property
    def results(self) -> list[dict]:
        """Every result, one shape (kind = match | placing | fixture), newest first."""
        rows = self._sport_block("results").get("results_flat") or []
        return sorted(rows, key=lambda r: str(r.get("date") or ""), reverse=True)

    @property
    def testing(self) -> dict:
        """Physical testing: anthropometry, skeletal age, field tests, aerobic stage tests."""
        return self._payload("testing").get("testing") or {}

    @property
    def knowledge(self) -> dict:
        """Media mentions (Aspire Instagram + press), id-linked."""
        return self._payload("knowledge").get("knowledge") or {}

    @property
    def report(self) -> dict:
        """The report card: rings, age_group, standards, ladders, competitors, trajectory."""
        return self.section("report")

    def __repr__(self) -> str:
        return f"AthleteRecord({self.aspire_id}, loaded={list(self._data)}, errors={self.errors})"


def athlete_record(api, aspire_id: int, sections: Iterable[str] = PAGE_OPEN) -> AthleteRecord:
    """One athlete, page-open sections fetched in parallel. `api` is a SportsApi."""
    return AthleteRecord(api, aspire_id).load(sections)
