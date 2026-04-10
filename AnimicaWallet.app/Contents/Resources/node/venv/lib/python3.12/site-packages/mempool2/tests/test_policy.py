"""
Tests for mempool2.policy
"""

import pytest

from coretx import RejectReason, TxAuth, TxBody, TxEnvelope, TxId, TxKind

from mempool2 import policy


def make_envelope(**overrides):
    """Helper to create test envelope with defaults"""
    defaults = {
        "version": 1,
        "chain_id": 1,
        "nonce": 0,
        "from_addr": b"a" * 32,
        "to_addr": b"b" * 32,
        "value": 1000,
        "fee": 21000,
        "gas_limit": 21000,
        "data": b"",
        "memo": "",
        "timestamp": 1234567890,
        "kind": TxKind.TRANSFER,
    }
    defaults.update(overrides)
    
    body = TxBody(**defaults)
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pk" * 32,
        signature_bytes=b"sig" * 64,
        prehash_id=2,
    )
    txid = TxId(b"x" * 32)
    
    return TxEnvelope(body=body, auth=auth, txid=txid)


class TestCheckFormat:
    """Tests for check_format()"""
    
    def test_valid_envelope(self):
        """Valid envelope passes format check"""
        envelope = make_envelope()
        result = policy.check_format(envelope)
        assert result is None
    
    def test_invalid_from_addr_length(self):
        """Invalid from_addr length rejected"""
        # Create valid envelope first, then break it
        envelope = make_envelope()
        # Bypass frozen dataclass to break the address
        object.__setattr__(envelope.body, "from_addr", b"short")
        result = policy.check_format(envelope)
        assert result is not None
        assert result.reason == RejectReason.invalid_field
        assert "from_addr" in result.message
    
    def test_invalid_to_addr_length(self):
        """Invalid to_addr length rejected"""
        # Create valid envelope first, then break it
        envelope = make_envelope()
        object.__setattr__(envelope.body, "to_addr", b"x" * 16)
        result = policy.check_format(envelope)
        assert result is not None
        assert result.reason == RejectReason.invalid_field
        assert "to_addr" in result.message
    
    def test_memo_too_long(self):
        """Memo exceeding 256 bytes rejected"""
        # Create valid envelope first, then break it
        envelope = make_envelope()
        long_memo = "x" * 300
        object.__setattr__(envelope.body, "memo", long_memo)
        result = policy.check_format(envelope)
        assert result is not None
        assert result.reason == RejectReason.invalid_field
        assert "memo" in result.message.lower()


class TestCheckChainId:
    """Tests for check_chain_id()"""
    
    def test_matching_chain_id(self):
        """Matching chain ID accepted"""
        envelope = make_envelope(chain_id=1)
        result = policy.check_chain_id(envelope, expected_chain_id=1)
        assert result is None
    
    def test_mismatched_chain_id(self):
        """Mismatched chain ID rejected"""
        envelope = make_envelope(chain_id=999)
        result = policy.check_chain_id(envelope, expected_chain_id=1)
        assert result is not None
        assert result.reason == RejectReason.chain_id_mismatch
        assert "999" in result.message
        assert result.context["actual"] == 999
        assert result.context["expected"] == 1


class TestCheckSize:
    """Tests for check_size()"""
    
    def test_small_tx_accepted(self):
        """Small transaction accepted"""
        envelope = make_envelope(data=b"x" * 100)
        result = policy.check_size(envelope, max_bytes=10000)
        assert result is None
    
    def test_oversized_tx_rejected(self):
        """Oversized transaction rejected"""
        envelope = make_envelope(data=b"x" * 10000)
        result = policy.check_size(envelope, max_bytes=1000)
        assert result is not None
        assert result.reason == RejectReason.tx_oversize
        assert "too large" in result.message.lower()


class TestCheckFee:
    """Tests for check_fee()"""
    
    def test_sufficient_fee(self):
        """Sufficient fee rate accepted"""
        envelope = make_envelope(fee=21000, gas_limit=21000)  # 1 wei/gas
        result = policy.check_fee(envelope, min_fee_rate=1)
        assert result is None
    
    def test_high_fee_accepted(self):
        """High fee rate accepted"""
        envelope = make_envelope(fee=42000, gas_limit=21000)  # 2 wei/gas
        result = policy.check_fee(envelope, min_fee_rate=1)
        assert result is None
    
    def test_insufficient_fee(self):
        """Insufficient fee rate rejected"""
        envelope = make_envelope(fee=10000, gas_limit=21000)  # 0.47 wei/gas
        result = policy.check_fee(envelope, min_fee_rate=1)
        assert result is not None
        assert result.reason == RejectReason.fee_too_low
        assert "too low" in result.message.lower()
    
    def test_zero_gas_limit(self):
        """Zero gas limit rejected"""
        # Create valid envelope first, then break it
        envelope = make_envelope()
        object.__setattr__(envelope.body, "fee", 1000)
        object.__setattr__(envelope.body, "gas_limit", 0)
        result = policy.check_fee(envelope, min_fee_rate=1)
        assert result is not None
        assert result.reason == RejectReason.invalid_field


class TestCheckNonce:
    """Tests for check_nonce()"""
    
    def test_next_nonce_accepted(self):
        """Next sequential nonce accepted"""
        envelope = make_envelope(nonce=5)
        result = policy.check_nonce(envelope, confirmed_nonce=4, pending_nonces=set())
        assert result is None
    
    def test_nonce_too_low(self):
        """Nonce <= confirmed rejected"""
        envelope = make_envelope(nonce=4)
        result = policy.check_nonce(envelope, confirmed_nonce=4, pending_nonces=set())
        assert result is not None
        assert result.reason == RejectReason.nonce_too_low
    
    def test_nonce_conflict(self):
        """Duplicate nonce in mempool rejected"""
        envelope = make_envelope(nonce=5)
        result = policy.check_nonce(envelope, confirmed_nonce=4, pending_nonces={5})
        assert result is not None
        assert result.reason == RejectReason.nonce_conflict
    
    def test_nonce_gap_rejected(self):
        """Nonce with gap rejected"""
        envelope = make_envelope(nonce=7)
        result = policy.check_nonce(envelope, confirmed_nonce=4, pending_nonces=set())
        assert result is not None
        assert result.reason == RejectReason.nonce_gap
        assert "missing" in result.message.lower()
    
    def test_nonce_gap_filled_accepted(self):
        """Nonce gap filled by pending accepted"""
        envelope = make_envelope(nonce=7)
        result = policy.check_nonce(
            envelope, confirmed_nonce=4, pending_nonces={5, 6}
        )
        assert result is None


class TestCheckFunds:
    """Tests for check_funds()"""
    
    def test_sufficient_funds(self):
        """Sufficient funds accepted"""
        envelope = make_envelope(value=1000, fee=21000)  # total 22000
        result = policy.check_funds(
            envelope, available_balance=100000, pending_debits=0
        )
        assert result is None
    
    def test_insufficient_balance(self):
        """Insufficient balance rejected"""
        envelope = make_envelope(value=1000, fee=21000)  # total 22000
        result = policy.check_funds(
            envelope, available_balance=20000, pending_debits=0
        )
        assert result is not None
        assert result.reason == RejectReason.insufficient_funds
    
    def test_pending_debits_considered(self):
        """Pending debits reduce available balance"""
        envelope = make_envelope(value=1000, fee=21000)  # total 22000
        result = policy.check_funds(
            envelope, available_balance=100000, pending_debits=80000
        )
        # Usable = 100000 - 80000 = 20000, need 22000
        assert result is not None
        assert result.reason == RejectReason.insufficient_funds
    
    def test_exact_balance(self):
        """Exact balance accepted"""
        envelope = make_envelope(value=1000, fee=21000)  # total 22000
        result = policy.check_funds(
            envelope, available_balance=22000, pending_debits=0
        )
        assert result is None
