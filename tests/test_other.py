"""Oracle / Aiven / Hetzner / Hana / Posit / CLI — env-validation +
import smoke. Live connection tests are deliberately out of scope."""
from __future__ import annotations

import pytest


# ---------- Oracle pools (env-only check; pools need real drivers) ----------

def test_oracle_mysql_pool_requires_env(monkeypatch):
    pytest.importorskip("aiomysql")  # pool imports the driver before the env check
    monkeypatch.delenv("ORACLE_MYSQL_URL", raising=False)
    from aspire_data.oracle import mysql_pool
    import asyncio
    with pytest.raises(RuntimeError, match="ORACLE_MYSQL_URL"):
        asyncio.run(mysql_pool())


def test_oracle_postgres_pool_requires_env(monkeypatch):
    pytest.importorskip("asyncpg")  # pool imports the driver before the env check
    monkeypatch.delenv("ORACLE_PG_URL", raising=False)
    from aspire_data.oracle import postgres_pool
    import asyncio
    with pytest.raises(RuntimeError, match="ORACLE_PG_URL"):
        asyncio.run(postgres_pool())


def test_oracle_mysql_url_parser():
    from aspire_data.oracle import _mysql_kwargs_from_url
    kw = _mysql_kwargs_from_url("mysql://user:pwd@host:3306/dbname")
    assert kw["host"] == "host"
    assert kw["port"] == 3306
    assert kw["user"] == "user"
    assert kw["password"] == "pwd"
    assert kw["db"] == "dbname"


def test_oracle_mysql_url_default_port():
    from aspire_data.oracle import _mysql_kwargs_from_url
    kw = _mysql_kwargs_from_url("mysql://u:p@host/db")
    assert kw["port"] == 3306


# ---------- Aiven (env-only) ----------

def test_aiven_postgres_requires_env(monkeypatch):
    pytest.importorskip("psycopg")  # conn imports the driver before the env check
    monkeypatch.delenv("AIVEN_PG_URL", raising=False)
    from aspire_data.aiven import aiven_postgres_conn
    with pytest.raises(RuntimeError, match="AIVEN_PG_URL"):
        with aiven_postgres_conn():
            pass


def test_aiven_mysql_requires_env(monkeypatch):
    pytest.importorskip("pymysql")  # conn imports the driver before the env check
    monkeypatch.delenv("AIVEN_MYSQL_URL", raising=False)
    from aspire_data.aiven import aiven_mysql_conn
    with pytest.raises(RuntimeError, match="AIVEN_MYSQL_URL"):
        with aiven_mysql_conn():
            pass


# ---------- Hetzner ----------

def test_hetzner_proxy_constructs(mock_httpx):
    from aspire_data.hetzner import HetznerClient
    h = HetznerClient()  # default = proxy mode
    assert h.direct is False
    assert "hetzner-proxy.example.com" in h.base_url


def test_hetzner_proxy_base_required(monkeypatch):
    monkeypatch.delenv("HETZNER_PROXY_BASE", raising=False)
    from aspire_data.hetzner import HetznerClient
    with pytest.raises(RuntimeError, match="HETZNER_PROXY_BASE"):
        HetznerClient()


def test_hetzner_direct_requires_separate_env(monkeypatch):
    monkeypatch.delenv("HETZNER_DIRECT_BASE", raising=False)
    from aspire_data.hetzner import HetznerClient
    with pytest.raises(RuntimeError, match="HETZNER_DIRECT_BASE"):
        HetznerClient(direct=True)


def test_hetzner_scrape_post(mock_httpx):
    from aspire_data.hetzner import HetznerClient
    h = HetznerClient()
    mock_httpx.instances[-1].set_response(json_body={"items": [1, 2, 3]})
    out = h.scrape("/sports/fip/calendar", json={"year": 2024})
    assert out == {"items": [1, 2, 3]}
    method, path, kwargs = mock_httpx.instances[-1].calls[0]
    assert method == "POST"
    assert path == "/sports/fip/calendar"


def test_hetzner_scrape_get(mock_httpx):
    from aspire_data.hetzner import HetznerClient
    h = HetznerClient()
    h.scrape("/health", method="GET")
    method, path, _ = mock_httpx.instances[-1].calls[0]
    assert method == "GET"


# ---------- HANA (Connect path) ----------

def test_hana_sql_via_connect_imports():
    from aspire_data.hana import (
        hana_sql_via_connect, hana_sql_direct, hana_sql_subprocess,
    )
    assert callable(hana_sql_via_connect)
    assert callable(hana_sql_direct)
    assert callable(hana_sql_subprocess)


# ---------- Posit admin ----------

def test_posit_admin_constructs(mock_httpx):
    from aspire_data.posit import ConnectAdminClient
    pc = ConnectAdminClient()
    assert "connect.example.com" in pc.base_url
    assert pc.base_url.endswith("/__api__/v1")


def test_posit_admin_requires_base_url(monkeypatch):
    monkeypatch.delenv("CONNECT_BASE_URL", raising=False)
    from aspire_data.posit import ConnectAdminClient
    with pytest.raises(RuntimeError, match="CONNECT_BASE_URL"):
        ConnectAdminClient()


# ---------- CLI ----------

def test_cli_env_command(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["aspire-data", "env"])
    from aspire_data.__main__ import main
    main()
    out = capsys.readouterr().out
    # All probe env names should appear in the summary
    assert "CONNECT_API_KEY" in out
    assert "SAMS_BASE_URL" in out
    assert "set" in out or "-" in out


def test_cli_status_command_runs(capsys, monkeypatch, mock_httpx):
    monkeypatch.setattr("sys.argv", ["aspire-data", "status"])
    # Each probe's HTTP call returns the mock; status command shouldn't crash
    from aspire_data.__main__ import main
    main()
    out = capsys.readouterr().out
    # At least one verdict line should render
    assert "Connect base" in out or "Sports API" in out


def test_cli_status_json(capsys, monkeypatch, mock_httpx):
    monkeypatch.setattr("sys.argv", ["aspire-data", "status", "--json"])
    from aspire_data.__main__ import main
    main()
    import json
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert any("label" in r and "env" in r for r in parsed)


def test_cli_no_subcommand_shows_help(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["aspire-data"])
    from aspire_data.__main__ import main
    with pytest.raises(SystemExit):
        main()
    err = capsys.readouterr()
    combined = err.out + err.err
    assert "aspire-data" in combined or "status" in combined or "env" in combined
