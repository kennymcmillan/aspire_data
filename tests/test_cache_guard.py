"""aspire_data.cache 0.22.0: data_as_of (fetch-time tracking) + find_live_lru guard."""
import textwrap

from aspire_data import cache as C


def test_fetched_at_and_data_as_of(monkeypatch):
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(C.time, "time", lambda: t["now"])

    @C.ttl_cache(600)
    def a(k):
        return [k]

    @C.ttl_cache(600)
    def b(k):
        return [k]

    assert a.fetched_at(1) is None and C.data_as_of(a, b) is None
    a(1)
    t["now"] += 100
    b(2)
    assert a.fetched_at(1) == 1_000_000.0
    assert C.data_as_of(a, b) == 1_000_000.0      # oldest data on screen
    t["now"] += 550                               # a(1) now 650 s old: expired
    assert a.fetched_at(1) is None
    assert C.data_as_of(a, b) == 1_000_100.0


def test_shared_hit_keeps_original_fetch_time(monkeypatch):
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(C.time, "time", lambda: t["now"])
    store = {}

    class Shared:
        def get(self, k): return store.get(k)
        def set(self, k, v, timeout): store[k] = v
        def delete(self, k): store.pop(k, None)

    @C.ttl_cache(600, shared=Shared())
    def read(k):
        return [k]

    read(1)                                       # worker A fetches at t0
    read.cache_clear()                            # worker B: empty local layer
    t["now"] += 300
    assert read(1) == [1]
    assert read.fetched_at(1) == 1_000_000.0      # not "now": the real fetch time
    t["now"] += 301                               # shared entry older than ttl
    read.cache_clear()
    assert read(1) == [1] and read.fetched_at(1) == t["now"]   # refetched


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")


def test_single_flight_concurrent_cold_callers_fetch_once():
    """0.22.2: N threads asking for the same cold key -> ONE upstream fetch."""
    import threading
    import time as _t
    calls = []

    @C.ttl_cache(600)
    def slow(k):
        calls.append(k)
        _t.sleep(0.2)
        return [k]

    out = []
    ts = [threading.Thread(target=lambda: out.append(slow("a"))) for _ in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert calls == ["a"] and out == [["a"]] * 8
    # different keys still run in parallel (no global serialisation)
    t0 = _t.time()
    ts = [threading.Thread(target=slow, args=(f"k{i}",)) for i in range(4)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert _t.time() - t0 < 0.6


def test_single_flight_empty_result_lets_waiters_retry():
    """A blip (empty) is not cached; the next caller refetches (not pinned)."""
    n = {"c": 0}

    @C.ttl_cache(600)
    def read():
        n["c"] += 1
        return [] if n["c"] == 1 else [1]

    assert read() == [] and read() == [1] and n["c"] == 2


def test_find_live_lru_flags_live_readers_only(tmp_path):
    _write(tmp_path, "data/physio.py", """
        from functools import lru_cache
        from data.sports_api import call

        @lru_cache(maxsize=64)
        def aerobic(sid):
            return call("query_table", table_name="t")

        @lru_cache(maxsize=1)
        def client():
            return 1

        @lru_cache(maxsize=1)
        def layout():
            return json.loads(PATH.read_text())

        @lru_cache(maxsize=4)
        def roster(_b):
            return call("query_table", table_name="r")

        @lru_cache(maxsize=4)  # lru-ok: reads the frozen 2024 export
        def frozen():
            return call("query_table", table_name="old")
    """)
    _write(tmp_path, "tests/test_x.py", """
        from functools import lru_cache
        @lru_cache
        def f():
            return httpx.get("u")
    """)
    _write(tmp_path, "sams_api.py", """
        import functools
        @functools.lru_cache(maxsize=8)
        def injuries():
            return _client().get("/Medical/Search")
    """)
    hits = C.find_live_lru(tmp_path)
    assert any(h.startswith("data/physio.py:") and " aerobic " in h for h in hits)
    assert any(h.startswith("sams_api.py:") and " injuries " in h for h in hits)
    assert len(hits) == 2, hits          # client / bundled file / bucket / lru-ok / tests skipped


def test_find_live_lru_follows_helpers_and_allows_client_factories(tmp_path):
    _write(tmp_path, "physio.py", """
        from functools import lru_cache

        def _raw(sid):
            return call("query_table", table_name="aerobic")

        def _df(sid):
            return _raw(sid)

        @lru_cache(maxsize=64)
        def curve_by_date(sid):
            return _df(sid)

        @lru_cache(maxsize=1)
        def _sams_client():
            from aspire_data.sams import SamsClient
            return SamsClient()
    """)
    hits = C.find_live_lru(tmp_path)
    assert hits == ["physio.py:11 curve_by_date -> _df() -> _raw() -> call("], hits


def test_client_that_fetches_is_not_a_factory(tmp_path):
    """`return SportsApi().tool(...)` fetches data; only a bare constructor is exempt."""
    _write(tmp_path, "d.py", """
        from functools import lru_cache
        @lru_cache
        def squad():
            return SportsApi().tool("x")
    """)
    assert [h.split(" ")[1] for h in C.find_live_lru(tmp_path)] == ["squad"]


def test_find_live_lru_clean_repo(tmp_path):
    _write(tmp_path, "data.py", """
        from aspire_data.cache import ttl_cache, TTL_LIVE
        @ttl_cache(TTL_LIVE)
        def read():
            return call("query_table")
    """)
    assert C.find_live_lru(tmp_path) == []
