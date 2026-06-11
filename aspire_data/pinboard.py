"""Posit Connect pins — publish/read versioned datasets on Connect.

The org-internal CSV-sharing layer: a pin is a versioned file on Connect,
access-controlled by the Connect logins colleagues already have, readable
from Python (`pin_read`), R, or a browser download URL. Whole-file +
last-write-wins, so it is a *distribution* layer (publish once, read many),
NEVER an app's primary database — keep writes in Oracle and refresh the
pin after.

USAGE
=====

    from aspire_data.pinboard import publish_dataframe, read_pin

    publish_dataframe(df, "anthro_records", title="Anthro records (latest)")
    df = read_pin("anthro_records")                  # your own pin
    df = read_pin("Other.User@ASPIRE.QA/their_pin")  # someone else's

Pin names on Connect are `owner/name` where owner is the EXACT Connect
username (e.g. ``Kenneth.Mcmillan@ASPIRE.QA``). Bare names are auto-
prefixed with the caller's username, resolved once via /__api__/v1/user.

CONFIG (env): CONNECT_BASE_URL + CONNECT_API_KEY (same as aspire_data.connect).
Requires the optional `pins` package: ``pip install aspire_data[pins]``.
"""
from __future__ import annotations

__all__ = ["pin_board", "full_pin_name", "publish_dataframe", "read_pin"]

import threading
from typing import Any

import httpx

from .connect import _base, _key

_username_cache: dict[tuple, str] = {}
_lock = threading.Lock()


def _import_pins():
    try:
        import pins  # noqa: PLC0415 — optional dep, lazy by design
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "The `pins` package is not installed — "
            "pip install aspire_data[pins] (or just `pip install pins`)."
        ) from e
    return pins


def _username() -> str:
    """The Connect username of the API key's owner — required as the pin-name
    prefix (Connect rejects writes to another owner's namespace)."""
    key = (_base(), _key())
    with _lock:
        if key not in _username_cache:
            r = httpx.get(f"{_base()}/__api__/v1/user",
                          headers={"Authorization": f"Key {_key()}"}, timeout=15)
            r.raise_for_status()
            _username_cache[key] = r.json()["username"]
        return _username_cache[key]


def full_pin_name(name: str) -> str:
    """`owner/name` as-is; a bare `name` gets the caller's username prefix."""
    return name if "/" in name else f"{_username()}/{name}"


def pin_board():
    """A pins board for this Connect server (env-configured)."""
    pins = _import_pins()
    return pins.board_connect(server_url=_base(), api_key=_key())


def publish_dataframe(df, name: str, *, title: str | None = None,
                      description: str | None = None,
                      type: str = "csv") -> dict[str, Any]:
    """Write/refresh a pin from a DataFrame. Each call creates a new version;
    readers get the latest by default. Returns {name, version, rows}."""
    board = pin_board()
    full = full_pin_name(name)
    meta = board.pin_write(df, full, type=type, title=title,
                           description=description)
    return {"name": full, "version": meta.version.version, "rows": len(df)}


def read_pin(name: str, version: str | None = None):
    """Read a pin back (latest version unless one is given)."""
    board = pin_board()
    return board.pin_read(full_pin_name(name), version=version)
