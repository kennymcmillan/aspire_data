"""aspire_data.pinboard — Connect pins publish/read (mocked, no network)."""
from __future__ import annotations

import aspire_data.pinboard as pb


def test_full_pin_name_prefixes_bare_names(monkeypatch):
    monkeypatch.setattr(pb, "_username", lambda: "Kenny@ASPIRE.QA")
    assert pb.full_pin_name("anthro_records") == "Kenny@ASPIRE.QA/anthro_records"
    assert pb.full_pin_name("Other.User/their_pin") == "Other.User/their_pin"


def test_username_resolved_once_via_connect_api(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return self

        def json(self):
            return {"username": "U@ASPIRE.QA"}

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return FakeResponse()

    monkeypatch.setattr(pb.httpx, "get", fake_get)
    pb._username_cache.clear()
    assert pb._username() == "U@ASPIRE.QA"
    assert pb._username() == "U@ASPIRE.QA"  # second hit served from cache
    assert len(calls) == 1
    assert calls[0][0] == "https://connect.example.com/__api__/v1/user"
    assert calls[0][1]["Authorization"] == "Key stub-connect-key"


def test_publish_and_read(monkeypatch):
    class FakeMeta:
        class version:  # noqa: N801 — mirrors pins' meta.version.version
            version = "20260611T000000Z-abc12"

    class FakeBoard:
        def __init__(self):
            self.writes = []

        def pin_write(self, df, name, **kw):
            self.writes.append((name, kw))
            return FakeMeta()

        def pin_read(self, name, version=None):
            return ("READ", name, version)

    board = FakeBoard()
    monkeypatch.setattr(pb, "pin_board", lambda: board)
    monkeypatch.setattr(pb, "_username", lambda: "U")

    out = pb.publish_dataframe([1, 2, 3], "x", title="T", description="D")
    assert out == {"name": "U/x", "version": "20260611T000000Z-abc12", "rows": 3}
    name, kw = board.writes[0]
    assert name == "U/x"
    assert kw == {"type": "csv", "title": "T", "description": "D"}

    assert pb.read_pin("x") == ("READ", "U/x", None)
    assert pb.read_pin("Other/y", version="v9") == ("READ", "Other/y", "v9")
