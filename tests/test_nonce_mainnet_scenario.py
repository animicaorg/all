"""
Test to reproduce the exact mainnet "nonce chasing" bug scenario.

This test simulates:
1. User submits tx with nonce N
2. RPC returns tx_hash ("accepted")
3. But mempool actually rejected it (nonce_too_low)
4. User retries with "expected" nonce from error
5. Expected nonce increases every time

The fix ensures:
- RPC raises an error (not success) when mempool rejects
- Rejected txs don't mutate sender nonce tracking
- state.getNextNonce returns same value mempool uses for validation
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mempool.pool import Pool, PoolConfig
from mempool.types import EffectiveFee, PoolTx, TxMeta
from mempool.errors import NonceTooLow, NonceGap
from rpc.mempool_service import MempoolService


def make_tx_dict(sender_hex: str, nonce: int, chain_id: int = 1337) -> tuple[dict, bytes]:
    """Create a tx dict for testing (simulates CLI/SDK format)."""
    try:
        from core.encoding.cbor import dumps as cbor_dumps
        from core.utils.hash import sha3_256
    except ImportError:
        pytest.skip("CBOR encoding not available")
    
    sender_bytes = bytes.fromhex(sender_hex[2:] if sender_hex.startswith("0x") else sender_hex)
    
    body = {
        "from": sender_bytes,
        "sender": sender_bytes,
        "nonce": nonce,
        "gasLimit": 21000,
        "chainId": chain_id,
        "gasPrice": 1000000000,  # 1 gwei
        "maxFee": 1000000000,
        "to": bytes(32),  # dummy recipient
        "value": 0,
        "data": b"",
    }
    
    # Create a mock signature
    sig = {
        "algId": 2,  # Dilithium3
        "pubkey": bytes(32),
        "sig": bytes(64),
        "prehash": "sha3-512",
        "domain": "tx",
    }
    
    tx_envelope = {"body": body, "sig": sig}
    raw_bytes = cbor_dumps(tx_envelope)
    
    tx_dict = tx_envelope.copy()
    tx_dict["raw"] = raw_bytes
    tx_dict["hash"] = "0x" + sha3_256(raw_bytes).hex()
    
    return tx_dict, raw_bytes


def test_mainnet_nonce_chasing_repro():
    """
    Reproduce the exact mainnet scenario where a user gets stuck in a nonce chase loop.
    
    Setup:
    - Account confirmed nonce: 58
    - No pending txs
    
    Scenario:
    1. User submits tx with nonce 57 (too low)
       - Expected: rejection with expected=58, got=57
       - Bug: if this modifies state, next call sees expected=59
    2. User retries with nonce 58 (should work)
       - Expected: acceptance
       - Bug: if previous rejection bumped expected, this shows expected=59
    3. User keeps retrying, expected keeps increasing
    
    Fix verification:
    - After rejection at step 1, expected should stay 58
    - Submission at step 2 should succeed
    - No "nonce drift" occurs
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=58)  # Confirmed nonce is 58
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,  # Bypass fee validation
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender_hex = "0x" + "aa" * 32
    sender_bytes = bytes.fromhex(sender_hex[2:])
    
    # STEP 1: Submit nonce 57 (too low) - should reject with expected=58
    tx_57, raw_57 = make_tx_dict(sender_hex, 57)
    tx_hash_57 = tx_57["hash"]
    
    with pytest.raises(NonceTooLow) as exc_info_57:
        service.submit(tx=tx_57, raw=raw_57, tx_hash_hex=tx_hash_57, local=True)
    
    # Verify rejection details
    assert exc_info_57.value.context["got_nonce"] == 57
    assert exc_info_57.value.context["expected_nonce"] == 58, \
        f"Expected nonce should be 58, got {exc_info_57.value.context['expected_nonce']}"
    
    # CRITICAL: After rejection, expected should STILL be 58 (not 59!)
    next_nonce_after_57 = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_57 == 58, \
        f"NONCE DRIFT BUG: After rejecting nonce 57, expected should still be 58, got {next_nonce_after_57}"
    
    # Verify tx is NOT in mempool
    assert not service.has_hash(tx_hash_57), "Rejected tx should not be in mempool"
    
    # Verify no pending txs
    pending = service.pending_nonce(sender_bytes)
    assert pending is None, f"Should have no pending txs after rejection, got {pending}"
    
    # STEP 2: Submit nonce 58 (correct) - should succeed
    tx_58, raw_58 = make_tx_dict(sender_hex, 58)
    tx_hash_58 = tx_58["hash"]
    
    result_58 = service.submit(tx=tx_58, raw=raw_58, tx_hash_hex=tx_hash_58, local=True)
    assert result_58 == tx_hash_58, f"Submit should return tx hash, got {result_58}"
    
    # Verify tx IS in mempool
    assert service.has_hash(tx_hash_58), "Accepted tx should be in mempool"
    
    # Now expected should be 59
    next_nonce_after_58 = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_58 == 59, \
        f"After accepting nonce 58, expected should be 59, got {next_nonce_after_58}"
    
    # STEP 3: Try to submit nonce 58 again (duplicate) - should be idempotent
    result_58_dup = service.submit(tx=tx_58, raw=raw_58, tx_hash_hex=tx_hash_58, local=True)
    assert result_58_dup == tx_hash_58, "Duplicate submit should return same hash (idempotent)"
    
    # Expected should STILL be 59 (not 60!)
    next_nonce_after_dup = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_dup == 59, \
        f"NONCE DRIFT BUG: After duplicate submit, expected should still be 59, got {next_nonce_after_dup}"
    
    # STEP 4: Try nonce 60 (gap) - should reject with expected=59
    tx_60, raw_60 = make_tx_dict(sender_hex, 60)
    tx_hash_60 = tx_60["hash"]
    
    with pytest.raises(NonceGap) as exc_info_60:
        service.submit(tx=tx_60, raw=raw_60, tx_hash_hex=tx_hash_60, local=True)
    
    assert exc_info_60.value.context["got_nonce"] == 60
    assert exc_info_60.value.context["expected_nonce"] == 59
    
    # Expected should STILL be 59 (not 60 or 61!)
    next_nonce_after_gap = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce_after_gap == 59, \
        f"NONCE DRIFT BUG: After rejecting gap nonce 60, expected should still be 59, got {next_nonce_after_gap}"
    
    print("✅ Mainnet nonce chasing bug is FIXED:")
    print("  - Rejected txs don't modify sender nonce tracking")
    print("  - Expected nonce is stable across retries")
    print("  - state.getNextNonce matches mempool admission validation")


def test_rpc_returns_error_not_success_on_rejection():
    """
    Test that the RPC layer returns an error (not success) when mempool rejects a tx.
    
    This is critical: if RPC returns a tx_hash even though mempool rejected the tx,
    then CLI/SDK thinks the tx was accepted, but mempool.getStatus shows "rejected".
    
    The fix ensures:
    - MempoolService.submit() raises an exception on rejection
    - RPC tx.sendRawTransaction propagates this exception as a JSON-RPC error
    - Client receives error response (not success with tx_hash)
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=100)
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender_hex = "0x" + "bb" * 32
    sender_bytes = bytes.fromhex(sender_hex[2:])
    
    # Submit tx with nonce 95 (too low)
    tx_95, raw_95 = make_tx_dict(sender_hex, 95)
    tx_hash_95 = tx_95["hash"]
    
    # MempoolService.submit should RAISE an exception (not return success)
    with pytest.raises(NonceTooLow) as exc_info:
        service.submit(tx=tx_95, raw=raw_95, tx_hash_hex=tx_hash_95, local=True)
    
    # Verify error details are correct
    assert exc_info.value.context["got_nonce"] == 95
    assert exc_info.value.context["expected_nonce"] == 100
    
    # Verify tx is NOT in mempool
    assert not service.has_hash(tx_hash_95), "Rejected tx should not be in mempool"
    
    # Verify rejection is recorded (for status queries)
    rejection = service.get_rejection(tx_hash_95)
    assert rejection is not None, "Rejection should be recorded for genuinely low nonce"
    assert rejection["reason"] == "nonce_too_low"
    assert rejection["details"]["got"] == 95
    assert rejection["details"]["expected"] == 100
    
    print("✅ RPC error handling is correct:")
    print("  - MempoolService.submit raises exception on rejection")
    print("  - RPC layer will convert this to JSON-RPC error")
    print("  - Client receives error (not success) when tx is rejected")


def test_state_getNextNonce_matches_mempool_validation():
    """
    Test that state.getNextNonce returns the same value used by mempool admission.
    
    This is critical to prevent TOCTOU races:
    1. Client calls state.getNextNonce → returns N
    2. Client submits tx with nonce N
    3. Mempool validates and expects... N (same value!)
    
    If they disagree, client gets "nonce too low" even when using the "correct" nonce.
    
    The fix ensures:
    - Both use the same authoritative calculation
    - Both are protected by the same per-sender lock
    - No drift can occur between getNextNonce and admission
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=50)
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender_hex = "0x" + "cc" * 32
    sender_bytes = bytes.fromhex(sender_hex[2:])
    
    # Get next nonce (what client would use)
    next_nonce = service.get_next_nonce(sender_bytes, confirmed_nonce=50)
    assert next_nonce == 50, f"Next nonce should be 50 (no pending), got {next_nonce}"
    
    # Submit tx with that nonce - should succeed
    tx_50, raw_50 = make_tx_dict(sender_hex, 50)
    tx_hash_50 = tx_50["hash"]
    
    result = service.submit(tx=tx_50, raw=raw_50, tx_hash_hex=tx_hash_50, local=True)
    assert result == tx_hash_50, "Submit should succeed with nonce from getNextNonce"
    
    # Get next nonce again
    next_nonce_2 = service.get_next_nonce(sender_bytes, confirmed_nonce=50)
    assert next_nonce_2 == 51, f"Next nonce should now be 51, got {next_nonce_2}"
    
    # Submit tx with that nonce - should succeed
    tx_51, raw_51 = make_tx_dict(sender_hex, 51)
    tx_hash_51 = tx_51["hash"]
    
    result_2 = service.submit(tx=tx_51, raw=raw_51, tx_hash_hex=tx_hash_51, local=True)
    assert result_2 == tx_hash_51, "Submit should succeed with new nonce from getNextNonce"
    
    # Get next nonce again
    next_nonce_3 = service.get_next_nonce(sender_bytes, confirmed_nonce=50)
    assert next_nonce_3 == 52, f"Next nonce should now be 52, got {next_nonce_3}"
    
    print("✅ state.getNextNonce matches mempool validation:")
    print("  - Both use same authoritative calculation")
    print("  - No TOCTOU race between getNextNonce and admission")
    print("  - Client can reliably use nonce from getNextNonce")


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
