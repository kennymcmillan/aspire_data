"""Site feedback store — one Connect pin per app, one inbox across all of them.

Every Aspire Dash app gets the same "Feedback" button (aspire_dash.site_feedback); this module is its
storage. Each app writes its OWN pin, ``site_feedback__<app>`` (pins are whole-file, last-write-wins:
one shared pin written by many apps would drop notes). ``read_all_feedback()`` merges every
``site_feedback__*`` pin into one table, so there is still ONE place to see every request.

USAGE
=====

    from aspire_data.feedback import FeedbackStore, read_all_feedback

    store = FeedbackStore("medical-dashboard")         # pin site_feedback__medical-dashboard
    store.add("Current Injuries", "Make the photo bigger", by="physio@aspire.qa", category="Idea")
    store.set_status(row_id, "Done", by="kenneth.mcmillan@aspire.qa")
    df = store.load()                                  # newest first
    inbox = read_all_feedback()                        # every app, one DataFrame

SPEED: a pin read is 2-5 s and a write publishes a new Connect version, so the store keeps an
in-memory copy (instant UI) and a single background thread writes. Every change is queued as an
OPERATION on one row id and replayed on a FRESH read of the pin before writing, so a change made in
another process (or another app instance) is never overwritten, and an old browser tab can never undo
someone else's change (the old "save every row as this page saw it" tick did exactly that).

WHERE: the pin ONLY when running on Connect (or ``force_pin=True``) with CONNECT_API_KEY set. A laptop
with a Connect key must never write test notes into the real pin (it did once, 2026-09-23); off
Connect it is a local JSON file. No athlete data belongs here: staff notes about the app.

Requires the optional ``pins`` package (``pip install aspire_data[pins]``) for the pin path only.
"""
from __future__ import annotations

__all__ = ["COLUMNS", "STATUSES", "FeedbackStore", "pin_name_for", "read_all_feedback", "on_connect"]

import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PIN_PREFIX = "site_feedback__"
COLUMNS = ["id", "created_utc", "app", "page", "page_path", "category", "note",
           "status", "status_on", "status_by", "by"]
STATUSES = ("Open", "In progress", "Done", "Won't do")
_QATAR = timezone(timedelta(hours=3))
_STAMP = "%Y-%m-%d %H:%M"


def on_connect() -> bool:
    return os.environ.get("RSTUDIO_PRODUCT") == "CONNECT" or bool(os.environ.get("CONNECT_CONTENT_GUID"))


def pin_name_for(app: str) -> str:
    """'Medical Dashboard' -> 'site_feedback__medical-dashboard'."""
    slug = re.sub(r"[^a-z0-9]+", "-", app.lower()).strip("-")
    if not slug:
        raise ValueError("app name is empty")
    return PIN_PREFIX + slug


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime(_STAMP)


def _missing(e: Exception) -> bool:
    return any(t in str(e).lower() for t in ("does not exist", "not found", "404"))


def _normalise(df, app: str):
    """Any stored shape -> COLUMNS, newest first. Also reads the first medical-dashboard layout
    (created in Qatar time, done bool, done_on) so an app can move onto this store with its history."""
    import pandas as pd
    df = pd.DataFrame(df).copy()
    if "status" not in df.columns and "done" in df.columns:
        done = df["done"].astype(str).str.lower().isin(["true", "1"])
        df["status"] = done.map({True: "Done", False: "Open"})
        df["status_on"] = df.get("done_on", "")
    if "created_utc" not in df.columns and "created" in df.columns:
        def to_utc(s):
            try:
                return (datetime.strptime(str(s), _STAMP) - timedelta(hours=3)).strftime(_STAMP)
            except ValueError:
                return str(s or "")
        df["created_utc"] = df["created"].map(to_utc)
    df = df.reindex(columns=COLUMNS).fillna("").astype(str)
    df.loc[df["app"] == "", "app"] = app
    df.loc[~df["status"].isin(STATUSES), "status"] = "Open"
    return df.sort_values("created_utc", ascending=False, kind="stable").reset_index(drop=True)


class FeedbackStore:
    """One app's feedback. Thread-safe; one writer thread per store."""

    CACHE_TTL_S = 60.0

    def __init__(self, app: str, *, pin_name: str | None = None, legacy_pin: str | None = None,
                 local_file: str | Path | None = None, force_pin: bool | None = None):
        self.app = app
        self.pin_name = pin_name or pin_name_for(app)
        self.legacy_pin = legacy_pin              # read ONLY while the new pin does not exist yet
        self.local_file = Path(local_file) if local_file else Path.cwd() / f"{self.pin_name}.local.json"
        self.force_pin = force_pin
        self._lock = threading.RLock()
        self._cache = {"df": None, "t": 0.0}
        self._ops: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._failed: list = []                   # changes whose write failed: retried with the next one
        self.last_error = ""

    # ── where ──────────────────────────────────────────────────────────────────────────────────
    def uses_pin(self) -> bool:
        if os.environ.get("ASPIRE_FEEDBACK_LOCAL") == "1":
            return False
        wanted = on_connect() if self.force_pin is None else self.force_pin
        return bool(wanted and os.environ.get("CONNECT_API_KEY"))

    def _read(self):
        """Fresh read: pin (slow) or local file."""
        import pandas as pd
        if not self.uses_pin():
            raw = json.loads(self.local_file.read_text(encoding="utf-8")) if self.local_file.exists() else []
            return _normalise(pd.DataFrame(raw), self.app)
        from .pinboard import read_pin
        for name in (self.pin_name, self.legacy_pin):
            if not name:
                continue
            try:
                return _normalise(read_pin(name), self.app)
            except Exception as e:  # noqa: BLE001  first run: the pin does not exist yet
                if not _missing(e):
                    raise
        return _normalise(pd.DataFrame(), self.app)

    def _write(self, df) -> None:
        df = df[COLUMNS]
        if not self.uses_pin():
            self.local_file.write_text(json.dumps(df.to_dict("records"), indent=1), encoding="utf-8")
            return
        from .pinboard import publish_dataframe
        publish_dataframe(df, self.pin_name, title=f"Site feedback: {self.app}",
                          description=f"Staff requests for the {self.app} app (aspire_data.feedback).")

    # ── operations (replayed on a fresh read by the writer) ─────────────────────────────────────
    @staticmethod
    def _apply(df, op):
        import pandas as pd
        kind, row = op
        if kind == "add":
            if row["id"] in set(df["id"]):
                return df
            return pd.concat([pd.DataFrame([row]), df], ignore_index=True)[COLUMNS]
        if kind == "status":                     # row: {id, status, status_on, status_by}
            df = df.copy()
            hit = df.index[df["id"] == row["id"]]
            for i in hit:
                df.loc[i, ["status", "status_on", "status_by"]] = [row["status"], row["status_on"],
                                                                   row["status_by"]]
            return df
        return df

    def _worker(self):
        while True:
            op = self._ops.get()
            batch = self._failed + [op]            # a failed change is replayed first, never dropped
            try:
                with self._lock:
                    df = self._read()
                    for o in batch:
                        df = self._apply(df, o)
                    self._write(df)
                    self._cache["df"], self._cache["t"] = df.copy(), time.time()
                self._failed, self.last_error = [], ""
            except Exception as e:  # noqa: BLE001  keep the worker alive; the page shows last_error
                self._failed = batch
                self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
                log.exception("site feedback pin write failed (%s)", self.pin_name)
            finally:
                self._ops.task_done()

    def _commit(self, op) -> None:
        """Instant in memory; saved now (local file) or by the writer thread (pin)."""
        with self._lock:
            base = self._cache["df"] if self._cache["df"] is not None else self._read()
            self._cache["df"], self._cache["t"] = self._apply(base, op), time.time()
            if not self.uses_pin():
                self._write(self._cache["df"])
                return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name=f"feedback-{self.pin_name}",
                                            daemon=True)
            self._thread.start()
        self._ops.put(op)

    # ── public ─────────────────────────────────────────────────────────────────────────────────
    def load(self, fresh: bool = False):
        """Every request, newest first. Cached; re-read at most every 60 s (or fresh=True), never
        while writes are queued (the in-memory copy is newer than the pin then)."""
        with self._lock:
            stale = time.time() - self._cache["t"] > self.CACHE_TTL_S
            if self._cache["df"] is None or ((fresh or stale) and self.pending() == 0):
                self._cache["df"], self._cache["t"] = self._read(), time.time()
            return self._cache["df"].copy()

    def pending(self) -> int:
        """Changes not yet written to the pin (queued, or failed and waiting for the next change)."""
        return self._ops.unfinished_tasks + len(self._failed)

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait for queued writes (tests, shutdown). True when everything is saved."""
        end = time.time() + timeout
        while self._ops.unfinished_tasks and time.time() < end:
            time.sleep(0.05)
        return self._ops.unfinished_tasks == 0

    def add(self, page: str, note: str, *, by: str = "", category: str = "", page_path: str = "") -> dict:
        note = (note or "").strip()
        if not note:
            raise ValueError("Write what should change first.")
        row = {"id": uuid.uuid4().hex[:10], "created_utc": _now_utc(), "app": self.app,
               "page": (page or "").strip() or "Whole site", "page_path": page_path or "",
               "category": category or "", "note": note, "status": "Open", "status_on": "",
               "status_by": "", "by": by or ""}
        self._commit(("add", row))
        return row

    def set_status(self, row_id: str, status: str, *, by: str = "") -> bool:
        """Change ONE request's status. False when the id is unknown or it already has that status."""
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        df = self.load()
        hit = df[df["id"] == row_id]
        if hit.empty or hit.iloc[0]["status"] == status:
            return False
        self._commit(("status", {"id": row_id, "status": status,
                                 "status_on": _now_utc() if status != "Open" else "", "status_by": by or ""}))
        return True


def read_all_feedback(pin_names: list[str] | None = None, *, board=None):
    """Every app's requests in one DataFrame (the inbox), newest first. By default every pin whose
    name holds ``site_feedback__`` that the API key can see. A pin that fails to read is skipped
    and named in ``df.attrs['failed']`` rather than breaking the inbox."""
    import pandas as pd
    from .pinboard import pin_board, read_pin
    if pin_names is None:
        board = board or pin_board()
        pin_names = [n for n in board.pin_list() if PIN_PREFIX in n]
    frames, failed = [], []
    for name in pin_names:
        app = name.split("/")[-1][len(PIN_PREFIX):] if PIN_PREFIX in name else name
        try:
            frames.append(_normalise(read_pin(name), app))
        except Exception as e:  # noqa: BLE001
            failed.append(f"{name}: {type(e).__name__}")
    df = _normalise(pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), "")
    df.attrs["failed"] = failed
    return df
