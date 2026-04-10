"""
Tests for mempool2.storage
"""

import tempfile
from pathlib import Path

import pytest

from coretx import TxAuth, TxBody, TxEnvelope, TxId, TxKind
from coretx.canonical import compute_txid

from mempool2.storage import MempoolStorage
from mempool2.types import MempoolEntry, TxSource


def make_test_envelope(nonce: int = 0, fee: int = 21000, sender: bytes = None) -> TxEnvelope:
    """Helper to create test envelope"""
    if sender is None:
        sender = b"a" * 32
    
    body = TxBody(
        version=1,
        chain_id=1,
        nonce=nonce,
        from_addr=sender,
        to_addr=b"b" * 32,
        value=1000,
        fee=fee,
        gas_limit=21000,
        data=b"",
        memo="",
        timestamp=1234567890,
        kind=TxKind.TRANSFER,
    )
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pk" * 32,
        signature_bytes=b"sig" * 64,
        prehash_id=2,
    )
    # Create temporary envelope to compute real txid
    temp_envelope = TxEnvelope(body=body, auth=auth, txid=TxId(b"\x00" * 32))
    txid = compute_txid(temp_envelope)
    
    return TxEnvelope(body=body, auth=auth, txid=txid)


@pytest.fixture
def storage():
    """Create temporary storage"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mempool.db"
        store = MempoolStorage(db_path)
        yield store
        store.close()


class TestStorageBasics:
    """Basic storage operations"""
    
    def test_add_and_get_tx(self, storage):
        """Add and retrieve transaction"""
        envelope = make_test_envelope(nonce=0)
        entry = MempoolEntry(
            envelope=envelope,
            arrival_time=1234567890.0,
            fee_rate=1,
            source=TxSource.RPC,
        )
        
        # Add
        added = storage.add_tx(entry)
        assert added is True
        
        # Retrieve
        retrieved = storage.get_tx(envelope.txid)
        assert retrieved is not None
        assert retrieved.txid == envelope.txid
        assert retrieved.nonce == 0
        assert retrieved.fee_rate == 1
    
    def test_add_duplicate(self, storage):
        """Adding duplicate returns False"""
        envelope = make_test_envelope(nonce=0)
        entry = MempoolEntry(
            envelope=envelope,
            arrival_time=1234567890.0,
            fee_rate=1,
            source=TxSource.RPC,
        )
        
        added1 = storage.add_tx(entry)
        added2 = storage.add_tx(entry)
        
        assert added1 is True
        assert added2 is False
    
    def test_remove_tx(self, storage):
        """Remove transaction"""
        envelope = make_test_envelope(nonce=0)
        entry = MempoolEntry(
            envelope=envelope,
            arrival_time=1234567890.0,
            fee_rate=1,
            source=TxSource.RPC,
        )
        
        storage.add_tx(entry)
        
        # Remove
        removed = storage.remove_tx(envelope.txid)
        assert removed is True
        
        # Should be gone
        retrieved = storage.get_tx(envelope.txid)
        assert retrieved is None
        
        # Remove again returns False
        removed2 = storage.remove_tx(envelope.txid)
        assert removed2 is False
    
    def test_has_tx(self, storage):
        """Check transaction existence"""
        envelope = make_test_envelope(nonce=0)
        entry = MempoolEntry(
            envelope=envelope,
            arrival_time=1234567890.0,
            fee_rate=1,
            source=TxSource.RPC,
        )
        
        assert storage.has_tx(envelope.txid) is False
        
        storage.add_tx(entry)
        assert storage.has_tx(envelope.txid) is True
        
        storage.remove_tx(envelope.txid)
        assert storage.has_tx(envelope.txid) is False


class TestStorageQueries:
    """Storage query operations"""
    
    def test_list_txs_empty(self, storage):
        """List empty mempool"""
        txs = storage.list_txs()
        assert txs == []
    
    def test_list_txs_ordered_by_fee(self, storage):
        """Transactions listed by fee rate descending"""
        # Add txs with different fee rates
        for i, fee in enumerate([21000, 42000, 10000]):  # rates: 1, 2, 0.47
            envelope = make_test_envelope(nonce=i, fee=fee)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0 + i,
                fee_rate=fee // 21000,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        txs = storage.list_txs()
        assert len(txs) == 3
        # Should be ordered: 2, 1, 0 (by fee rate)
        assert txs[0].fee_rate == 2
        assert txs[1].fee_rate == 1
        assert txs[2].fee_rate == 0
    
    def test_iter_by_fee_descending(self, storage):
        """Iterate by fee descending"""
        for i, fee in enumerate([21000, 42000, 10000]):
            envelope = make_test_envelope(nonce=i, fee=fee)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=fee // 21000,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        fees = [entry.fee_rate for entry in storage.iter_by_fee(descending=True)]
        assert fees == [2, 1, 0]
    
    def test_iter_by_fee_ascending(self, storage):
        """Iterate by fee ascending"""
        for i, fee in enumerate([21000, 42000, 10000]):
            envelope = make_test_envelope(nonce=i, fee=fee)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=fee // 21000,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        fees = [entry.fee_rate for entry in storage.iter_by_fee(descending=False)]
        assert fees == [0, 1, 2]
    
    def test_get_sender_txs(self, storage):
        """Get transactions by sender"""
        sender_a = b"a" * 32
        sender_b = b"b" * 32
        
        # Add txs from two senders
        for nonce in [0, 2, 1]:  # Out of order
            envelope = make_test_envelope(nonce=nonce, sender=sender_a)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        envelope_b = make_test_envelope(nonce=0, sender=sender_b)
        entry_b = MempoolEntry(
            envelope=envelope_b,
            arrival_time=1234567890.0,
            fee_rate=1,
            source=TxSource.RPC,
        )
        storage.add_tx(entry_b)
        
        # Get sender A txs
        txs_a = storage.get_sender_txs(sender_a)
        assert len(txs_a) == 3
        # Should be sorted by nonce
        assert [tx.nonce for tx in txs_a] == [0, 1, 2]
        
        # Get sender B txs
        txs_b = storage.get_sender_txs(sender_b)
        assert len(txs_b) == 1
    
    def test_get_sender_nonces(self, storage):
        """Get nonce set for sender"""
        sender = b"a" * 32
        
        for nonce in [0, 1, 5]:
            envelope = make_test_envelope(nonce=nonce, sender=sender)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        nonces = storage.get_sender_nonces(sender)
        assert nonces == {0, 1, 5}
    
    def test_get_sender_pending_debits(self, storage):
        """Calculate pending debits for sender"""
        sender = b"a" * 32
        
        # Add txs: value=1000, fee=21000 each => total 22000 per tx
        for nonce in [0, 1, 2]:
            envelope = make_test_envelope(nonce=nonce, sender=sender)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        debits = storage.get_sender_pending_debits(sender)
        assert debits == 3 * (1000 + 21000)


class TestStorageStats:
    """Storage statistics"""
    
    def test_stats_empty(self, storage):
        """Stats for empty mempool"""
        stats = storage.get_stats()
        assert stats.tx_count == 0
        assert stats.total_bytes == 0
        assert stats.unique_senders == 0
    
    def test_stats_populated(self, storage):
        """Stats for populated mempool"""
        sender_a = b"a" * 32
        sender_b = b"b" * 32
        
        for nonce in [0, 1]:
            envelope = make_test_envelope(nonce=nonce, sender=sender_a, fee=21000)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        envelope_b = make_test_envelope(nonce=0, sender=sender_b, fee=42000)
        entry_b = MempoolEntry(
            envelope=envelope_b,
            arrival_time=1234567890.0,
            fee_rate=2,
            source=TxSource.RPC,
        )
        storage.add_tx(entry_b)
        
        stats = storage.get_stats()
        assert stats.tx_count == 3
        assert stats.total_bytes > 0
        assert stats.unique_senders == 2
        assert stats.fee_stats.min_fee_rate == 1
        assert stats.fee_stats.max_fee_rate == 2
    
    def test_clear(self, storage):
        """Clear all transactions"""
        for nonce in [0, 1, 2]:
            envelope = make_test_envelope(nonce=nonce)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        count = storage.clear()
        assert count == 3
        
        stats = storage.get_stats()
        assert stats.tx_count == 0
