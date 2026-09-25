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

__all__ = ["ttl_cache", "clear_all", "has_data", "data_as_of", "find_live_lru",
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
        # v2: the shared layer stores (fetched_at, value) so every worker knows the
        # real fetch time ("data as of"); a new prefix never reads 0.21.0 entries.
        prefix = f"aspire_data.cache.v2:{fn.__module__}.{fn.__qualname__}:"
        local: dict[tuple, tuple[float, Any, float]] = {}   # key -> (stored_at, value, fetched_at)
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
            if box is not None and now - box[2] < ttl:
                return box[1]
            if shared is not None:
                try:
                    hit = shared.get(prefix + repr(k))
                except Exception:  # noqa: BLE001 - a broken shared layer must not break reads
                    hit = None
                if (isinstance(hit, tuple) and len(hit) == 2 and _keep(hit[1])
                        and now - hit[0] < ttl):
                    with lock:
                        local[k] = (now, hit[1], hit[0])
                    return hit[1]
            value = fn(*args, **kwargs)
            if _keep(value):
                with lock:
                    if len(local) >= maxsize and k not in local:
                        local.pop(min(local, key=lambda kk: local[kk][0]), None)
                    local[k] = (now, value, now)
                if shared is not None:
                    try:
                        shared.set(prefix + repr(k), (now, value), timeout=ttl)
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

        def fetched_at(*args, **kwargs):
            """Epoch seconds the cached value for these args was fetched upstream,
            or None if nothing valid is cached."""
            box = local.get(_key(args, kwargs))
            return box[2] if box is not None and time.time() - box[2] < ttl else None

        def _oldest_valid():
            now = time.time()
            with lock:
                ts = [b[2] for b in local.values() if now - b[2] < ttl]
            return min(ts) if ts else None

        wrap.invalidate = invalidate
        wrap.fetched_at = fetched_at
        wrap._oldest_valid = _oldest_valid
        wrap.cache_clear = cache_clear
        wrap.ttl = ttl
        _REGISTRY.append(wrap)
        return wrap

    return deco


def clear_all() -> None:
    """Clear every ttl_cache in the process (tests, an app-wide Refresh)."""
    for fn in _REGISTRY:
        fn.cache_clear()


def data_as_of(*fns: Callable) -> float | None:
    """Oldest fetch time (epoch seconds) among the still-valid cached entries of
    ``fns`` (default: every ttl_cache in the process). It is the honest "data as
    of" for a page: nothing shown is older than this. None when nothing is cached."""
    ts = [t for f in (fns or tuple(_REGISTRY))
          if (t := getattr(f, "_oldest_valid", lambda: None)()) is not None]
    return min(ts) if ts else None


# --- guard: find lru_cache on live-data readers ---------------------------------
# A static scan an app runs as a one-line test, so the never-expiring-cache bug
# cannot come back. Heuristic by design: it flags an @lru_cache / @functools.cache
# whose function body touches a live source. Silence a deliberate case with a
# comment on the decorator or def line:  # lru-ok: <reason>

import ast as _ast
import re as _re
from pathlib import Path as _Path

_LIVE = _re.compile(
    r"SportsApi|SamsClient|sams_client|sports_api|httpx\.|requests\.|urlopen|"
    r"query_table|execute_read_sql|read_pin|pin_read|board\.|hana|cursor\(|\.execute\(|"
    r"read_sql|mysql|oracle|motherduck|duckdb\.connect|\bcall\(|api\(\)|_api\(\)\.|"
    r"\.search\(|/api/|TrainingPlan|Players/|Medical|summary\(player_id")
_LRU = {"lru_cache", "cache"}
# A no-arg function that just builds/returns a client (SamsClient(), SportsApi(),
# a MultiFernet, a connection pool) caches an object, not data: allowed.
_CLIENT_FACTORY = _re.compile(r"return\s+\w*(?:Client|Api|API|Fernet|Pool|pool|Session|Engine)\([^()]*\)\s*$", _re.M)
_SKIP_DIRS = {".venv", "venv", "site-packages", "node_modules", "__pycache__", ".git",
              "build", "dist", "tests", ".claude", "_vendor", "scripts"}


def _is_lru(dec) -> bool:
    node = dec.func if isinstance(dec, _ast.Call) else dec
    name = node.attr if isinstance(node, _ast.Attribute) else getattr(node, "id", "")
    return name in _LRU


def find_live_lru(root) -> list[str]:
    """Return ``"path:line func -> marker"`` for every lru_cache on a function that
    reads live data under ``root`` (tests/, venvs, vendored code skipped).
    Functions taking a time-bucket arg (``_b`` / ``_bucket``) already roll over and
    are allowed; so are clients and bundled-file readers (no live marker)."""
    root = _Path(root)
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if _SKIP_DIRS & set(path.relative_to(root).parts[:-1]):
            continue
        try:
            src = path.read_text(encoding="utf-8")
            tree = _ast.parse(src)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        lines = src.splitlines()
        funcs = {n.name: n for n in _ast.walk(tree)
                 if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}

        def _body(fn) -> str:
            seg = _ast.get_source_segment(src, fn) or ""
            return seg.split(":", 1)[-1]          # drop the signature line

        def _live_marker(fn, depth=0, seen=None):
            """Marker in fn's body, or in a same-module helper it calls (2 hops),
            e.g. aerobic() -> _df() -> call("query_table")."""
            seen = seen if seen is not None else set()
            if fn.name in seen:
                return None
            seen.add(fn.name)
            m = _LIVE.search(_body(fn))
            if m:
                return m.group(0)
            if depth >= 2:
                return None
            for c in _ast.walk(fn):
                if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name) \
                        and c.func.id in funcs and c.func.id != fn.name:
                    hit = _live_marker(funcs[c.func.id], depth + 1, seen)
                    if hit:
                        return f"{c.func.id}() -> {hit}"
            return None

        for node in funcs.values():
            decs = [d for d in node.decorator_list if _is_lru(d)]
            if not decs:
                continue
            span = lines[decs[0].lineno - 1: node.lineno]
            if any("lru-ok" in ln for ln in span):
                continue
            argnames = {a.arg for a in node.args.args + node.args.kwonlyargs}
            if argnames & {"_b", "_bucket", "bucket"}:
                continue
            if not argnames and _CLIENT_FACTORY.search(_body(node)):
                continue                          # returns a client object, holds no data
            m = _live_marker(node)
            if m:
                rel = path.relative_to(root).as_posix()
                hits.append(f"{rel}:{node.lineno} {node.name} -> {m}")
    return sorted(hits)
