from __future__ import annotations

import pytest

from animica.cli import tx


def test_chain_identity_falls_back_to_env_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.delenv("ANIMICA_NETWORK", raising=False)

    def _rpc_fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("RPC down")

    monkeypatch.setattr(tx, "_rpc", _rpc_fail)

    resolution = tx._get_chain_identity("http://localhost:9999")
    assert resolution.identity["chainId"] == 1
    assert resolution.source == "ANIMICA_CHAIN_ID"


def test_chain_identity_fails_without_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANIMICA_CHAIN_ID", raising=False)
    monkeypatch.delenv("ANIMICA_NETWORK", raising=False)

    def _rpc_fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("RPC down")

    monkeypatch.setattr(tx, "_rpc", _rpc_fail)

    with pytest.raises(tx.ChainIdentityResolutionError) as exc:
        tx._get_chain_identity("http://localhost:9999")

    assert "RPC unreachable and no local chain identity found" in str(exc.value)
