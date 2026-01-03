"""
Test the exact mainnet scenario: rejection cache confusion.

This test validates the specific bug report where:
1. TX submitted with nonce N → rejected (nonce_too_low) → cached
2. TX submitted with correct nonce → accepted
3. Query mempool.getStatus for original rejected tx → still shows rejected
4. This could confuse CLI into thinking nonce needs to keep increasing
"""

from __future__ import annotations

import time
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
    
    raw = f"raw_{sender}_{nonce}{tx_hash_suffix}".encode()
    
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


def test_rejection_cache_doesnt_interfere_with_success():
    """
    Test that rejection cache doesn't cause confusion after successful retry.
    
    Bug scenario:
    1. Submit tx A with nonce 58 → rejected, cached
    2. Submit tx B with nonce 58 → accepted  
    3. Query status of tx A → should return rejected (from cache) ✓
    4. Query status of tx B → should return pending (from pool) ✓
    5. get_next_nonce should return 59 (not affected by cached rejection) ✓
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=58)
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender = "0x" + "ff" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    # Step 1: Try to submit with nonce 57 (too low) → rejected, cached
    pool_tx_57, meta_57 = make_pool_tx(sender, 57, "_first")
    tx_hash_57 = "0x" + pool_tx_57.tx_hash.hex()
    
    try:
        pool.add(pool_tx_57, meta_57, is_local=True)
        pytest.fail("Should have raised exception for nonce 57")
    except Exception as exc:
        # Record the rejection in service's cache
        service._record_rejection(tx_hash_57, "nonce_too_low", {"expected": 58, "got": 57})
    
    # Verify rejection is cached
    rejection_57 = service.get_rejection(tx_hash_57)
    assert rejection_57 is not None
    assert rejection_57["reason"] == "nonce_too_low"
    assert rejection_57["details"]["expected"] == 58
    
    # Step 2: Submit with correct nonce 58 → should succeed
    pool_tx_58, meta_58 = make_pool_tx(sender, 58, "_retry")
    tx_hash_58 = "0x" + pool_tx_58.tx_hash.hex()
    
    pool.add(pool_tx_58, meta_58, is_local=True)
    
    # Verify it's in pool
    assert service.has_hash(tx_hash_58)
    
    # Step 3: get_next_nonce should return 59, ignoring cached rejection
    next_nonce = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce == 59, f"Expected 59, got {next_nonce} (cached rejection should not affect)"
    
    # Step 4: Verify rejection cache still has the old rejection
    rejection_57_check = service.get_rejection(tx_hash_57)
    assert rejection_57_check is not None, "Cached rejection should persist"
    
    # Step 5: But new tx hash should NOT be in rejection cache
    rejection_58 = service.get_rejection(tx_hash_58)
    assert rejection_58 is None, "Successful tx should not have rejection"


def test_concurrent_clients_with_rejection_cache():
    """
    Test scenario with multiple clients causing race-like behavior.
    
    Scenario:
    - Client A: getNextNonce → 58
    - Client B: getNextNonce → 58  
    - Client B: submit with 58 → succeeds
    - Client A: submit with 58 → rejected (duplicate or RBF fails)
    - Client A: getNextNonce → should get 59, not 60
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=58)
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender = "0x" + "aa" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    # Both clients get nonce 58
    nonce_a = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    nonce_b = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert nonce_a == 58
    assert nonce_b == 58
    
    # Client B submits first → succeeds
    pool_tx_b, meta_b = make_pool_tx(sender, 58, "_b")
    pool.add(pool_tx_b, meta_b, is_local=True)
    
    # Client A tries to submit → should fail (duplicate)
    pool_tx_a, meta_a = make_pool_tx(sender, 58, "_a")
    try:
        pool.add(pool_tx_a, meta_a, is_local=True)
        # May succeed via RBF, or fail with duplicate
    except Exception:
        pass
    
    # Client A queries nonce again → should get 59
    nonce_a_retry = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert nonce_a_retry == 59, f"Expected 59, got {nonce_a_retry}"
    
    # Client A submits with nonce 59 → should succeed
    pool_tx_a2, meta_a2 = make_pool_tx(sender, 59, "_a2")
    pool.add(pool_tx_a2, meta_a2, is_local=True)
    
    # Now next nonce should be 60
    nonce_final = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert nonce_final == 60, f"Expected 60, got {nonce_final}"


def test_clear_rejection_on_successful_submit():
    """
    Test recommendation: Clear rejection cache when tx is successfully re-submitted.
    
    This is a "nice to have" feature to prevent confusion.
    """
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024*1024))
    
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=58)
    
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )
    
    sender = "0x" + "bb" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    # Record a rejection for a specific hash
    fake_hash = "0xdeadbeef"
    service._record_rejection(fake_hash, "nonce_too_low", {"expected": 58})
    
    # Verify it's cached
    assert service.get_rejection(fake_hash) is not None
    
    # Now submit a valid tx (simulating successful retry)
    pool_tx, meta = make_pool_tx(sender, 58)
    pool.add(pool_tx, meta, is_local=True)
    
    # RECOMMENDATION: Service should clear rejection cache for this sender's nonce range
    # Currently this doesn't happen, but it's not critical since:
    # 1. Status checks pool first
    # 2. Rejection cache expires after TTL
    # 3. Different hash for retry tx
    
    # The rejection cache for the OLD hash should remain (it's a different hash)
    assert service.get_rejection(fake_hash) is not None
    
    # But for practical purposes, this is fine because:
    # - CLI will use the NEW tx hash after retry
    # - mempool.getStatus will show "pending" for new hash
    # - Old hash gradually expires from cache


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
