from __future__ import annotations

import importlib


def test_backend_diagnostics_include_required_mainnet_schemes(monkeypatch):
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.delenv("ANIMICA_ALLOWED_SIG_SCHEMES", raising=False)
    monkeypatch.delenv("ANIMICA_DISABLED_SIGNATURE_SCHEMES", raising=False)

    import coretx.crypto as crypto

    importlib.reload(crypto)
    status = crypto.get_signature_policy_status()
    backends = {int(row["schemeId"]): row for row in status.get("backends", [])}

    assert 1 in backends
    assert 2 in backends
    assert backends[1].get("selfTest") in {"ok", "failed", "error"}
    assert backends[2].get("selfTest") in {"ok", "failed", "error"}

