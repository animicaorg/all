"""
Unit tests for AICF (AI Compute Fund) verification.
"""

import pytest
from unittest.mock import Mock, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ena.animica.aicf_verify import (
    calculate_aicf_split,
    verify_payment_and_aicf,
    AICFVerificationError,
    _verify_single_tx,
    _verify_two_tx,
)
from ena.animica.verify import TransactionVerificationError


class TestCalculateAICFSplit:
    """Test AICF fee calculation."""
    
    def test_calculate_25_percent(self):
        """Test 25% AICF split."""
        total = 10000000  # 0.01 ANM
        service_fee, aicf_fee = calculate_aicf_split(total, 2500)
        
        assert aicf_fee == 2500000  # 25% rounded up
        assert service_fee == 7500000
        assert service_fee + aicf_fee == total
    
    def test_calculate_10_percent(self):
        """Test 10% AICF split."""
        total = 10000000
        service_fee, aicf_fee = calculate_aicf_split(total, 1000)
        
        assert aicf_fee == 1000000  # 10%
        assert service_fee == 9000000
        assert service_fee + aicf_fee == total
    
    def test_calculate_with_rounding(self):
        """Test AICF split with rounding."""
        total = 12345
        service_fee, aicf_fee = calculate_aicf_split(total, 2500)
        
        # 25% of 12345 = 3086.25, rounded up to 3087
        assert aicf_fee == 3087
        assert service_fee == 9258
        assert service_fee + aicf_fee == total
    
    def test_calculate_zero_bp(self):
        """Test 0% AICF (edge case)."""
        total = 10000000
        service_fee, aicf_fee = calculate_aicf_split(total, 0)
        
        assert aicf_fee == 0
        assert service_fee == total


class TestVerifyTwoTransactions:
    """Test two-transaction AICF verification."""
    
    def test_verify_two_tx_success(self):
        """Test successful two-transaction verification."""
        # Mock RPC client
        rpc_client = Mock()
        
        # Mock service transaction
        tx_service = {
            "from": "anim1payer123",
            "to": "anim1service456",
            "value": 7500000,  # Service fee
        }
        
        # Mock AICF transaction
        tx_aicf = {
            "from": "anim1payer123",
            "to": "anim1aicf789",
            "value": 2500000,  # AICF fee
        }
        
        # Setup RPC responses
        def get_transaction(tx_hash):
            if tx_hash == "0xservice":
                return tx_service
            elif tx_hash == "0xaicf":
                return tx_aicf
            return None
        
        rpc_client.get_transaction = Mock(side_effect=get_transaction)
        rpc_client.get_transaction_receipt = Mock(return_value=None)
        
        # Verify
        result = verify_payment_and_aicf(
            rpc_client=rpc_client,
            payer="anim1payer123",
            service_address="anim1service456",
            aicf_address="anim1aicf789",
            total_required=10000000,
            aicf_bp=2500,
            tx_hash_service="0xservice",
            tx_hash_aicf="0xaicf",
            require_confirmed=False,
        )
        
        assert result["paid"] is True
        assert result["payer"] == "anim1payer123"
        assert int(result["totalPaid"]) == 10000000
        assert int(result["servicePaid"]) == 7500000
        assert int(result["aicfPaid"]) == 2500000
        assert result["aicfExplicit"] is True
        assert result["txHashService"] == "0xservice"
        assert result["txHashAicf"] == "0xaicf"
    
    def test_verify_two_tx_insufficient_aicf(self):
        """Test rejection when AICF payment is insufficient."""
        rpc_client = Mock()
        
        tx_service = {
            "from": "anim1payer123",
            "to": "anim1service456",
            "value": 7500000,
        }
        
        # AICF payment is too low
        tx_aicf = {
            "from": "anim1payer123",
            "to": "anim1aicf789",
            "value": 1000000,  # Should be 2500000
        }
        
        def get_transaction(tx_hash):
            if tx_hash == "0xservice":
                return tx_service
            elif tx_hash == "0xaicf":
                return tx_aicf
            return None
        
        rpc_client.get_transaction = Mock(side_effect=get_transaction)
        
        with pytest.raises(AICFVerificationError) as exc_info:
            verify_payment_and_aicf(
                rpc_client=rpc_client,
                payer="anim1payer123",
                service_address="anim1service456",
                aicf_address="anim1aicf789",
                total_required=10000000,
                aicf_bp=2500,
                tx_hash_service="0xservice",
                tx_hash_aicf="0xaicf",
            )
        
        assert "AICF contribution missing/insufficient" in str(exc_info.value)
    
    def test_verify_two_tx_wrong_aicf_recipient(self):
        """Test rejection when AICF goes to wrong address."""
        rpc_client = Mock()
        
        tx_service = {
            "from": "anim1payer123",
            "to": "anim1service456",
            "value": 7500000,
        }
        
        tx_aicf = {
            "from": "anim1payer123",
            "to": "anim1wrong999",  # Wrong address!
            "value": 2500000,
        }
        
        def get_transaction(tx_hash):
            if tx_hash == "0xservice":
                return tx_service
            elif tx_hash == "0xaicf":
                return tx_aicf
            return None
        
        rpc_client.get_transaction = Mock(side_effect=get_transaction)
        
        with pytest.raises(AICFVerificationError) as exc_info:
            verify_payment_and_aicf(
                rpc_client=rpc_client,
                payer="anim1payer123",
                service_address="anim1service456",
                aicf_address="anim1aicf789",
                total_required=10000000,
                aicf_bp=2500,
                tx_hash_service="0xservice",
                tx_hash_aicf="0xaicf",
            )
        
        assert "Invalid AICF tx recipient" in str(exc_info.value)
    
    def test_verify_two_tx_different_payers(self):
        """Test rejection when transactions have different payers."""
        rpc_client = Mock()
        
        tx_service = {
            "from": "anim1payer123",
            "to": "anim1service456",
            "value": 7500000,
        }
        
        tx_aicf = {
            "from": "anim1other999",  # Different payer!
            "to": "anim1aicf789",
            "value": 2500000,
        }
        
        def get_transaction(tx_hash):
            if tx_hash == "0xservice":
                return tx_service
            elif tx_hash == "0xaicf":
                return tx_aicf
            return None
        
        rpc_client.get_transaction = Mock(side_effect=get_transaction)
        
        with pytest.raises(TransactionVerificationError) as exc_info:
            verify_payment_and_aicf(
                rpc_client=rpc_client,
                payer="anim1payer123",
                service_address="anim1service456",
                aicf_address="anim1aicf789",
                total_required=10000000,
                aicf_bp=2500,
                tx_hash_service="0xservice",
                tx_hash_aicf="0xaicf",
            )
        
        assert "Invalid AICF tx sender" in str(exc_info.value)
    
    def test_verify_two_tx_not_found(self):
        """Test rejection when transaction not found."""
        rpc_client = Mock()
        rpc_client.get_transaction = Mock(return_value=None)
        
        with pytest.raises(TransactionVerificationError) as exc_info:
            verify_payment_and_aicf(
                rpc_client=rpc_client,
                payer="anim1payer123",
                service_address="anim1service456",
                aicf_address="anim1aicf789",
                total_required=10000000,
                aicf_bp=2500,
                tx_hash_service="0xservice",
                tx_hash_aicf="0xaicf",
            )
        
        assert "transaction not found" in str(exc_info.value).lower()


class TestVerifySingleTransaction:
    """Test single-transaction AICF verification."""
    
    def test_verify_single_tx_fallback(self):
        """Test single transaction paying full amount to service (fallback)."""
        rpc_client = Mock()
        
        tx = {
            "from": "anim1payer123",
            "to": "anim1service456",
            "value": 10000000,  # Full amount to service
        }
        
        rpc_client.get_transaction = Mock(return_value=tx)
        
        # This should work but log a warning
        result = verify_payment_and_aicf(
            rpc_client=rpc_client,
            payer="anim1payer123",
            service_address="anim1service456",
            aicf_address="anim1aicf789",
            total_required=10000000,
            aicf_bp=2500,
            tx_hash="0xsingle",
        )
        
        assert result["paid"] is True
        assert result["aicfExplicit"] is False  # Not explicitly paid
        assert int(result["totalPaid"]) == 10000000


class TestValueExtraction:
    """Test transaction value extraction and normalization."""
    
    def test_hex_value(self):
        """Test extraction of hex value."""
        from ena.animica.aicf_verify import _extract_value
        
        tx = {"value": "0x989680"}  # 10000000 in hex
        value = _extract_value(tx)
        assert value == 10000000
    
    def test_int_value(self):
        """Test extraction of integer value."""
        from ena.animica.aicf_verify import _extract_value
        
        tx = {"value": 10000000}
        value = _extract_value(tx)
        assert value == 10000000
    
    def test_string_value(self):
        """Test extraction of string value."""
        from ena.animica.aicf_verify import _extract_value
        
        tx = {"value": "10000000"}
        value = _extract_value(tx)
        assert value == 10000000
    
    def test_invalid_value(self):
        """Test rejection of invalid value."""
        from ena.animica.aicf_verify import _extract_value
        
        tx = {"value": -100}
        with pytest.raises(TransactionVerificationError):
            _extract_value(tx)


class TestMissingTransactionHashes:
    """Test validation of transaction hash requirements."""
    
    def test_no_hashes_provided(self):
        """Test rejection when no transaction hashes provided."""
        rpc_client = Mock()
        
        with pytest.raises(AICFVerificationError) as exc_info:
            verify_payment_and_aicf(
                rpc_client=rpc_client,
                payer="anim1payer123",
                service_address="anim1service456",
                aicf_address="anim1aicf789",
                total_required=10000000,
                aicf_bp=2500,
            )
        
        assert "Must provide either tx_hash or both" in str(exc_info.value)
    
    def test_only_service_hash(self):
        """Test rejection when only service hash provided."""
        rpc_client = Mock()
        
        with pytest.raises(AICFVerificationError):
            verify_payment_and_aicf(
                rpc_client=rpc_client,
                payer="anim1payer123",
                service_address="anim1service456",
                aicf_address="anim1aicf789",
                total_required=10000000,
                aicf_bp=2500,
                tx_hash_service="0xservice",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
