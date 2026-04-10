"""Tests for chain ID resolution in tx RPC methods."""

import types

import pytest

from rpc.errors import ChainIdMismatch
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


def test_validate_chain_id_rejects_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismatch should surface tx-provided id as 'got' and node id as 'expected'."""

    monkeypatch.setattr(tx.deps, "get_chain_id", lambda: 2)

    tx_obj = {"body": {"chainId": 1}, "sig": {"algId": 0, "pubkey": b"", "sig": b""}}

    with pytest.raises(ChainIdMismatch) as excinfo:
        tx._validate_chain_id(tx_obj)

    assert excinfo.value.data["got"] == 1
    assert excinfo.value.data["expected"] == 2


def test_validate_chain_id_prefers_body_over_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signed body chainId must be honored even if wrapper differs."""

    monkeypatch.setattr(tx.deps, "get_chain_id", lambda: 9)

    tx_obj = {
        "chainId": 3,
        "body": {"chainId": 9},
        "sig": {"algId": 0, "pubkey": b"", "sig": b""},
    }

    assert tx._validate_chain_id(tx_obj) == 9
