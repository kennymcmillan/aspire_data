"""Smoke imports for every module. Catches refactors that break the
public surface before they land downstream."""
from __future__ import annotations


def test_top_level_import():
    import aspire_data
    assert hasattr(aspire_data, "__version__")
    assert aspire_data.__version__.startswith("0.")


def test_all_modules_import():
    from aspire_data import (
        ssl_fix, connect, sports_api, sams, oracle,
        aiven, hetzner, hana, posit,
    )
    # Quick sanity — every module has a docstring
    for mod in (ssl_fix, connect, sports_api, sams, oracle,
                aiven, hetzner, hana, posit):
        assert mod.__doc__, f"{mod.__name__} missing docstring"


def test_truststore_inject_is_idempotent():
    # Multiple calls must not raise
    from aspire_data.ssl_fix import inject_truststore
    # Behavior depends on the env: returns bool either way
    for _ in range(3):
        result = inject_truststore()
        assert isinstance(result, bool)


def test_truststore_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ASPIRE_DATA_NO_TRUSTSTORE", "1")
    from aspire_data.ssl_fix import inject_truststore
    assert inject_truststore() is False
