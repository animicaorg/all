"""Tests for tx.sendRawTransaction behavior when PQ backend is unavailable."""

from __future__ import annotations

import types

import pytest

from rpc.methods import tx


@pytest.fixture(autouse=True)
def _clear_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset optional PQ flags between tests."""

    monkeypatch.setattr(tx, "_PQ_VERIFY_OPTIONAL", False)
    monkeypatch.setattr(tx, "_pq_verify", None)


def _sample_raw() -> bytes:
    obj = {
        "body": {"chainId": 1, "nonce": 0},
        "sig": {"algId": 4097, "pubkey": b"\x01" * 1952, "sig": b"\x02" * 3293},  # Fixed: Dilithium3 requires 1952-byte pubkey
    }
    return tx._cbor_dumps(obj)


class _StubMempool:
    def __init__(self) -> None:
        self.by_hash: dict[str, bytes] = {}
        self.tx_index = None

    def submit(self, *, tx, raw: bytes, tx_hash_hex: str, local: bool = True, origin_peer=None):
        self.by_hash[tx_hash_hex] = bytes(raw)
        return tx_hash_hex

    def has_hash(self, tx_hash_hex: str) -> bool:
        return tx_hash_hex in self.by_hash


class _InvisibleMempool(_StubMempool):
    def has_hash(self, tx_hash_hex: str) -> bool:
        return False


def test_sendRawTransaction_skips_verify_when_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _sample_raw()

    # Bypass PQ verification path
    monkeypatch.setattr(tx, "_PQ_VERIFY_OPTIONAL", True)
    monkeypatch.setattr(tx, "_pq_verify", None)

    # Avoid external dependencies during the send flow
    monkeypatch.setattr(tx, "_validate_chain_id", lambda obj: 1)
    monkeypatch.setattr(tx, "_lookup_persisted_tx", lambda h: (None, None, None, None))
    monkeypatch.setattr(tx, "_pending_get", lambda h: None)
    mempool = _StubMempool()
    monkeypatch.setattr(tx, "_get_mempool_service", lambda: mempool)

    tx_hash = tx._tx_send_raw_transaction("0x" + raw.hex())

    assert isinstance(tx_hash, str)
    assert tx_hash.startswith("0x")
    assert len(tx_hash) == 66
    assert tx_hash in mempool.by_hash
    assert isinstance(mempool.by_hash[tx_hash], (bytes, bytearray))


def test_sendRawTransaction_requires_pq_when_not_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _sample_raw()

    monkeypatch.setattr(tx, "_PQ_VERIFY_OPTIONAL", False)
    monkeypatch.setattr(tx, "_pq_verify", None)
    monkeypatch.setattr(tx, "_validate_chain_id", lambda obj: 1)

    with pytest.raises(tx.rpc_errors.InternalError):
        tx._tx_send_raw_transaction("0x" + raw.hex())


def test_sendRawTransaction_fails_if_not_visible_in_canonical_mempool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _sample_raw()

    monkeypatch.setattr(tx, "_PQ_VERIFY_OPTIONAL", True)
    monkeypatch.setattr(tx, "_pq_verify", None)
    monkeypatch.setattr(tx, "_validate_chain_id", lambda obj: 1)
    monkeypatch.setattr(tx, "_lookup_persisted_tx", lambda h: (None, None, None, None))
    monkeypatch.setattr(tx, "_pending_get", lambda h: None)

    mempool = _InvisibleMempool()
    monkeypatch.setattr(tx, "_get_mempool_service", lambda: mempool)

    with pytest.raises(tx.rpc_errors.InternalError, match="not present in mempool"):
        tx._tx_send_raw_transaction("0x" + raw.hex())
