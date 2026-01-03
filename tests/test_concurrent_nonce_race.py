"""
Test to reproduce concurrent nonce race conditions that could cause "nonce chasing".

This test attempts to find race conditions where:
1. Multiple threads submit txs concurrently
2. Nonce validation passes but tx addition fails
3. Partial state updates cause nonce drift
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Any
from unittest.mock import Mock

import pytest

from mempool.pool import Pool, PoolConfig
from mempool.types import EffectiveFee, PoolTx, TxMeta
from mempool.errors import NonceTooLow, NonceGap
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


def test_concurrent_submit_same_sender():
    """
    Test that concurrent submissions from the same sender don't cause nonce drift.
    
    Scenario:
    - 10 threads all try to submit with sequential nonces (58-67)
    - Only one should succeed per nonce
    - get_next_nonce should always return correct value
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
    
    sender = "0x" + "cc" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    results = []
    errors = []
    
    def try_submit(nonce: int):
        """Try to submit a tx with given nonce."""
        try:
            pool_tx, meta = make_pool_tx(sender, nonce, f"_thread_{nonce}")
            # Directly add to pool (bypassing full submit validation for speed)
            pool.add(pool_tx, meta, is_local=True)
            return ("success", nonce, None)
        except Exception as exc:
            return ("error", nonce, str(exc))
    
    # Submit 10 transactions concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(try_submit, nonce) for nonce in range(58, 68)]
        results = [f.result() for f in futures]
    
    # Check results
    successes = [r for r in results if r[0] == "success"]
    errors = [r for r in results if r[0] == "error"]
    
    print(f"Successes: {len(successes)}, Errors: {len(errors)}")
    print(f"Success nonces: {sorted([r[1] for r in successes])}")
    print(f"Error nonces: {sorted([r[1] for r in errors])}")
    
    # All should succeed (different nonces)
    assert len(successes) == 10, f"Expected 10 successes, got {len(successes)}"
    
    # Check that get_next_nonce returns correct value
    next_nonce = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert next_nonce == 68, f"Expected next nonce 68, got {next_nonce}"


def test_concurrent_getNextNonce_and_submit():
    """
    Test race between getNextNonce and submit operations.
    
    Scenario:
    - Thread A: calls getNextNonce → gets 58
    - Thread B: submits with nonce 58 → succeeds
    - Thread A: submits with nonce 58 → should be rejected (duplicate)
    - Thread A: calls getNextNonce again → should get 59 (not 60!)
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
    
    sender = "0x" + "dd" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    # Thread A gets nonce
    nonce_a = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert nonce_a == 58
    
    # Thread B submits with same nonce
    pool_tx_b, meta_b = make_pool_tx(sender, 58, "_b")
    pool.add(pool_tx_b, meta_b, is_local=True)
    
    # Thread A tries to submit (should fail - duplicate)
    pool_tx_a, meta_a = make_pool_tx(sender, 58, "_a")
    with pytest.raises(Exception):  # Should raise DuplicateTx or similar
        pool.add(pool_tx_a, meta_a, is_local=True)
    
    # Thread A queries nonce again - should be 59, not 60
    nonce_a_retry = service.get_next_nonce(sender_bytes, confirmed_nonce=58)
    assert nonce_a_retry == 59, \
        f"After B's submit, next nonce should be 59, got {nonce_a_retry}"


def test_rapid_parallel_retries():
    """
    Test rapid parallel retries with rejected nonces.
    
    This simulates a buggy client that rapidly retries without waiting.
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
    
    sender = "0x" + "ee" * 32
    sender_bytes = bytes.fromhex(sender[2:])
    
    # Submit valid tx at nonce 100
    pool_tx_100, meta_100 = make_pool_tx(sender, 100)
    pool.add(pool_tx_100, meta_100, is_local=True)
    
    # Now spawn 5 threads that all try to query nonce and submit
    def query_and_check():
        """Query nonce multiple times and verify stability."""
        nonces = []
        for _ in range(10):
            n = service.get_next_nonce(sender_bytes, confirmed_nonce=100)
            nonces.append(n)
            time.sleep(0.001)  # Small delay
        return nonces
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(query_and_check) for _ in range(5)]
        all_nonces = [f.result() for f in futures]
    
    # All threads should consistently see nonce 101
    for thread_nonces in all_nonces:
        assert all(n == 101 for n in thread_nonces), \
            f"All queries should return 101, got {thread_nonces}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
