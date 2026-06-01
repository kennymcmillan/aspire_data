"""Public-safety audit — fail loud if a secret pattern leaked into the repo.

This script itself contains NO real secret material. It detects leaks two ways:

  1. GENERIC scan (always on) — private-key blocks, known-prefix API keys
     (sk-ant-, sk-proj-, ghp_, AKIA…), JWT-like tokens, and URLs with inline
     credentials (scheme://user:pass@host). Placeholder creds in docs/tests
     (user:pwd@, u:p@, <...>:<...>@) are ignored.

  2. SPECIFIC scan (opt-in) — exact internal hostnames / IPs / GUID or key
     fragments, loaded at runtime from sources that are NEVER committed:
        • env var  AUDIT_DENYLIST  (comma-separated)  — e.g. a CI secret
        • file     _audit_denylist.local  (one value per line, # comments ok)
     Both are git-ignored, so the real values stay out of the repo. CI runs
     the generic scan; a developer (or CI with a secret) runs the full scan.

Exit code 1 on any finding.
"""
from __future__ import annotations

import os
import pathlib
import re

SCANNED_SUFFIXES = (".py", ".md", ".txt", ".yml", ".yaml", ".example", ".toml", ".cfg", "")
SELF = ("_audit.py", "_audit_denylist.local")

# --- Generic detectors (contain NO real secrets) ---
GENERIC_PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("anthropic key",     re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai key",        re.compile(r"sk-proj-[A-Za-z0-9_-]{16,}")),
    ("github token",      re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws access key",    re.compile(r"AKIA[0-9A-Z]{16}")),
    ("jwt",               re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("url inline creds",  re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@'\"]+:[^/\s@'\"]+@")),
]

# Placeholder credentials that are NOT real (avoid false positives in docs/tests)
PLACEHOLDER_CREDS = re.compile(
    r"://(?:user|username|u|<[^>]+>):(?:pwd|pass|password|p|<[^>]+>)@", re.I
)


def load_specific_denylist() -> list[str]:
    values: list[str] = []
    env = os.environ.get("AUDIT_DENYLIST", "")
    if env:
        values += [v.strip() for v in env.split(",") if v.strip()]
    local = pathlib.Path("_audit_denylist.local")
    if local.exists():
        for line in local.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                values.append(line)
    return values


def iter_files():
    for path in pathlib.Path(".").rglob("*"):
        if path.is_dir():
            continue
        if any(p in path.parts for p in (".git", "__pycache__", ".venv", ".pytest_cache")):
            continue
        if path.name in SELF:
            continue
        if path.suffix not in SCANNED_SUFFIXES:
            continue
        yield path


def main() -> int:
    specific = load_specific_denylist()
    issues: list[str] = []
    scanned = 0
    for path in iter_files():
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        scanned += 1
        for label, rx in GENERIC_PATTERNS:
            for m in rx.finditer(src):
                if label == "url inline creds" and PLACEHOLDER_CREDS.search(m.group(0)):
                    continue
                line_no = src[: m.start()].count("\n") + 1
                issues.append(f"{path} L{line_no}: {label}: {m.group(0)[:48]!r}")
        for bad in specific:
            idx = src.find(bad)
            if idx != -1:
                line_no = src[:idx].count("\n") + 1
                issues.append(f"{path} L{line_no}: denylisted value present")

    mode = "loaded" if specific else "none — generic scan only"
    print(f"Scanned {scanned} files. Specific denylist: {len(specific)} value(s) ({mode}).")
    if issues:
        print(f"\n!!! {len(issues)} POTENTIAL LEAK(S) !!!")
        for i in issues:
            print(f"  {i}")
        return 1
    print("CLEAN — no private keys, known-prefix API keys, JWTs, inline-cred URLs, "
          "or denylisted values found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
