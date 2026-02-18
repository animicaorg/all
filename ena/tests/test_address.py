"""Tests for ENA animica integration."""

import pytest
from ena.animica.address import (
    validate_address,
    validate_tx_hash,
    normalize_address,
    normalize_tx_hash,
)


class TestAddressValidation:
    """Test address validation functions."""
    
    def test_validate_valid_address(self):
        """Test valid address validation."""
        # Valid format: anim1 + 39 chars
        valid_addr = "anim1" + "q" * 39
        assert validate_address(valid_addr) is True
    
    def test_validate_invalid_prefix(self):
        """Test invalid prefix rejection."""
        invalid = "anim2" + "q" * 39
        assert validate_address(invalid) is False
    
    def test_validate_invalid_length(self):
        """Test invalid length rejection."""
        too_short = "anim1" + "q" * 10
        assert validate_address(too_short) is False
        
        too_long = "anim1" + "q" * 50
        assert validate_address(too_long) is False
    
    def test_validate_empty_address(self):
        """Test empty address rejection."""
        assert validate_address("") is False
        assert validate_address(None) is False
    
    def test_normalize_address(self):
        """Test address normalization."""
        addr = "ANIM1" + "Q" * 39
        normalized = normalize_address(addr)
        assert normalized == addr.lower()


class TestTxHashValidation:
    """Test transaction hash validation."""
    
    def test_validate_valid_tx_hash(self):
        """Test valid tx hash validation."""
        valid_hash = "0x" + "a" * 64
        assert validate_tx_hash(valid_hash) is True
    
    def test_validate_invalid_prefix(self):
        """Test invalid prefix rejection."""
        invalid = "a" * 64
        assert validate_tx_hash(invalid) is False
    
    def test_validate_invalid_length(self):
        """Test invalid length rejection."""
        too_short = "0x" + "a" * 10
        assert validate_tx_hash(too_short) is False
        
        too_long = "0x" + "a" * 100
        assert validate_tx_hash(too_long) is False
    
    def test_validate_invalid_chars(self):
        """Test invalid character rejection."""
        invalid = "0x" + "g" * 64
        assert validate_tx_hash(invalid) is False
    
    def test_normalize_tx_hash(self):
        """Test tx hash normalization."""
        hash_val = "0x" + "A" * 64
        normalized = normalize_tx_hash(hash_val)
        assert normalized == hash_val.lower()
