"""aspire_data.feedback — per-app site feedback store + all-apps inbox (no network)."""
from __future__ import annotations

import pandas as pd
import pytest

import aspire_data.feedback as fb
import aspire_data.pinboard as pb


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv("RSTUDIO_PRODUCT", raising=False)
    monkeypatch.delenv("CONNECT_CONTENT_GUID", raising=False)
    return fb.FeedbackStore("Medical Dashboard", local_file=tmp_path / "fb.json")


class FakePins:
    """In-memory stand-in for the Connect pin board (read_pin / publish_dataframe / pin_list)."""

    def __init__(self, monkeypatch, pins=None):
        self.pins = dict(pins or {})
        self.writes = []
        monkeypatch.setattr(pb, "read_pin", self.read)
        monkeypatch.setattr(pb, "publish_dataframe", self.publish)
        monkeypatch.setenv("RSTUDIO_PRODUCT", "CONNECT")

    def read(self, name, version=None):
        if name not in self.pins:
            raise RuntimeError(f"Cannot check version, since pin {name} does not exist")
        return self.pins[name].copy()

    def publish(self, df, name, **kw):
        self.pins[name] = df.copy()
        self.writes.append(name)
        return {"name": name, "version": str(len(self.writes)), "rows": len(df)}

    def pin_list(self):
        return list(self.pins)


def test_pin_name_for():
    assert fb.pin_name_for("Medical Dashboard") == "site_feedback__medical-dashboard"
    assert fb.pin_name_for("strength_rota") == "site_feedback__strength-rota"
    with pytest.raises(ValueError):
        fb.pin_name_for("  ")


def test_add_load_and_status_local(local):
    a = local.add("Current Injuries", "  Bigger photos  ", by="physio", category="Idea", page_path="/")
    b = local.add("", "Something general")
    df = local.load(fresh=True)
    assert list(df["id"]) == [b["id"], a["id"]] or set(df["id"]) == {a["id"], b["id"]}
    row = df.set_index("id").loc[a["id"]]
    assert (row["note"], row["page"], row["category"], row["status"], row["app"]) == \
        ("Bigger photos", "Current Injuries", "Idea", "Open", "Medical Dashboard")
    assert df.set_index("id").loc[b["id"], "page"] == "Whole site"
    assert local.set_status(a["id"], "Done", by="kenny") is True
    assert local.set_status(a["id"], "Done") is False                        # already Done
    assert local.set_status("nope", "Done") is False
    done = fb.FeedbackStore("Medical Dashboard", local_file=local.local_file).load().set_index("id").loc[a["id"]]
    assert (done["status"], done["status_by"]) == ("Done", "kenny") and done["status_on"]
    with pytest.raises(ValueError):
        local.set_status(a["id"], "Finished")
    with pytest.raises(ValueError):
        local.add("Page", "   ")


def test_laptop_with_a_connect_key_never_uses_the_pin(local, monkeypatch):
    monkeypatch.setenv("CONNECT_API_KEY", "k")
    assert local.uses_pin() is False                                        # not on Connect
    monkeypatch.setenv("RSTUDIO_PRODUCT", "CONNECT")
    assert local.uses_pin() is True
    monkeypatch.setenv("ASPIRE_FEEDBACK_LOCAL", "1")
    assert local.uses_pin() is False


def test_pin_writes_are_per_row_ops_replayed_on_a_fresh_read(monkeypatch):
    """Another process changes the pin between our read and our write: nothing it did is lost, and a
    status change touches ONE row (no stale page can revert another admin's work)."""
    pins = FakePins(monkeypatch)
    s = fb.FeedbackStore("App A")
    r1 = s.add("P", "first")
    assert s.flush() and pins.writes == ["site_feedback__app-a"]
    other = pins.pins["site_feedback__app-a"].copy()                        # a second instance adds + closes
    other.loc[len(other)] = {**r1, "id": "zzz", "note": "from elsewhere", "status": "Done"}
    pins.pins["site_feedback__app-a"] = other
    assert s.set_status(r1["id"], "In progress")
    assert s.flush()
    final = pins.pins["site_feedback__app-a"].set_index("id")
    assert final.loc["zzz", "status"] == "Done" and final.loc[r1["id"], "status"] == "In progress"


def test_a_failed_write_is_reported_and_the_worker_survives(monkeypatch):
    pins = FakePins(monkeypatch)
    calls = {"n": 0}

    def flaky(df, name, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connect down")
        return pins.publish(df, name)
    monkeypatch.setattr(pb, "publish_dataframe", flaky)
    s = fb.FeedbackStore("App B")
    s.add("P", "one")
    assert s.flush() and "connect down" in s.last_error
    s.add("P", "two")
    assert s.flush() and s.last_error == ""
    assert len(pins.pins["site_feedback__app-b"]) == 2                      # the retry replays on fresh read


def test_legacy_medical_pin_is_read_until_the_new_pin_exists(monkeypatch):
    old = pd.DataFrame([{"id": "a1", "created": "2026-09-23 11:21", "page": "Weekly", "note": "old note",
                         "done": "True", "done_on": "2026-09-24 09:00", "by": "x"},
                        {"id": "a2", "created": "2026-09-24 08:00", "page": "Ask", "note": "open one",
                         "done": "False", "done_on": "", "by": ""}])
    pins = FakePins(monkeypatch, {"medical_dashboard_feedback": old})
    s = fb.FeedbackStore("medical-dashboard", legacy_pin="medical_dashboard_feedback")
    df = s.load().set_index("id")
    assert df.loc["a1", "status"] == "Done" and df.loc["a2", "status"] == "Open"
    assert df.loc["a1", "created_utc"] == "2026-09-23 08:21"                  # Qatar -> UTC
    s.add("Admin", "new one")
    assert s.flush()
    new = pins.pins["site_feedback__medical-dashboard"]
    assert set(new["note"]) == {"old note", "open one", "new one"}           # history carried over
    assert len(pins.pins["medical_dashboard_feedback"]) == 2                  # legacy pin untouched


def test_read_all_feedback_merges_every_app(monkeypatch):
    a = fb._normalise(pd.DataFrame([{"id": "1", "created_utc": "2026-09-28 08:00", "note": "a", "page": "X"}]), "")
    b = fb._normalise(pd.DataFrame([{"id": "2", "created_utc": "2026-09-28 09:00", "note": "b", "page": "Y"}]), "")
    pins = FakePins(monkeypatch, {"K/site_feedback__medical-dashboard": a, "K/site_feedback__strength-rota": b,
                                  "K/anthro_records": pd.DataFrame({"x": [1]}), "K/site_feedback__broken": None})
    pins.pins["K/site_feedback__broken"] = object()                           # unreadable
    df = fb.read_all_feedback(board=pins)
    assert list(df["app"]) == ["strength-rota", "medical-dashboard"]          # newest first, app from pin
    assert df.attrs["failed"] and "broken" in df.attrs["failed"][0]
    assert fb.read_all_feedback([]).empty
