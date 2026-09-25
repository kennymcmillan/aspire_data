"""Expiring function cache for app data readers: the ONE way to cache live data.

Why this exists (2026-09-25): apps cached SAMS / Oracle / Sports API readers with
``@functools.lru_cache``. lru_cache never expires, so a long-lived Posit Connect
worker served its startup snapshot forever: new Vyntus aerobic tests, anthro
tests and Firstbeat sessions were in Oracle but hidden until a restart. Stacking
``@cache.memoize(timeout)`` on top did not help, because the inner lru_cache
answered first. Each app had also hand-rolled its own fix (endurance
``memoize_nonempty``, development ``ttl_cache``).

Rules baked in:
  * Every entry expires after ``ttl`` seconds, in EVERY layer.
  * An empty result (``[]``, ``{}``, ``None``, empty DataFrame, dict whose values
    are all empty) is never stored, so a transient upstream blip retries on the
    next call instead of blanking a page for the whole TTL.
  * Optional cross-worker layer: pass any object with ``get(key)`` /
    ``set(key, value, timeout=)`` / ``delete(key)`` (a flask_caching ``Cache``).
  * ``fn.invalidate(*args)`` drops one entry (a Refresh button for the athlete on
    screen); ``fn.cache_clear()`` drops all; ``clear_all()`` clears every wrapped fn.

    from aspire_data.cache import ttl_cache, TTL_LIVE

    @ttl_cache(TTL_LIVE)
    def aerobic_tests(sams_id: str): ...

Plain ``lru_cache`` stays fine ONLY for things that cannot go stale: client /
connection objects and files bundled with the app.
"""
from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable

__all__ = ["ttl_cache", "clear_all", "has_data",
           "TTL_LIVE", "TTL_HOURLY", "TTL_DAILY"]

TTL_LIVE = 900       # 15 min: test data, sessions, rosters (someone may have just saved)
TTL_HOURLY = 3600    # 1 h: slow-moving (anthro, benchmarks, standards pins)
TTL_DAILY = 86400    # 24 h: nightly-refreshed populations / reference sets

_REGISTRY: list[Callable] = []
_MISSING = object()


def has_data(value: Any) -> bool:
    """True iff ``value`` carries something worth caching.

    Falsy containers and None are a miss. A pandas DataFrame / Series counts by
    ``.empty`` (its truth value is ambiguous). A dict counts only if at least one
    value has data, which catches the bulk-reader trap ``{id: [] for id in ids}``
    and a dict of empty DataFrames. ``False`` / ``0`` are real answers and cache.
    """
    if value is None:
        return False
    empty = getattr(value, "empty", None)
    if isinstance(empty, bool) and not isinstance(value, (dict, list, tuple, set, str)):
        return not empty
    if isinstance(value, dict):
        return any(has_data(v) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset, str, bytes)):
        return len(value) > 0
    return True


def ttl_cache(ttl: int = TTL_LIVE, *, shared: Any = None, skip_empty: bool = True,
              maxsize: int = 512):
    """Decorator: cache a function's result per argument set for ``ttl`` seconds.

    ``shared``: optional cross-worker store (flask_caching ``Cache`` or anything
    with get/set/delete). ``skip_empty=False`` caches empty results too (for
    readers where empty is a legitimate, stable answer). ``maxsize`` bounds the
    per-process layer; the oldest entry is evicted first. Args must be hashable.
    """
    if ttl <= 0:
        raise ValueError("ttl must be > 0 seconds")

    def deco(fn: Callable) -> Callable:
        prefix = f"aspire_data.cache:{fn.__module__}.{fn.__qualname__}:"
        local: dict[tuple, tuple[float, Any]] = {}
        lock = threading.Lock()

        def _key(args, kwargs):
            return args + tuple(sorted(kwargs.items())) if kwargs else args

        def _keep(value) -> bool:
            return has_data(value) if skip_empty else True

        @functools.wraps(fn)
        def wrap(*args, **kwargs):
            k = _key(args, kwargs)
            now = time.time()
            with lock:
                box = local.get(k)
            if box is not None and now - box[0] < ttl:
                return box[1]
            if shared is not None:
                try:
                    hit = shared.get(prefix + repr(k))
                except Exception:  # noqa: BLE001 - a broken shared layer must not break reads
                    hit = None
                if hit is not None and _keep(hit):
                    with lock:
                        local[k] = (now, hit)
                    return hit
            value = fn(*args, **kwargs)
            if _keep(value):
                with lock:
                    if len(local) >= maxsize and k not in local:
                        local.pop(min(local, key=lambda kk: local[kk][0]), None)
                    local[k] = (now, value)
                if shared is not None:
                    try:
                        shared.set(prefix + repr(k), value, timeout=ttl)
                    except Exception:  # noqa: BLE001
                        pass
            return value

        def invalidate(*args, **kwargs):
            """Drop one entry (both layers) so the next call refetches."""
            k = _key(args, kwargs)
            with lock:
                local.pop(k, None)
            if shared is not None:
                try:
                    shared.delete(prefix + repr(k))
                except Exception:  # noqa: BLE001
                    pass

        def cache_clear():
            """Drop every per-process entry (the shared layer expires on its TTL,
            or clear it with the backend's own clear())."""
            with lock:
                local.clear()

        wrap.invalidate = invalidate
        wrap.cache_clear = cache_clear
        wrap.ttl = ttl
        _REGISTRY.append(wrap)
        return wrap

    return deco


def clear_all() -> None:
    """Clear every ttl_cache in the process (tests, an app-wide Refresh)."""
    for fn in _REGISTRY:
        fn.cache_clear()
