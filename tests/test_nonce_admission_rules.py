from __future__ import annotations

from unittest.mock import Mock

import pytest

from mempool.errors import NonceGap, NonceTooLow
from mempool.pool import Pool, PoolConfig
from rpc.mempool_service import MempoolService


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
    sender_hex = "0x" + "ab" * 32
    sender_bytes = bytes.fromhex(sender_hex[2:])
    return service, sender_bytes


def _build_tx(sender_bytes: bytes, *, nonce: int, chain_id: int = 1337) -> tuple[dict, bytes, str]:
    from core.encoding.cbor import dumps as cbor_dumps
    from core.utils.hash import sha3_256

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
    tx_dict = {"body": body, "raw": raw_bytes}
    tx_hash = "0x" + sha3_256(raw_bytes).hex()
    return tx_dict, raw_bytes, tx_hash


def test_rejected_too_low_does_not_advance_expected() -> None:
    service, sender_bytes = _make_service(confirmed_nonce=70)

    tx_dict, raw_bytes, tx_hash = _build_tx(sender_bytes, nonce=69)
    with pytest.raises(NonceTooLow) as exc_info:
        service.submit(tx=tx_dict, raw=raw_bytes, tx_hash_hex=tx_hash, local=True)

    assert exc_info.value.context["expected_nonce"] == 70
    assert exc_info.value.context["got_nonce"] == 69
    assert service.get_next_nonce(sender_bytes, confirmed_nonce=70) == 70

    tx_dict_retry, raw_bytes_retry, tx_hash_retry = _build_tx(sender_bytes, nonce=69)
    with pytest.raises(NonceTooLow) as exc_info_retry:
        service.submit(
            tx=tx_dict_retry, raw=raw_bytes_retry, tx_hash_hex=tx_hash_retry, local=True
        )

    assert exc_info_retry.value.context["expected_nonce"] == 70
    assert service.get_next_nonce(sender_bytes, confirmed_nonce=70) == 70


def test_get_next_nonce_matches_admission() -> None:
    service, sender_bytes = _make_service(confirmed_nonce=70)

    tx_70, raw_70, hash_70 = _build_tx(sender_bytes, nonce=70)
    tx_71, raw_71, hash_71 = _build_tx(sender_bytes, nonce=71)
    assert service.submit(tx=tx_70, raw=raw_70, tx_hash_hex=hash_70, local=True) == hash_70
    assert service.submit(tx=tx_71, raw=raw_71, tx_hash_hex=hash_71, local=True) == hash_71

    assert service.get_next_nonce(sender_bytes, confirmed_nonce=70) == 72

    tx_72, raw_72, hash_72 = _build_tx(sender_bytes, nonce=72)
    assert service.submit(tx=tx_72, raw=raw_72, tx_hash_hex=hash_72, local=True) == hash_72
    assert service.has_hash(hash_72)


def test_gap_rejection_expected_nonce_stable() -> None:
    service, sender_bytes = _make_service(confirmed_nonce=70)

    tx_gap, raw_gap, hash_gap = _build_tx(sender_bytes, nonce=72)
    with pytest.raises(NonceGap) as exc_info:
        service.submit(tx=tx_gap, raw=raw_gap, tx_hash_hex=hash_gap, local=True)

    assert exc_info.value.context["expected_nonce"] == 70
    assert exc_info.value.context["got_nonce"] == 72
    assert service.get_next_nonce(sender_bytes, confirmed_nonce=70) == 70

    tx_gap_retry, raw_gap_retry, hash_gap_retry = _build_tx(sender_bytes, nonce=72)
    with pytest.raises(NonceGap) as exc_info_retry:
        service.submit(
            tx=tx_gap_retry,
            raw=raw_gap_retry,
            tx_hash_hex=hash_gap_retry,
            local=True,
        )

    assert exc_info_retry.value.context["expected_nonce"] == 70
    assert service.get_next_nonce(sender_bytes, confirmed_nonce=70) == 70
