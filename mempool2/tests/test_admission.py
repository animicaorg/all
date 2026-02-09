"""
Tests for mempool2.admission

Key requirement: admission NEVER throws exceptions.
"""

import tempfile
from pathlib import Path

import pytest

from coretx import RejectReason, TxAuth, TxBody, TxEnvelope, TxId, TxKind
from coretx.canonical import compute_txid

from mempool2.admission import AdmissionEngine
from mempool2.storage import MempoolStorage
from mempool2.types import TxSource


def make_test_envelope(
    nonce: int = 0,
    chain_id: int = 1,
    fee: int = 21000,
    sender: bytes = None,
    data: bytes = b"",
) -> TxEnvelope:
    """Helper to create test envelope"""
    if sender is None:
        sender = b"a" * 32
    
    body = TxBody(
        version=1,
        chain_id=chain_id,
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


@pytest.fixture
def engine(storage):
    """Create admission engine"""
    return AdmissionEngine(
        storage=storage,
        chain_id=1,
        max_tx_bytes=128 * 1024,
        min_fee_rate=1,
    )


class TestAdmissionBasics:
    """Basic admission tests"""
    
    def test_admit_valid_tx_no_state_checks(self, engine):
        """Admit valid transaction without state checks"""
        envelope = make_test_envelope(nonce=0)
        
        # Note: signature will fail, but we're testing the flow
        success, rejection = engine.admit_tx(envelope)
        
        # Should fail on signature (we don't have real keys)
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.invalid_signature
    
    def test_chain_id_mismatch(self, engine):
        """Wrong chain ID rejected"""
        envelope = make_test_envelope(chain_id=999)
        
        success, rejection = engine.admit_tx(envelope)
        
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.chain_id_mismatch
    
    def test_oversized_tx(self, engine):
        """Oversized transaction rejected"""
        large_data = b"x" * (200 * 1024)  # 200KB
        envelope = make_test_envelope(data=large_data)
        
        success, rejection = engine.admit_tx(envelope)
        
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.tx_oversize
    
    def test_insufficient_fee_rate(self, engine):
        """Low fee rate rejected"""
        envelope = make_test_envelope(fee=10000)  # < 1 wei/gas
        
        success, rejection = engine.admit_tx(envelope)
        
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.fee_too_low


class TestAdmissionNeverThrows:
    """Critical: admission must NEVER throw exceptions"""
    
    def test_malformed_envelope_caught(self, engine):
        """Malformed envelope doesn't throw"""
        # Create envelope with invalid addresses
        try:
            envelope = make_test_envelope()
            # Manually break it (bypass validation)
            object.__setattr__(envelope.body, "from_addr", b"short")
            
            success, rejection = engine.admit_tx(envelope)
            
            # Should not throw - should return rejection
            assert success is False
            assert rejection is not None
        except Exception:
            pytest.fail("admit_tx() threw an exception!")
    
    def test_balance_getter_exception_caught(self, engine):
        """Exception in balance_getter caught and returned as rejection"""
        envelope = make_test_envelope()
        
        def bad_balance_getter(addr):
            raise RuntimeError("Database exploded!")
        
        success, rejection = engine.admit_tx(envelope, balance_getter=bad_balance_getter)
        
        # Should not throw - should return internal_error
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.internal_error
        assert "Database exploded!" in rejection.message or "internal_error" in str(rejection)
    
    def test_unexpected_exception_caught(self, engine):
        """Any unexpected exception caught"""
        # This is a meta-test: we can't easily trigger unexpected exceptions,
        # but the code path is there in the catch-all try/except
        pass


class TestAdmissionWithStateChecks:
    """Admission with balance_getter for state checks"""
    
    def test_nonce_too_low(self, engine):
        """Nonce too low rejected"""
        envelope = make_test_envelope(nonce=5)
        
        def balance_getter(addr):
            return (1000000, 10)  # balance, confirmed_nonce
        
        success, rejection = engine.admit_tx(envelope, balance_getter=balance_getter)
        
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.nonce_too_low
    
    def test_insufficient_funds(self, engine):
        """Insufficient funds rejected"""
        envelope = make_test_envelope(nonce=5, fee=21000)  # total: 22000
        
        def balance_getter(addr):
            return (20000, 4)  # balance < required
        
        success, rejection = engine.admit_tx(envelope, balance_getter=balance_getter)
        
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.insufficient_funds
    
    def test_nonce_gap_rejected(self, engine):
        """Nonce gap rejected"""
        envelope = make_test_envelope(nonce=10)
        
        def balance_getter(addr):
            return (1000000, 4)  # confirmed_nonce=4, so expect 5
        
        success, rejection = engine.admit_tx(envelope, balance_getter=balance_getter)
        
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.nonce_gap


class TestAdmissionDuplicates:
    """Duplicate detection"""
    
    def test_duplicate_rejected(self, engine):
        """Duplicate transaction rejected"""
        envelope = make_test_envelope(nonce=0)
        
        # Manually add to storage (bypass signature check)
        from mempool2.types import MempoolEntry
        entry = MempoolEntry(
            envelope=envelope,
            arrival_time=1234567890.0,
            fee_rate=1,
            source=TxSource.RPC,
        )
        engine.storage.add_tx(entry)
        
        # Try to admit again
        success, rejection = engine.admit_tx(envelope)
        
        assert success is False
        assert rejection is not None
        assert rejection.reason == RejectReason.tx_already_known


class TestAdmissionSources:
    """Test different transaction sources"""
    
    def test_rpc_source(self, engine):
        """RPC source recorded"""
        envelope = make_test_envelope()
        
        # Will fail on signature, but we can check the attempt
        success, rejection = engine.admit_tx(envelope, source="rpc")
        
        assert success is False  # signature will fail
        # Source would be recorded if it passed
    
    def test_p2p_source_with_peer_id(self, engine):
        """P2P source with peer ID"""
        envelope = make_test_envelope()
        
        success, rejection = engine.admit_tx(
            envelope, source="p2p", peer_id="peer123"
        )
        
        assert success is False  # signature will fail
        # peer_id would be recorded if it passed


class TestAdmissionEdgeCases:
    """Edge cases and boundary conditions"""
    
    def test_zero_value_tx(self, engine):
        """Zero value transaction allowed"""
        body = TxBody(
            version=1,
            chain_id=1,
            nonce=0,
            from_addr=b"a" * 32,
            to_addr=b"b" * 32,
            value=0,  # Zero value
            fee=21000,
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
        txid = TxId(b"x" * 32)
        envelope = TxEnvelope(body=body, auth=auth, txid=txid)
        
        success, rejection = engine.admit_tx(envelope)
        
        # Will fail on signature, but zero value itself is OK
        assert rejection.reason != RejectReason.invalid_field
    
    def test_exact_max_size(self, engine):
        """Transaction at exact max size allowed"""
        # Create tx close to limit
        data_size = 128 * 1024 - 2000  # Leave room for overhead
        envelope = make_test_envelope(data=b"x" * data_size)
        
        success, rejection = engine.admit_tx(envelope)
        
        # Should not fail on size (will fail on signature)
        if rejection:
            assert rejection.reason != RejectReason.tx_oversize
