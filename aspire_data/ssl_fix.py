"""TLS-trust fix for Aspire laptops behind GlobalProtect.

GlobalProtect performs TLS inspection: every HTTPS connection is
intercepted and re-signed with a corporate CA that's installed in
the Windows trust store but NOT in `certifi` (which Python's
`requests`/`httpx`/`urllib3` default to).

Net effect: every Aspire-internal HTTPS call fails with
`CERTIFICATE_VERIFY_FAILED [self-signed certificate in chain]`.

Fix: `truststore.inject_into_ssl()` switches Python to use the
system trust store, which already has the corp CA. This is NOT
`verify=False` — it's proper validation against the right cert store.

Behavior:
  - No-op on machines without truststore installed
  - No-op on machines where `truststore` is already injected
  - No-op on properly-trusted environments (Connect / VMs)
  - Idempotent (safe to call multiple times)
"""
from __future__ import annotations

import os


def inject_truststore() -> bool:
    """Try to enable the Windows-trust-store fix. Returns True if
    truststore was injected, False if skipped (already injected,
    library missing, or explicitly disabled)."""
    if os.environ.get("ASPIRE_DATA_NO_TRUSTSTORE", "").lower() in ("1", "true", "yes"):
        return False
    try:
        import truststore
    except ImportError:
        return False
    try:
        truststore.inject_into_ssl()
        return True
    except Exception:  # noqa: BLE001
        # Already injected, or unsupported Python version, etc.
        return False
