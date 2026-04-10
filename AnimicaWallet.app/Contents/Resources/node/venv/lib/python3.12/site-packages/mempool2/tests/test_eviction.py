"""
Tests for mempool2.evict
"""

import tempfile
import time
from pathlib import Path

import pytest

from coretx import TxAuth, TxBody, TxEnvelope, TxId, TxKind
from coretx.canonical import compute_txid

from mempool2.evict import check_capacity, evict_expired, evict_lowest_fee, per_sender_limit
from mempool2.storage import MempoolStorage
from mempool2.types import MempoolEntry, TxSource


def make_test_envelope(
    nonce: int = 0,
    fee: int = 21000,
    sender: bytes = None,
    data: bytes = b"",
) -> TxEnvelope:
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
        data=data,
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


class TestEvictLowestFee:
    """Tests for evict_lowest_fee()"""
    
    def test_evict_zero(self, storage):
        """Evicting zero returns empty list"""
        result = evict_lowest_fee(storage, 0)
        assert result == []
    
    def test_evict_from_empty(self, storage):
        """Evicting from empty mempool returns empty"""
        result = evict_lowest_fee(storage, 5)
        assert result == []
    
    def test_evict_lowest_fee_deterministic(self, storage):
        """Eviction is deterministic by fee rate"""
        # Add txs with different fee rates
        fees = [42000, 21000, 10000, 30000]  # rates: 2, 1, 0, 1.4
        for i, fee in enumerate(fees):
            envelope = make_test_envelope(nonce=i, fee=fee)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0 + i,
                fee_rate=fee // 21000,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        # Evict 2 lowest
        to_evict = evict_lowest_fee(storage, 2)
        assert len(to_evict) == 2
        
        # Should evict nonce=2 (rate=0) and nonce=1 (rate=1)
        evicted_nonces = []
        for txid in to_evict:
            entry = storage.get_tx(txid)
            if entry:
                evicted_nonces.append(entry.nonce)
        
        # Lowest fee rates should be evicted
        assert 2 in evicted_nonces  # rate=0
    
    def test_evict_all(self, storage):
        """Can evict all transactions"""
        for i in range(5):
            envelope = make_test_envelope(nonce=i)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        to_evict = evict_lowest_fee(storage, 10)
        assert len(to_evict) == 5


class TestCheckCapacity:
    """Tests for check_capacity()"""
    
    def test_under_capacity(self, storage):
        """No eviction needed when under capacity"""
        for i in range(3):
            envelope = make_test_envelope(nonce=i)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        to_evict = check_capacity(storage, max_txs=10, max_bytes=1000000)
        assert to_evict == []
    
    def test_over_tx_count(self, storage):
        """Evict when over tx count limit"""
        for i in range(5):
            envelope = make_test_envelope(nonce=i, fee=21000 * (i + 1))
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=(i + 1),
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        # Limit to 3 txs
        to_evict = check_capacity(storage, max_txs=3, max_bytes=1000000)
        assert len(to_evict) >= 2  # Should evict at least 2
    
    def test_over_byte_limit(self, storage):
        """Evict when over byte limit"""
        # Add txs with large data
        for i in range(3):
            envelope = make_test_envelope(nonce=i, data=b"x" * 10000)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=i,  # Different fees for deterministic ordering
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        # Set very low byte limit
        to_evict = check_capacity(storage, max_txs=100, max_bytes=5000)
        assert len(to_evict) > 0


class TestPerSenderLimit:
    """Tests for per_sender_limit()"""
    
    def test_under_limit(self, storage):
        """No eviction when under limit"""
        sender = b"a" * 32
        
        for nonce in [0, 1, 2]:
            envelope = make_test_envelope(nonce=nonce, sender=sender)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        to_evict = per_sender_limit(storage, sender, max_per_sender=5)
        assert to_evict == []
    
    def test_over_limit_evicts_highest_nonce(self, storage):
        """Evict highest nonces when over limit"""
        sender = b"a" * 32
        
        # Add 5 txs with nonces 0-4
        for nonce in [0, 1, 2, 3, 4]:
            envelope = make_test_envelope(nonce=nonce, sender=sender)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        # Limit to 3
        to_evict = per_sender_limit(storage, sender, max_per_sender=3)
        assert len(to_evict) == 2
        
        # Should evict nonces 3 and 4 (highest)
        evicted_nonces = []
        for txid in to_evict:
            entry = storage.get_tx(txid)
            if entry:
                evicted_nonces.append(entry.nonce)
        
        assert 3 in evicted_nonces
        assert 4 in evicted_nonces
    
    def test_different_senders_independent(self, storage):
        """Per-sender limits are independent"""
        sender_a = b"a" * 32
        sender_b = b"b" * 32
        
        # Add 5 txs from sender A
        for nonce in range(5):
            envelope = make_test_envelope(nonce=nonce, sender=sender_a)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        # Add 2 txs from sender B
        for nonce in range(2):
            envelope = make_test_envelope(nonce=nonce, sender=sender_b)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=1234567890.0,
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        # Limit sender A to 3 (should evict 2)
        to_evict_a = per_sender_limit(storage, sender_a, max_per_sender=3)
        assert len(to_evict_a) == 2
        
        # Limit sender B to 3 (should evict 0)
        to_evict_b = per_sender_limit(storage, sender_b, max_per_sender=3)
        assert len(to_evict_b) == 0


class TestEvictExpired:
    """Tests for evict_expired()"""
    
    def test_no_expired(self, storage):
        """No eviction when nothing expired"""
        current_time = time.time()
        
        for i in range(3):
            envelope = make_test_envelope(nonce=i)
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=current_time - 10,  # 10 seconds ago
                fee_rate=1,
                source=TxSource.RPC,
            )
            storage.add_tx(entry)
        
        to_evict = evict_expired(storage, current_time, max_age_seconds=60)
        assert to_evict == []
    
    def test_evict_expired_txs(self, storage):
        """Evict transactions older than max age"""
        current_time = time.time()
        
        # Add old tx
        old_envelope = make_test_envelope(nonce=0)
        old_entry = MempoolEntry(
            envelope=old_envelope,
            arrival_time=current_time - 120,  # 2 minutes ago
            fee_rate=1,
            source=TxSource.RPC,
        )
        storage.add_tx(old_entry)
        
        # Add recent tx
        new_envelope = make_test_envelope(nonce=1)
        new_entry = MempoolEntry(
            envelope=new_envelope,
            arrival_time=current_time - 10,  # 10 seconds ago
            fee_rate=1,
            source=TxSource.RPC,
        )
        storage.add_tx(new_entry)
        
        # Evict txs older than 60 seconds
        to_evict = evict_expired(storage, current_time, max_age_seconds=60)
        assert len(to_evict) == 1
        assert to_evict[0] == old_envelope.txid
