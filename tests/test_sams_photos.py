"""SAMS athlete photos: resolve_photo_url / fetch_photo / fetch_photos (0.23.0).

Real-world shapes (2026-09-27, 164 athletes): public blob URLs, https URLs on the SAMS web host
(403 to the python-requests user-agent), and bare file names that live under /uploads/player-images/.
"""
from __future__ import annotations

import threading
import time

import httpx
import pytest

from aspire_data import sams


@pytest.fixture(autouse=True)
def _clear_cache():
    sams._photo_cache.clear()
    yield
    sams._photo_cache.clear()


# ── resolve_photo_url ───────────────────────────────────────────────────────
def test_resolve_keeps_https_urls():
    u = "https://blob.example.net/sports/player/1.jpg"
    assert sams.resolve_photo_url(u) == u


def test_resolve_bare_filename_uses_sams_web_root():
    got = sams.resolve_photo_url("30963401416.jpg?t=224", base_url="https://sams.example.com/SamsApiExternalApps")
    assert got == "https://sams.example.com/uploads/player-images/30963401416.jpg?t=224"


@pytest.mark.parametrize("raw", ["uploads/player-images/9.jpg?t=3", "/uploads/player-images/9.jpg?t=3"])
def test_resolve_relative_uploads_path(raw):
    assert sams.resolve_photo_url(raw) == "https://sams.example.com/uploads/player-images/9.jpg?t=3"


def test_resolve_relative_path_cannot_escape_the_folder():
    assert sams.resolve_photo_url("uploads/player-images/../secret.jpg") == ""
    assert sams.resolve_photo_url("uploads/other/9.jpg") == ""


def test_resolve_bare_filename_defaults_to_env():     # conftest sets SAMS_BASE_URL=https://sams.example.com
    assert sams.resolve_photo_url("7.png") == "https://sams.example.com/uploads/player-images/7.png"


@pytest.mark.parametrize("raw", [None, "", "  ", "javascript:alert(1)", "http://insecure.example/1.jpg",
                                 "../x.jpg", "a/b.jpg", "a\\b.jpg", "notes.txt", "12345"])
def test_resolve_rejects_unsafe_or_junk(raw):
    assert sams.resolve_photo_url(raw) == ""


def test_resolve_needs_https_base_for_bare_names():
    assert sams.resolve_photo_url("1.jpg", base_url="http://sams.example.com") == ""


# ── fetch_photo ─────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status=200, ctype="image/jpeg", content=b"JPEGDATA"):
        self.status_code, self.headers, self.content = status, {"content-type": ctype}, content


def test_fetch_sends_named_user_agent_and_returns_bytes(monkeypatch):
    seen = {}

    def fake_get(url, timeout, follow_redirects, headers):
        seen.update(headers)
        return _Resp()
    monkeypatch.setattr(httpx, "get", fake_get)
    assert sams.fetch_photo("https://sams.example.com/uploads/player-images/1.jpg") == b"JPEGDATA"
    assert seen["User-Agent"] == sams.PHOTO_USER_AGENT
    assert "python-requests" not in seen["User-Agent"] and "Mozilla" not in seen["User-Agent"]


@pytest.mark.parametrize("resp", [_Resp(status=403, ctype="text/html", content=b"<html>forbidden"),
                                  _Resp(status=404, ctype="text/html"),
                                  _Resp(status=200, ctype="text/html", content=b"<html>login"),
                                  _Resp(status=200, content=b"")])
def test_fetch_returns_none_on_non_image(monkeypatch, resp):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)
    assert sams.fetch_photo("https://x.example/1.jpg") is None


def test_fetch_never_raises_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("slow")
    monkeypatch.setattr(httpx, "get", boom)
    assert sams.fetch_photo("https://x.example/1.jpg") is None
    assert sams.fetch_photo("") is None and sams.fetch_photo("http://x.example/1.jpg") is None


def test_fetch_caches_success_not_failure(monkeypatch):
    calls = []
    responses = iter([_Resp(status=500, ctype="text/html"), _Resp(), _Resp(content=b"OTHER")])

    def fake_get(*a, **k):
        calls.append(1)
        return next(responses)
    monkeypatch.setattr(httpx, "get", fake_get)
    u = "https://x.example/1.jpg"
    assert sams.fetch_photo(u) is None           # failure: not cached
    assert sams.fetch_photo(u) == b"JPEGDATA"    # retried, success cached
    assert sams.fetch_photo(u) == b"JPEGDATA"    # served from cache
    assert len(calls) == 2


# ── fetch_photos ────────────────────────────────────────────────────────────
def test_fetch_photos_dedupes_and_respects_deadline(monkeypatch):
    release = threading.Event()

    def fake_get(url, **k):
        if "slow" in url:
            release.wait(5)
        return _Resp(content=url.encode())
    monkeypatch.setattr(httpx, "get", fake_get)
    t0 = time.time()
    got = sams.fetch_photos(["https://x/a.jpg", "https://x/a.jpg", "https://x/slow.jpg", "", None], deadline=0.5)
    release.set()
    assert time.time() - t0 < 2.0
    assert set(got) == {"https://x/a.jpg", "https://x/slow.jpg"}
    assert got["https://x/a.jpg"] == b"https://x/a.jpg" and got["https://x/slow.jpg"] is None


def test_fetch_photos_empty():
    assert sams.fetch_photos([]) == {}


# ── SamsClient results carry resolved URLs ──────────────────────────────────
def test_client_rows_resolve_bare_filenames(mock_httpx):
    from aspire_data.sams import SamsClient
    s = SamsClient()
    hit = s._build_hit({"playerId": 1, "fullName": "X", "profileImageUrl": "55.jpg?t=1"})
    assert hit["photo_url"] == "https://sams.example.com/uploads/player-images/55.jpg?t=1"
    ctx = s._build_context({"playerId": 2, "fullName": "Y", "profileImageUrl": "javascript:alert(1)"})
    assert ctx["photo_url"] is None
