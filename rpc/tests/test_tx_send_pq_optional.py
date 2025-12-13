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
        "sig": {"algId": 4097, "pubkey": b"\x01" * 32, "sig": b"\x02" * 64},
    }
    return tx._cbor_dumps(obj)


def test_sendRawTransaction_skips_verify_when_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _sample_raw()

    # Bypass PQ verification path
    monkeypatch.setattr(tx, "_PQ_VERIFY_OPTIONAL", True)
    monkeypatch.setattr(tx, "_pq_verify", None)

    # Avoid external dependencies during the send flow
    monkeypatch.setattr(tx, "_validate_chain_id", lambda obj: 1)
    monkeypatch.setattr(tx, "_lookup_persisted_tx", lambda h: (None, None, None, None))
    monkeypatch.setattr(tx, "_pending_get", lambda h: None)

    stored: list[tuple[str, bytes]] = []
    monkeypatch.setattr(tx, "_pending_put", lambda h, r: stored.append((h, r)))

    tx_hash = tx._tx_send_raw_transaction("0x" + raw.hex())

    # Hash computed from raw CBOR bytes (sha3_256)
    assert tx_hash == tx._hex(tx._sha3_256(raw))
    assert stored == [(tx_hash, raw)]


def test_sendRawTransaction_requires_pq_when_not_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _sample_raw()

    monkeypatch.setattr(tx, "_PQ_VERIFY_OPTIONAL", False)
    monkeypatch.setattr(tx, "_pq_verify", None)
    monkeypatch.setattr(tx, "_validate_chain_id", lambda obj: 1)

    with pytest.raises(tx.rpc_errors.InternalError):
        tx._tx_send_raw_transaction("0x" + raw.hex())
