"""aspire_data.cache.ttl_cache: expires in every layer, never pins empties,
DataFrame-safe, per-entry invalidate (2026-09-25)."""
import pandas as pd
import pytest

from aspire_data import cache as C


class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(C.time, "time", c)
    return c


class _Shared:
    """flask_caching-shaped store that honours its own timeout."""
    def __init__(self, clock):
        self.d, self.clock = {}, clock

    def get(self, k):
        v = self.d.get(k)
        if v is None or self.clock() >= v[1]:
            return None
        return v[0]

    def set(self, k, v, timeout):
        self.d[k] = (v, self.clock() + timeout)

    def delete(self, k):
        self.d.pop(k, None)


def test_new_data_shows_after_ttl(clock):
    """The lru_cache bug: a new test saved upstream must appear after the TTL."""
    src = {"rows": [1]}

    @C.ttl_cache(60)
    def read(sid):
        return list(src["rows"])

    assert read("a") == [1]
    src["rows"] = [1, 2]
    clock.t += 30
    assert read("a") == [1]            # within TTL: cached
    clock.t += 31
    assert read("a") == [1, 2]         # expired: refetched


def test_shared_layer_also_expires(clock):
    shared = _Shared(clock)
    src = {"v": {"n": 1}}

    @C.ttl_cache(60, shared=shared)
    def read(sid):
        return dict(src["v"])

    assert read(1) == {"n": 1}
    read.cache_clear()                  # new worker: local empty, shared warm
    src["v"] = {"n": 2}
    assert read(1) == {"n": 1}          # served from shared
    clock.t += 61
    read.cache_clear()
    assert read(1) == {"n": 2}          # both layers expired


@pytest.mark.parametrize("empty", [[], {}, None, (), pd.DataFrame(),
                                   {"a": [], "b": []}, {"x": pd.DataFrame()}])
def test_empty_is_never_pinned(clock, empty):
    calls = {"n": 0}

    @C.ttl_cache(60)
    def read():
        calls["n"] += 1
        return empty if calls["n"] == 1 else [1]

    assert not C.has_data(read())
    assert read() == [1]                # blip retried, not pinned for the TTL
    assert calls["n"] == 2


def test_dataframes_and_dict_of_dataframes_cache(clock):
    calls = {"n": 0}
    df = pd.DataFrame({"a": [1]})

    @C.ttl_cache(60)
    def read():
        calls["n"] += 1
        return {"2026-09-24": df}

    read(); read()
    assert calls["n"] == 1


def test_false_and_zero_are_real_answers(clock):
    calls = {"n": 0}

    @C.ttl_cache(60)
    def read():
        calls["n"] += 1
        return 0

    read(); read()
    assert calls["n"] == 1


def test_invalidate_one_entry(clock):
    src = {"a": 1, "b": 1}

    @C.ttl_cache(600)
    def read(k):
        return [src[k]]

    read("a"); read("b")
    src["a"], src["b"] = 2, 2
    read.invalidate("a")                # Refresh button for athlete "a"
    assert read("a") == [2]
    assert read("b") == [1]             # untouched


def test_kwargs_key_and_clear_all(clock):
    calls = {"n": 0}

    @C.ttl_cache(600)
    def read(pid, days=28):
        calls["n"] += 1
        return [pid, days]

    read(1, days=7); read(1, days=7); read(1, days=28)
    assert calls["n"] == 2
    C.clear_all()
    read(1, days=7)
    assert calls["n"] == 3


def test_skip_empty_false_caches_empties(clock):
    calls = {"n": 0}

    @C.ttl_cache(60, skip_empty=False)
    def read():
        calls["n"] += 1
        return []

    read(); read()
    assert calls["n"] == 1


def test_maxsize_evicts_oldest(clock):
    calls = []

    @C.ttl_cache(600, maxsize=2)
    def read(k):
        calls.append(k)
        return [k]

    read(1); clock.t += 1; read(2); clock.t += 1; read(3)   # evicts 1 (oldest)
    read(2); read(3)
    assert calls == [1, 2, 3]            # 2 and 3 still cached
    read(1)
    assert calls == [1, 2, 3, 1]         # 1 was evicted, refetched


def test_broken_shared_layer_does_not_break_reads(clock):
    class Boom:
        def get(self, k): raise RuntimeError("redis down")
        def set(self, k, v, timeout): raise RuntimeError("redis down")
        def delete(self, k): raise RuntimeError("redis down")

    @C.ttl_cache(60, shared=Boom())
    def read():
        return [1]

    assert read() == [1]
    read.invalidate()


def test_bad_ttl_rejected():
    with pytest.raises(ValueError):
        C.ttl_cache(0)
