"""
Test for mempool.accounting value conversion

Ensures that estimate_max_spend can handle different value formats
that might come from CLI/RPC submissions (strings, hex strings, ints).
"""

import pytest
from mempool.accounting import estimate_max_spend, _safe_int_from_value


class MockTx:
    """Mock transaction object for testing."""
    
    def __init__(self, value=0, gas_limit=21000, gas_price=1000000000):
        self.value = value
        self.gas_limit = gas_limit
        self.gas_price = gas_price
        self.data = b""
        self.kind = "transfer"


def test_safe_int_from_value_handles_int():
    """Test that _safe_int_from_value handles integer values."""
    assert _safe_int_from_value(0) == 0
    assert _safe_int_from_value(100) == 100
    assert _safe_int_from_value(1000000) == 1000000


def test_safe_int_from_value_handles_none():
    """Test that _safe_int_from_value handles None."""
    assert _safe_int_from_value(None) == 0


def test_safe_int_from_value_handles_decimal_string():
    """Test that _safe_int_from_value handles decimal string values."""
    assert _safe_int_from_value("0") == 0
    assert _safe_int_from_value("10") == 10
    assert _safe_int_from_value("1000000") == 1000000
    assert _safe_int_from_value("  100  ") == 100  # with whitespace


def test_safe_int_from_value_handles_hex_string():
    """Test that _safe_int_from_value handles hex string values (the bug fix)."""
    assert _safe_int_from_value("0x0") == 0
    assert _safe_int_from_value("0xa") == 10
    assert _safe_int_from_value("0x10") == 16
    assert _safe_int_from_value("0xff") == 255
    assert _safe_int_from_value("0xFF") == 255
    assert _safe_int_from_value("0x123abc") == 1194684


def test_safe_int_from_value_handles_empty_string():
    """Test that _safe_int_from_value handles empty strings."""
    assert _safe_int_from_value("") == 0
    assert _safe_int_from_value("  ") == 0


def test_estimate_max_spend_with_int_value():
    """Test estimate_max_spend with integer value (original behavior)."""
    tx = MockTx(value=1000000, gas_limit=21000, gas_price=1000000000)
    estimate = estimate_max_spend(tx)
    
    assert estimate.value == 1000000
    assert estimate.gas_limit == 21000
    assert estimate.effective_gas_price == 1000000000
    assert estimate.total_max_spend == 1000000 + (21000 * 1000000000)


def test_estimate_max_spend_with_string_value():
    """Test estimate_max_spend with decimal string value (CLI format)."""
    tx = MockTx(value="1000000", gas_limit=21000, gas_price=1000000000)
    estimate = estimate_max_spend(tx)
    
    assert estimate.value == 1000000
    assert estimate.total_max_spend == 1000000 + (21000 * 1000000000)


def test_estimate_max_spend_with_hex_string_value():
    """Test estimate_max_spend with hex string value (the bug case)."""
    tx = MockTx(value="0xa", gas_limit=21000, gas_price=1000000000)
    estimate = estimate_max_spend(tx)
    
    assert estimate.value == 10
    assert estimate.total_max_spend == 10 + (21000 * 1000000000)


def test_estimate_max_spend_with_large_hex_value():
    """Test estimate_max_spend with a larger hex string value."""
    # 0x123abc = 1194684
    tx = MockTx(value="0x123abc", gas_limit=21000, gas_price=1000000000)
    estimate = estimate_max_spend(tx)
    
    assert estimate.value == 1194684
    assert estimate.total_max_spend == 1194684 + (21000 * 1000000000)


def test_estimate_max_spend_with_none_value():
    """Test estimate_max_spend with None value."""
    tx = MockTx(value=None, gas_limit=21000, gas_price=1000000000)
    estimate = estimate_max_spend(tx)
    
    assert estimate.value == 0
    assert estimate.total_max_spend == (21000 * 1000000000)


def test_estimate_max_spend_with_amount_field():
    """Test estimate_max_spend falls back to 'amount' field if 'value' not present."""
    class TxWithAmount:
        def __init__(self):
            self.amount = "100"
            self.gas_limit = 21000
            self.gas_price = 1000000000
            self.data = b""
            self.kind = "transfer"
    
    tx = TxWithAmount()
    estimate = estimate_max_spend(tx)
    
    assert estimate.value == 100
    assert estimate.total_max_spend == 100 + (21000 * 1000000000)


def test_estimate_max_spend_with_hex_gas_limit():
    """Test estimate_max_spend with hex string gas_limit."""
    tx = MockTx(value=100, gas_limit="0x5208", gas_price=1000000000)  # 0x5208 = 21000
    estimate = estimate_max_spend(tx)
    
    assert estimate.gas_limit == 21000
    assert estimate.total_max_spend == 100 + (21000 * 1000000000)


def test_estimate_max_spend_with_string_gas_price():
    """Test estimate_max_spend with string gas_price."""
    tx = MockTx(value=100, gas_limit=21000, gas_price="1000000000")
    estimate = estimate_max_spend(tx)
    
    assert estimate.effective_gas_price == 1000000000
    assert estimate.total_max_spend == 100 + (21000 * 1000000000)


def test_estimate_max_spend_with_hex_gas_price():
    """Test estimate_max_spend with hex string gas_price."""
    tx = MockTx(value=100, gas_limit=21000, gas_price="0x3b9aca00")  # 0x3b9aca00 = 1000000000
    estimate = estimate_max_spend(tx)
    
    assert estimate.effective_gas_price == 1000000000
    assert estimate.total_max_spend == 100 + (21000 * 1000000000)

