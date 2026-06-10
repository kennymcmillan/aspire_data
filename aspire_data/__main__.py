"""aspire-data CLI — verify every env-configured connection in one shot.

Usage:
    aspire-data status              # ping every endpoint, print verdicts
    aspire-data status --json       # machine-readable
    aspire-data env                 # show which env vars are set (no values)
"""
from __future__ import annotations

import argparse
import json
import os
import sys


# (env var, label, probe-fn) tuples. Each probe-fn returns (ok: bool, msg: str).
PROBES = [
    # Connect APIs
    ("CONNECT_API_KEY", "Connect base", "_probe_connect"),
    ("HANA_API_GUID",   "hana-api on Connect", "_probe_hana_api"),
    ("RENDER_API_GUID", "render-api on Connect", "_probe_render_api"),
    ("JOBS_API_GUID",   "jobs-api on Connect", "_probe_jobs_api"),
    ("NOTIFY_API_GUID", "notify-api on Connect", "_probe_notify_api"),
    ("ASPIRE_KB_API_GUID", "aspire-kb-api on Connect", "_probe_aspire_kb"),
    # FastAPI / scraper layers
    ("SPORTS_API_URL",  "Sports API",   "_probe_sports_api"),
    ("HETZNER_PROXY_BASE", "Hetzner scraper (proxy)", "_probe_hetzner"),
    # SAMS
    ("SAMS_BASE_URL",   "SAMS",         "_probe_sams"),
    # DBs (just env presence — actual conn is heavyweight)
    ("ORACLE_MYSQL_URL", "Oracle MySQL (env present)", "_probe_env_only"),
    ("ORACLE_PG_URL",    "Oracle Postgres (env present)", "_probe_env_only"),
    ("AIVEN_PG_URL",     "Aiven Postgres (env present)",  "_probe_env_only"),
    ("AIVEN_MYSQL_URL",  "Aiven MySQL (env present)",     "_probe_env_only"),
    ("MOTHERDUCK_TOKEN", "MotherDuck (env present)",       "_probe_env_only"),
]


# ---- individual probes ----

def _probe_connect() -> tuple[bool, str]:
    import httpx
    base = os.environ.get("CONNECT_BASE_URL", "").rstrip("/")
    if not base:
        return False, "CONNECT_BASE_URL not set"
    try:
        r = httpx.get(f"{base}/__ping__", timeout=10)
        return r.status_code < 500, f"{r.status_code} ({base})"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _probe_render_api() -> tuple[bool, str]:
    return _probe_generic_guid("RENDER_API_GUID")


def _probe_jobs_api() -> tuple[bool, str]:
    return _probe_generic_guid("JOBS_API_GUID")


def _probe_notify_api() -> tuple[bool, str]:
    return _probe_generic_guid("NOTIFY_API_GUID")


def _probe_aspire_kb() -> tuple[bool, str]:
    return _probe_generic_guid("ASPIRE_KB_API_GUID")


def _probe_generic_guid(env_var: str) -> tuple[bool, str]:
    from .connect import ConnectClient
    guid = os.environ.get(env_var)
    if not guid: return False, "guid not set"
    try:
        cli = ConnectClient(guid)
        cli.health()
        return True, "/health OK"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _probe_hana_api() -> tuple[bool, str]:
    return _probe_generic_guid("HANA_API_GUID")


def _probe_sports_api() -> tuple[bool, str]:
    from .sports_api import SportsApi
    try:
        h = SportsApi().health()
        return h.get("ok") in (True, None), f"{h}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _probe_hetzner() -> tuple[bool, str]:
    from .hetzner import HetznerClient
    try:
        h = HetznerClient().health()
        return True, f"{h}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _probe_sams() -> tuple[bool, str]:
    from .sams import SamsClient
    try:
        # Cheap: just see if the client constructs (env present + auth headers built).
        SamsClient()
        return True, "auth headers ok"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _probe_env_only() -> tuple[bool, str]:
    """Just confirm the env var is set — don't open the connection."""
    return True, "env var present"


# ---- command dispatch ----

def cmd_status(args):
    results = []
    for env_var, label, probe_name in PROBES:
        if not os.environ.get(env_var):
            results.append({"label": label, "env": env_var, "ok": None,
                            "msg": "(env var not set — skipping)"})
            continue
        fn = globals()[probe_name]
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"probe crash: {e}"
        results.append({"label": label, "env": env_var, "ok": ok, "msg": msg})

    if args.json:
        print(json.dumps(results, indent=2))
        return
    for r in results:
        if r["ok"] is None:
            mark = "·"
        elif r["ok"]:
            mark = "OK"
        else:
            mark = "FAIL"
        print(f"  [{mark:<4}] {r['label']:<35} {r['msg']}")


def cmd_env(_args):
    print("aspire_data env vars (showing names + 'set'/'-'):")
    for env_var, label, _ in PROBES:
        flag = "set" if os.environ.get(env_var) else "-"
        print(f"  {env_var:<28} {flag:<6} {label}")


def main():
    parser = argparse.ArgumentParser(prog="aspire-data",
        description="Universal data-layer companion for Aspire apps.")
    sub = parser.add_subparsers(dest="command")

    s = sub.add_parser("status", help="Ping every env-configured endpoint")
    s.add_argument("--json", action="store_true", help="JSON output")
    s.set_defaults(func=cmd_status)

    e = sub.add_parser("env", help="Show which env vars are set (no values)")
    e.set_defaults(func=cmd_env)

    args = parser.parse_args()
    if not args.command:
        parser.print_help(); sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
