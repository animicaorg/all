"""Integration tests for PTL (Pending Transaction Ledger) system."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from core.ptl.model import TxStatus
from core.ptl.service import PtlService
from core.ptl.store import PtlStore
from core.utils.hash import sha3_256


@pytest.fixture
def ptl_store():
    """Create a temporary PTL store."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "ptl.db"
        store = PtlStore(db_path)
        yield store
        store.close()


@pytest.fixture
def ptl_service(ptl_store):
    """Create a PTL service."""
    return PtlService(ptl_store, ttl_seconds=3600, min_peer_acks=2)


@pytest.mark.asyncio
async def test_ptl_submit_and_get(ptl_service):
    """Test submitting and retrieving a transaction."""
    tx_bytes = b"test transaction data"
    
    txid, entry = await ptl_service.submit(tx_bytes, origin="test")
    
    assert txid == sha3_256(tx_bytes)
    assert entry.status == TxStatus.STORED
    assert entry.tx_bytes == tx_bytes
    assert entry.origin == "test"
    
    # Retrieve the transaction
    retrieved = await ptl_service.get(txid)
    assert retrieved is not None
    assert retrieved.txid == txid
    assert retrieved.status == TxStatus.STORED


@pytest.mark.asyncio
async def test_ptl_status_lifecycle(ptl_service):
    """Test transaction status lifecycle."""
    tx_bytes = b"lifecycle test transaction"
    txid, entry = await ptl_service.submit(tx_bytes, origin="test")
    
    # Initial status
    assert entry.status == TxStatus.STORED
    
    # Update to ANNOUNCED
    await ptl_service.update_status(txid, TxStatus.ANNOUNCED)
    entry = await ptl_service.get(txid)
    assert entry.status == TxStatus.ANNOUNCED
    
    # Update to REPLICATING
    await ptl_service.update_status(txid, TxStatus.REPLICATING)
    entry = await ptl_service.get(txid)
    assert entry.status == TxStatus.REPLICATING
    
    # Add receipts to reach ATTESTED
    await ptl_service.add_receipt(txid, "peer1", "ack")
    entry = await ptl_service.get(txid)
    assert entry.ack_count() == 1
    
    await ptl_service.add_receipt(txid, "peer2", "ack")
    entry = await ptl_service.get(txid)
    assert entry.ack_count() == 2
    assert entry.status == TxStatus.ATTESTED  # Should auto-update to ATTESTED
    
    # Mark as included
    await ptl_service.mark_included(txid, height=100)
    entry = await ptl_service.get(txid)
    assert entry.status == TxStatus.INCLUDED
    assert entry.included_height == 100


@pytest.mark.asyncio
async def test_ptl_receipts(ptl_service):
    """Test replication receipt tracking."""
    tx_bytes = b"receipt test transaction"
    txid, _ = await ptl_service.submit(tx_bytes, origin="test")
    
    # Add acknowledgment receipt
    await ptl_service.add_receipt(txid, "peer1", "ack", reason="received")
    
    # Add reject receipt
    await ptl_service.add_receipt(txid, "peer2", "reject", reason="invalid")
    
    # Get replication status
    status = await ptl_service.get_replication_status(txid)
    assert status is not None
    assert status["ack_count"] == 1
    assert len(status["receipts"]) == 2
    
    receipts = status["receipts"]
    ack_receipt = next(r for r in receipts if r["status"] == "ack")
    reject_receipt = next(r for r in receipts if r["status"] == "reject")
    
    assert ack_receipt["peer_id"] == "peer1"
    assert ack_receipt["reason"] == "received"
    assert reject_receipt["peer_id"] == "peer2"
    assert reject_receipt["reason"] == "invalid"


@pytest.mark.asyncio
async def test_ptl_pending_list(ptl_service):
    """Test listing pending transactions."""
    # Submit multiple transactions
    for i in range(5):
        tx_bytes = f"test transaction {i}".encode()
        await ptl_service.submit(tx_bytes, origin=f"test{i}")
    
    # Get pending transactions
    pending = await ptl_service.get_pending(limit=10)
    assert len(pending) == 5
    
    # Mark one as included
    txid = pending[0].txid
    await ptl_service.mark_included(txid, height=100)
    
    # Should now have 4 pending
    pending = await ptl_service.get_pending(limit=10)
    assert len(pending) == 4


@pytest.mark.asyncio
async def test_ptl_mining_selection(ptl_service):
    """Test transaction selection for mining."""
    # Submit transactions with different fees
    for i in range(10):
        tx_bytes = f"mining test transaction {i}".encode()
        txid, entry = await ptl_service.submit(tx_bytes, origin=f"test{i}")
        
        # Set different fees
        ptl_service.store.update_status(
            txid, TxStatus.ATTESTED, time.time(), **{"fee": i * 1000}
        )
    
    # Get transactions for mining
    mining_txs = await ptl_service.get_for_mining(limit=5)
    assert len(mining_txs) <= 5
    
    # Should be sorted by fee (highest first)
    fees = [tx.fee for tx in mining_txs]
    assert fees == sorted(fees, reverse=True)


@pytest.mark.asyncio
async def test_ptl_expiration(ptl_service):
    """Test transaction expiration."""
    # Submit transaction with short TTL
    service = PtlService(ptl_service.store, ttl_seconds=1, min_peer_acks=2)
    
    tx_bytes = b"expiring transaction"
    txid, entry = await service.submit(tx_bytes, origin="test")
    
    assert entry.status == TxStatus.STORED
    assert entry.expire_at is not None
    
    # Wait for expiration
    await asyncio.sleep(2)
    
    # Mark expired
    now = time.time()
    expired_count = service.store.mark_expired(now)
    assert expired_count == 1
    
    # Check status
    entry = await service.get(txid)
    assert entry.status == TxStatus.EXPIRED


@pytest.mark.asyncio
async def test_ptl_reject(ptl_service):
    """Test marking transaction as rejected."""
    tx_bytes = b"rejected transaction"
    txid, entry = await ptl_service.submit(tx_bytes, origin="test")
    
    # Mark as rejected
    await ptl_service.mark_rejected(txid, reason="invalid signature")
    
    entry = await ptl_service.get(txid)
    assert entry.status == TxStatus.REJECTED
    assert entry.reject_reason == "invalid signature"


@pytest.mark.asyncio
async def test_ptl_stats(ptl_service):
    """Test PTL statistics."""
    # Submit transactions in various states
    tx1 = await ptl_service.submit(b"tx1", origin="test")
    tx2 = await ptl_service.submit(b"tx2", origin="test")
    tx3 = await ptl_service.submit(b"tx3", origin="test")
    
    await ptl_service.mark_included(tx1[0], height=100)
    await ptl_service.mark_rejected(tx2[0], reason="test")
    
    # Get stats
    stats = ptl_service.get_stats()
    
    assert stats[TxStatus.INCLUDED.value] == 1
    assert stats[TxStatus.REJECTED.value] == 1
    assert stats[TxStatus.STORED.value] == 1


@pytest.mark.asyncio
async def test_ptl_duplicate_submission(ptl_service):
    """Test that duplicate submissions are idempotent."""
    tx_bytes = b"duplicate test transaction"
    
    # Submit first time
    txid1, entry1 = await ptl_service.submit(tx_bytes, origin="test1")
    
    # Submit again
    txid2, entry2 = await ptl_service.submit(tx_bytes, origin="test2")
    
    # Should be same transaction
    assert txid1 == txid2
    assert entry1.txid == entry2.txid
    
    # Origin should be from first submission
    assert entry2.origin == "test1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
