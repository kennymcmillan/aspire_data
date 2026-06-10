"""Pin-drift detection and bumping for the aspire libs across deploy repos.

Apps on Connect pin `aspire_dash` / `aspire_data` to exact commit SHAs in
requirements.txt (deploy-what-you-tested). The failure mode that motivated
this module (2026-06-10): a library release does NOTHING for an app until
its pin is bumped — a redeploy silently rebuilds with the old SHA.

    aspire-data bump-pins              # report drift across ~/Documents/posit-deploys
    aspire-data bump-pins --apply      # rewrite drifted SHA pins to current main
    aspire-data bump-pins --base DIR   # scan a different root

Only exact-SHA pins are ever rewritten; `@main` / branch / tag refs are
reported as "branch" and left alone.
"""
from __future__ import annotations

__all__ = ["LIBS", "current_shas", "scan", "bump_file"]

import re
import subprocess
from pathlib import Path

LIBS = ("aspire_dash", "aspire_data")

# aspire_dash @ git+https://github.com/kennymcmillan/aspire_dash.git@<ref>
_PIN_RE = re.compile(
    r"(?P<lib>aspire_dash|aspire_data)\s*@\s*"
    r"git\+https://github\.com/kennymcmillan/(?P=lib)(?:\.git)?@(?P<ref>[^\s#]+)"
)
_HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")


def current_shas(libs: tuple[str, ...] = LIBS) -> dict[str, str]:
    """Resolve each lib's current origin/main SHA via `git ls-remote`
    (no clone needed; works from any machine with git + HTTPS)."""
    shas: dict[str, str] = {}
    for lib in libs:
        out = subprocess.run(
            ["git", "ls-remote", f"https://github.com/kennymcmillan/{lib}.git",
             "refs/heads/main"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        shas[lib] = out.split()[0]
    return shas


def _classify(ref: str, current: str) -> str:
    if not _HEX_RE.match(ref):
        return "branch"            # @main, tags — tracked at install time, never rewritten
    if current.startswith(ref) or ref.startswith(current):
        return "ok"
    return "drift"


def scan(base_dir: str | Path, shas: dict[str, str]) -> list[dict]:
    """Find every aspire-lib pin under base_dir/*/requirements*.txt.

    Returns rows: {app, file, lib, ref, current, status} where status is
    ok | drift | branch.
    """
    base = Path(base_dir)
    rows: list[dict] = []
    for req in sorted(base.glob("*/requirements*.txt")):
        text = req.read_text(encoding="utf-8", errors="replace")
        for m in _PIN_RE.finditer(text):
            lib, ref = m.group("lib"), m.group("ref")
            current = shas.get(lib, "")
            rows.append({
                "app": req.parent.name,
                "file": str(req),
                "lib": lib,
                "ref": ref,
                "current": current,
                "status": _classify(ref, current),
            })
    return rows


def bump_file(req_file: str | Path, shas: dict[str, str]) -> list[str]:
    """Rewrite drifted exact-SHA pins in one requirements file to the current
    main SHA. Branch refs and already-current pins are untouched.
    Returns the list of libs that were rewritten."""
    path = Path(req_file)
    text = path.read_text(encoding="utf-8")
    changed: list[str] = []

    def _sub(m: re.Match) -> str:
        lib, ref = m.group("lib"), m.group("ref")
        current = shas.get(lib, "")
        if current and _classify(ref, current) == "drift":
            changed.append(lib)
            return m.group(0).replace(f"@{ref}", f"@{current}")
        return m.group(0)

    new_text = _PIN_RE.sub(_sub, text)
    if changed:
        path.write_text(new_text, encoding="utf-8")
    return changed
