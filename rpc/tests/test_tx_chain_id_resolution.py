"""Tests for chain ID resolution in tx RPC methods."""

import types

import pytest

from rpc.methods import tx


@pytest.fixture(autouse=True)
def restore_chain_id(monkeypatch: pytest.MonkeyPatch):
    """Ensure get_chain_id is reset after each test."""
    yield
    monkeypatch.undo()


def test_chain_id_required_uses_deps_accessor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure _chain_id_required prefers deps.get_chain_id() when available."""

    monkeypatch.setattr(tx.deps, "get_chain_id", lambda: 42)

    assert tx._chain_id_required() == 42


def test_validate_chain_id_matches_deps_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """_validate_chain_id should accept chain IDs matching deps.get_chain_id."""

    monkeypatch.setattr(tx.deps, "get_chain_id", lambda: 7)

    tx_obj = {"body": {"chainId": 7}, "sig": {"algId": 0, "pubkey": b"", "sig": b""}}

    # Should not raise ChainIdMismatch and should return the validated id
    assert tx._validate_chain_id(tx_obj) == 7
