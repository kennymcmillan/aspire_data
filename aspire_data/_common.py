"""Private shared plumbing for the Sports-API-backed modules.

One place for the three helpers that were copy-pasted across
sports_api / identifiers / whoop / firstbeat / supplements, plus a
shared, cached httpx.Client so a single athlete-card render doesn't
pay four TLS handshakes.

Not part of the public API — import nothing from here in app code.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import httpx

DEFAULT_TIMEOUT = 30.0

_clients: dict[tuple, httpx.Client] = {}
_caches: list[Any] = []
_lock = threading.Lock()


def _base() -> str:
    url = os.environ.get("SPORTS_API_URL", "").rstrip("/")
    if not url:
        raise RuntimeError(
            "SPORTS_API_URL not set — set your Sports API base URL.")
    return url


def _verify() -> bool:
    return os.environ.get("INSECURE_API_TLS", "false").lower() not in ("1", "true", "yes")


def _num(v):
    """float() or None — the whoop/firstbeat semantics.
    (supplements keeps its own default-0.0 variant: sums rely on it.)"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sports_client() -> httpx.Client:
    """Shared httpx.Client for the Sports API, cached per
    (client class, base URL, verify flag).

    Keying on the *class* means monkeypatched clients in tests get
    their own cache slot automatically; keying on base/verify means
    an env change (e.g. INSECURE_API_TLS) yields a fresh client.
    """
    key = (id(httpx.Client), _base(), _verify())
    with _lock:
        cli = _clients.get(key)
        if cli is None:
            cli = httpx.Client(base_url=_base(), verify=_verify(),
                               timeout=DEFAULT_TIMEOUT)
            _clients[key] = cli
    return cli


def get(path: str, *, params: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT) -> httpx.Response:
    """GET against the Sports API via the shared client."""
    return sports_client().get(path, params=params, timeout=timeout)


def post(path: str, *, json: dict | None = None,
         timeout: float = DEFAULT_TIMEOUT) -> httpx.Response:
    """POST against the Sports API via the shared client."""
    return sports_client().post(path, json=json, timeout=timeout)


def register_cache(cache) -> None:
    """Modules register their TTL caches so reset_caches() can clear
    everything in one call (tests, long-lived processes)."""
    with _lock:
        _caches.append(cache)


def reset_caches() -> None:
    """Drop all cached clients and clear every registered TTL cache."""
    with _lock:
        for cli in _clients.values():
            try:
                cli.close()
            except Exception:  # noqa: BLE001
                pass
        _clients.clear()
        for c in _caches:
            c.clear()
