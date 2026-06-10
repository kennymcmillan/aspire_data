"""aspire_data — universal data-layer companion for Aspire Academy Dash + report apps."""
from setuptools import setup, find_packages

setup(
    name="aspire_data",
    version="0.6.0",
    description=("Connection clients + helpers for every backing store every Aspire app talks to: "
                  "Posit Connect APIs, Sports API, SAMS, SAP HANA, Aiven, Oracle MySQL/Postgres, "
                  "Hetzner OpenClaw, MotherDuck. Auto-fixes the Aspire MITM TLS chain. "
                  "Public-safe by design: hostnames + secrets only via env vars."),
    author="Kenny McMillan",
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "httpx>=0.27",
        "python-dotenv>=1.0",
        "truststore>=0.10",
        "cachetools>=5.5",
        "rapidfuzz>=3.0",
    ],
    extras_require={
        "mysql":      ["aiomysql>=0.2.0", "pymysql>=1.1"],
        "postgres":   ["asyncpg>=0.29", "psycopg[binary]>=3.2"],
        "hana":       ["hdbcli>=2.20"],
        "duckdb":     ["duckdb>=1.1"],
        "all": [
            "aiomysql>=0.2.0", "pymysql>=1.1",
            "asyncpg>=0.29", "psycopg[binary]>=3.2",
            "duckdb>=1.1",
        ],
    },
    entry_points={
        "console_scripts": [
            "aspire-data=aspire_data.__main__:main",
        ],
    },
)
