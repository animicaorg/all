from __future__ import annotations

import importlib

import pytest


def test_mainnet_defaults_include_required_pq(monkeypatch):
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.delenv("ANIMICA_ALLOWED_SIG_SCHEMES", raising=False)
    monkeypatch.delenv("ANIMICA_DISABLED_SIGNATURE_SCHEMES", raising=False)

    import coretx.crypto as crypto

    importlib.reload(crypto)
    status = crypto.get_signature_policy_status()
    scheme_map = {int(row["schemeId"]): row for row in status["schemes"]}
    assert scheme_map[1]["enabledByPolicy"] is True
    assert scheme_map[2]["enabledByPolicy"] is True


def test_mainnet_rejects_contradictory_allowed_override(monkeypatch):
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.setenv("ANIMICA_ALLOWED_SIG_SCHEMES", "2")

    import coretx.crypto as crypto

    with pytest.raises(ValueError, match="ANIMICA_ALLOWED_SIG_SCHEMES contradicts"):
        importlib.reload(crypto)
