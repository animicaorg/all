"""
TLS trust configuration for the frozen app.

A PyInstaller build ships its own Python with NO system CA store, and macOS/Windows Python
often can't find the OS trust roots either — so urllib/requests raise
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`` on the very first HTTPS
call (name resolution against animica.dev). We fix it by shipping certifi's CA bundle and
pointing both the SSL env vars and an explicit SSLContext at it.

Call ``install_ca_env()`` once at startup (before any network use); use ``ca_context()`` for
every urlopen/opener so resolution works even if the env vars are ignored.
"""

from __future__ import annotations

import os
import ssl
from typing import Optional

_CTX: Optional[ssl.SSLContext] = None


def _ca_file() -> Optional[str]:
    # certifi first (bundled with the app), then any CA file the env already points at.
    try:
        import certifi
        path = certifi.where()
        if path and os.path.exists(path):
            return path
    except Exception:  # noqa: BLE001
        pass
    for env in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        p = os.environ.get(env)
        if p and os.path.exists(p):
            return p
    return None


def install_ca_env() -> None:
    """Point the whole process (urllib, requests, ssl defaults) at a real CA bundle."""
    ca = _ca_file()
    if not ca:
        return
    os.environ.setdefault("SSL_CERT_FILE", ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)
    d = os.path.dirname(ca)
    if d:
        os.environ.setdefault("SSL_CERT_DIR", d)


def ca_context() -> ssl.SSLContext:
    """A verifying SSLContext backed by certifi (falls back to the system default)."""
    global _CTX
    if _CTX is not None:
        return _CTX
    ca = _ca_file()
    try:
        _CTX = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    except Exception:  # noqa: BLE001
        _CTX = ssl.create_default_context()
    return _CTX
