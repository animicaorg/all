"""
Test to reproduce the "nonce chasing" bug where expected nonce increases on every retry.

This test simulates the mainnet scenario where:
1. A tx is submitted with nonce N
2. RPC returns "accepted" (tx_hash)
3. But mempool.getStatus shows "rejected" with "nonce_too_low"
4. CLI retries with the "expected" nonce from error details
5. Each retry causes "expected" to increase (N→N+1→N+2...)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import Mock

import pytest

from mempool.pool import Pool, PoolConfig
from mempool.types import EffectiveFee, PoolTx, TxMeta
from mempool.errors import NonceTooLow
from rpc.mempool_service import MempoolService


def make_pool_tx(sender: str, nonce: int, tx_hash_suffix: str = "") -> tuple[PoolTx, TxMeta]:
    """Create a PoolTx for testing."""
    tx_hash = f"hash_{sender}_{nonce}{tx_hash_suffix}".encode().ljust(32, b"\x00")
    
    tx = Mock()
    tx.body = Mock()
    tx.body.nonce = nonce
    
    raw = f"raw_{sender}_{nonce}".encode()
    
    meta = TxMeta(
        sender=sender,
        nonce=nonce,
        gas_limit=21000,
        size_bytes=len(raw),
        first_seen=time.time(),
        local=True,
        effective_fee_wei=1,
        origin="test",
        peer_id=None,
    )
    
    pool_tx = PoolTx(
        tx=tx,
        tx_hash=tx_hash,
        raw=raw,
        meta=meta,
        fee=EffectiveFee.from_legacy(1),
    )
    
    return pool_tx, meta


def test_nonce_chasing_scenario():
    """
    Reproduce the exact scenario from the mainnet bug report.
    
    Setup:
    - confirmed_nonce = 58
    - No pending txs initially
    
    Bug scenario:
    1. Submit tx with nonce 57 (too low) → expected=58
    2. Submit tx with nonce 58 (correct) → should work, but bug might cause expected=59
    3. Submit tx with nonce 58 again → expected=59 (chasing!)
    4. Submit tx with nonce 59 → expected=60 (still chasing!)
    
    Expected behavior:
    - Rejection of nonce 57 should NOT affect pending nonce state
    - Expected nonce should remain stable at 58 until a valid tx is accepted
    - After accepting nonce 58, expected should become 59 (not before!)
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=58)  # confirmed nonce is 58
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,  # Bypass fee validation for nonce testing
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender = "0x" + "aa" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    # Initial state: no pending txs
    next_nonce = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce == 58, f"Initial next nonce should be 58, got {next_nonce}"
    
    # Step 1: Try to submit nonce 57 (too low) - should be rejected
    try:
        from core.encoding.cbor import dumps as cbor_dumps
        from core.utils.hash import sha3_256
        
        body_57 = {
            "from": sender_bytes,
            "nonce": 57,
            "gasLimit": 21000,
            "chainId": 1337,
            "gasPrice": 1000000000,  # 1 gwei
            "maxFee": 1000000000,
        }
        tx_envelope_57 = {"body": body_57}
        raw_bytes_57 = cbor_dumps(tx_envelope_57)
        tx_dict_57 = tx_envelope_57.copy()
        tx_dict_57["raw"] = raw_bytes_57
        tx_hash_57 = "0x" + sha3_256(raw_bytes_57).hex()
        
        with pytest.raises(NonceTooLow) as exc_info:
            service.submit(tx=tx_dict_57, raw=raw_bytes_57, tx_hash_hex=tx_hash_57, local=True)
        
        # Verify rejection details
        assert exc_info.value.context["got_nonce"] == 57
        assert exc_info.value.context["expected_nonce"] == 58
        
    except ImportError:
        pytest.skip("CBOR encoding not available")
    
    # CRITICAL: After rejection, next nonce should STILL be 58
    next_nonce_after_reject = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_reject == 58, \
        f"After rejecting nonce 57, next nonce should still be 58, got {next_nonce_after_reject}"
    
    # Verify tx is NOT in mempool
    assert not service.has_hash(tx_hash_57), "Rejected tx should not be in mempool"
    
    # Verify pending_nonce is still None (no pending txs)
    pending = service.pending_nonce(sender_bytes)
    assert pending is None, f"Should have no pending nonce after rejection, got {pending}"
    
    # Step 2: Submit nonce 58 (correct) - should succeed
    body_58 = {
        "from": sender_bytes,
        "nonce": 58,
        "gasLimit": 21000,
        "chainId": 1337,
        "gasPrice": 1000000000,  # 1 gwei
        "maxFee": 1000000000,
    }
    tx_envelope_58 = {"body": body_58}
    raw_bytes_58 = cbor_dumps(tx_envelope_58)
    tx_dict_58 = tx_envelope_58.copy()
    tx_dict_58["raw"] = raw_bytes_58
    tx_hash_58 = "0x" + sha3_256(raw_bytes_58).hex()
    
    result_58 = service.submit(tx=tx_dict_58, raw=raw_bytes_58, tx_hash_hex=tx_hash_58, local=True)
    assert result_58 == tx_hash_58, "Submit should return tx hash on success"
    
    # Verify tx IS in mempool
    assert service.has_hash(tx_hash_58), "Accepted tx should be in mempool"
    
    # Now next nonce should be 59
    next_nonce_after_58 = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_58 == 59, \
        f"After accepting nonce 58, next nonce should be 59, got {next_nonce_after_58}"
    
    # Step 3: Try to submit nonce 58 again (duplicate) - should be idempotent or rejected
    # It should return the same hash (idempotent) since it's already in pool
    result_58_dup = service.submit(tx=tx_dict_58, raw=raw_bytes_58, tx_hash_hex=tx_hash_58, local=True)
    assert result_58_dup == tx_hash_58, "Duplicate submit should return same hash"
    
    # Next nonce should STILL be 59 (not 60!)
    next_nonce_after_dup = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_dup == 59, \
        f"After duplicate submit, next nonce should still be 59, got {next_nonce_after_dup}"
    
    # Step 4: Try to submit nonce 60 (gap) - should be rejected
    body_60 = {
        "from": sender_bytes,
        "nonce": 60,
        "gasLimit": 21000,
        "chainId": 1337,
        "gasPrice": 1000000000,  # 1 gwei
        "maxFee": 1000000000,
    }
    tx_envelope_60 = {"body": body_60}
    raw_bytes_60 = cbor_dumps(tx_envelope_60)
    tx_dict_60 = tx_envelope_60.copy()
    tx_dict_60["raw"] = raw_bytes_60
    tx_hash_60 = "0x" + sha3_256(raw_bytes_60).hex()
    
    from mempool.errors import NonceGap
    with pytest.raises(NonceGap) as exc_info_60:
        service.submit(tx=tx_dict_60, raw=raw_bytes_60, tx_hash_hex=tx_hash_60, local=True)
    
    # Verify rejection details
    assert exc_info_60.value.context["got_nonce"] == 60
    assert exc_info_60.value.context["expected_nonce"] == 59
    
    # CRITICAL: Next nonce should STILL be 59 (not 60 or 61!)
    next_nonce_after_gap = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_gap == 59, \
        f"After rejecting gap nonce 60, next nonce should still be 59, got {next_nonce_after_gap}"


def test_rapid_retry_loop():
    """
    Test that rapid retries with rejected nonces don't cause drift.
    
    This simulates a client that keeps retrying with the "expected" nonce
    from error messages.
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=100)
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,  # Bypass fee validation for nonce testing
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender = "0x" + "bb" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    try:
        from core.encoding.cbor import dumps as cbor_dumps
        from core.utils.hash import sha3_256
    except ImportError:
        pytest.skip("CBOR encoding not available")
    
    # Simulate 10 rapid retries with too-low nonce
    for i in range(10):
        nonce = 95  # Always too low
        
        body = {
            "from": sender_bytes,
            "nonce": nonce,
            "gasLimit": 21000,
            "chainId": 1337,
            "gasPrice": 1000000000,  # 1 gwei
            "maxFee": 1000000000,
        }
        tx_envelope = {"body": body}
        raw_bytes = cbor_dumps(tx_envelope)
        tx_dict = tx_envelope.copy()
        tx_dict["raw"] = raw_bytes
        tx_hash = "0x" + sha3_256(raw_bytes).hex()
        
        try:
            service.submit(tx=tx_dict, raw=raw_bytes, tx_hash_hex=tx_hash, local=True)
        except NonceTooLow as exc:
            # Expected nonce should ALWAYS be 100, never increase
            expected = exc.context["expected_nonce"]
            assert expected == 100, \
                f"Retry {i}: expected nonce should always be 100, got {expected}"
        
        # Verify next nonce is stable
        next_nonce = service.get_next_nonce(sender_bytes, confirmed_nonce=100)
        assert next_nonce == 100, \
            f"Retry {i}: next nonce should always be 100, got {next_nonce}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
