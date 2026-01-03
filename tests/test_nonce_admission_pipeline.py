"""
Tests for nonce admission and RPC correctness around mempool acceptance.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mempool.errors import NonceGap, NonceTooLow
from mempool.pool import Pool, PoolConfig
from rpc.mempool_service import MempoolService
from rpc.methods import tx as tx_methods
from rpc import errors as rpc_errors


def _build_tx(sender_bytes: bytes, nonce: int, *, chain_id: int = 1337) -> tuple[dict, bytes, str]:
    try:
        from core.encoding.cbor import dumps as cbor_dumps
        from core.utils.hash import sha3_256
    except ImportError as exc:  # pragma: no cover - depends on optional core
        pytest.skip(f"CBOR encoding not available: {exc}")

    body = {
        "from": sender_bytes,
        "nonce": nonce,
        "gasLimit": 21000,
        "chainId": chain_id,
        "gasPrice": 1,
        "maxFee": 1,
    }
    tx_envelope = {"body": body}
    raw_bytes = cbor_dumps(tx_envelope)
    tx_dict = tx_envelope.copy()
    tx_dict["raw"] = raw_bytes
    tx_hash_hex = "0x" + sha3_256(raw_bytes).hex()
    return tx_dict, raw_bytes, tx_hash_hex


def _make_service(confirmed_nonce: int) -> tuple[MempoolService, bytes]:
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024 * 1024))
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=confirmed_nonce)
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    sender = "0x" + "11" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    return service, sender_bytes


def test_rejected_too_low_does_not_advance_expected() -> None:
    service, sender_bytes = _make_service(confirmed_nonce=55)

    tx_dict, raw_bytes, tx_hash_hex = _build_tx(sender_bytes, 54)
    with pytest.raises(NonceTooLow) as exc_info:
        service.submit(tx=tx_dict, raw=raw_bytes, tx_hash_hex=tx_hash_hex, local=True)
    assert exc_info.value.context["expected_nonce"] == 55

    next_nonce = service.get_next_nonce(sender_bytes, confirmed_nonce=55)
    assert next_nonce == 55

    with pytest.raises(NonceTooLow):
        service.submit(tx=tx_dict, raw=raw_bytes, tx_hash_hex=tx_hash_hex, local=True)
    assert service.get_next_nonce(sender_bytes, confirmed_nonce=55) == 55


def test_sequential_acceptance() -> None:
    service, sender_bytes = _make_service(confirmed_nonce=58)

    tx_dict_58, raw_58, hash_58 = _build_tx(sender_bytes, 58)
    tx_dict_59, raw_59, hash_59 = _build_tx(sender_bytes, 59)

    assert service.submit(tx=tx_dict_58, raw=raw_58, tx_hash_hex=hash_58, local=True) == hash_58
    assert service.submit(tx=tx_dict_59, raw=raw_59, tx_hash_hex=hash_59, local=True) == hash_59

    assert service.get_next_nonce(sender_bytes, confirmed_nonce=58) == 60


def test_rpc_returns_error_on_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    service, sender_bytes = _make_service(confirmed_nonce=55)

    tx_dict, raw_bytes, _tx_hash_hex = _build_tx(sender_bytes, 54)
    raw_hex = "0x" + raw_bytes.hex()

    ctx = SimpleNamespace(mempool=service, state_db=service.state_db)
    monkeypatch.setattr(tx_methods.deps, "get_ctx", lambda: ctx)
    monkeypatch.setattr(tx_methods.deps, "get_chain_id", lambda: 1337)
    monkeypatch.setattr(tx_methods, "_PQ_VERIFY_OPTIONAL", True)
    monkeypatch.setattr(tx_methods, "_pq_verify", None)

    with pytest.raises(rpc_errors.RpcError) as exc_info:
        tx_methods.tx_send_raw_transaction(raw_hex)
    assert exc_info.value.code == int(rpc_errors.AnimicaCode.NONCE_TOO_LOW)


def test_gap_rejection_stable() -> None:
    service, sender_bytes = _make_service(confirmed_nonce=58)

    tx_dict, raw_bytes, tx_hash_hex = _build_tx(sender_bytes, 60)
    with pytest.raises(NonceGap) as exc_info:
        service.submit(tx=tx_dict, raw=raw_bytes, tx_hash_hex=tx_hash_hex, local=True)
    assert exc_info.value.context["expected_nonce"] == 58

    with pytest.raises(NonceGap):
        service.submit(tx=tx_dict, raw=raw_bytes, tx_hash_hex=tx_hash_hex, local=True)

    assert service.get_next_nonce(sender_bytes, confirmed_nonce=58) == 58
