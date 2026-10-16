"""0.25.3: the shared plumbing behind whoop / firstbeat / identifiers / supplements sends
SPORTS_API_KEY as the X-API-Key header. Since 2026-10-12 the Sports API refuses keyless
reads of PII tables (athlete_identifiers), so every WHOOP / Firstbeat summary 401'd at the
id lookup while SportsApi (fixed in 0.25.1) worked."""
from aspire_data import _common


class _Fake:
    def __init__(self):
        self.calls = []

    def get(self, path, **kw):
        self.calls.append(("get", path, kw))
        return "resp"

    def post(self, path, **kw):
        self.calls.append(("post", path, kw))
        return "resp"


def test_get_and_post_send_the_key_header(monkeypatch):
    fake = _Fake()
    monkeypatch.setattr(_common, "sports_client", lambda: fake)
    monkeypatch.setenv("SPORTS_API_KEY", "k-123")
    assert _common.get("/api/v1/table/athlete_identifiers", params={"limit": 1}) == "resp"
    _common.post("/api/x", json={"a": 1})
    assert fake.calls[0][2]["headers"] == {"X-API-Key": "k-123"}
    assert fake.calls[0][2]["params"] == {"limit": 1}
    assert fake.calls[1][2]["headers"] == {"X-API-Key": "k-123"} and fake.calls[1][2]["json"] == {"a": 1}


def test_no_key_sends_no_header(monkeypatch):
    fake = _Fake()
    monkeypatch.setattr(_common, "sports_client", lambda: fake)
    monkeypatch.delenv("SPORTS_API_KEY", raising=False)
    _common.get("/api/v1/table/x")
    assert fake.calls[0][2]["headers"] == {}


def test_key_read_per_call_not_frozen_in_the_cached_client(monkeypatch):
    fake = _Fake()
    monkeypatch.setattr(_common, "sports_client", lambda: fake)
    monkeypatch.setenv("SPORTS_API_KEY", "first")
    _common.get("/a")
    monkeypatch.setenv("SPORTS_API_KEY", "second")
    _common.get("/a")
    assert [c[2]["headers"]["X-API-Key"] for c in fake.calls] == ["first", "second"]


def test_whoop_table_read_carries_the_key(monkeypatch):
    from aspire_data import whoop

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"x": 1}]}
    fake = _Fake()
    fake.get = lambda path, **kw: (fake.calls.append(("get", path, kw)), R())[1]
    monkeypatch.setattr(_common, "sports_client", lambda: fake)
    monkeypatch.setenv("SPORTS_API_KEY", "k-9")
    assert whoop._table("Whoop_Recovery", where="whoop_user_id = 1") == [{"x": 1}]
    assert fake.calls[0][2]["headers"] == {"X-API-Key": "k-9"}
