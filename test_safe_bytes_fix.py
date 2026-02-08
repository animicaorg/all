"""
Test for the _safe_bytes_from_value fix to prevent TypeError in mempool admission.
"""
import sys
import os
# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

from mempool.accounting import _safe_bytes_from_value, intrinsic_gas
from dataclasses import dataclass
from typing import Any


def test_safe_bytes_from_value():
    """Test that _safe_bytes_from_value handles various input types safely."""
    
    # Test None
    assert _safe_bytes_from_value(None) == b""
    
    # Test bytes
    assert _safe_bytes_from_value(b"hello") == b"hello"
    
    # Test bytearray
    assert _safe_bytes_from_value(bytearray(b"world")) == b"world"
    
    # Test hex strings with 0x prefix
    assert _safe_bytes_from_value("0x48656c6c6f") == b"Hello"
    assert _safe_bytes_from_value("0X48656c6c6f") == b"Hello"
    
    # Test hex strings without prefix
    assert _safe_bytes_from_value("48656c6c6f") == b"Hello"
    
    # Test empty string
    assert _safe_bytes_from_value("") == b""
    assert _safe_bytes_from_value("   ") == b""
    
    # Test dict (should return empty bytes instead of raising TypeError)
    assert _safe_bytes_from_value({"key": "value"}) == b""
    
    # Test list (should return empty bytes instead of raising TypeError)
    assert _safe_bytes_from_value([1, 2, 3]) == b""
    
    # Test int (should return empty bytes instead of raising TypeError)
    assert _safe_bytes_from_value(123) == b""
    
    # Test invalid hex string (should return empty bytes or UTF-8 fallback)
    result = _safe_bytes_from_value("not hex")
    # It should either be UTF-8 encoded or empty
    assert isinstance(result, bytes)
    
    print("✅ All _safe_bytes_from_value tests passed!")


@dataclass
class MockTx:
    """Mock transaction object for testing."""
    data: Any = b""
    gas_limit: int = 100000  # Higher limit to accommodate data gas
    kind: str = ""


def test_intrinsic_gas_with_invalid_data():
    """Test that intrinsic_gas doesn't raise TypeError with invalid data types."""
    
    # Test with dict as data (this used to cause TypeError)
    tx = MockTx(data={"invalid": "data"})
    try:
        gas = intrinsic_gas(tx)
        assert gas >= 21000  # At least base gas
        print(f"✅ intrinsic_gas with dict data: {gas} gas")
    except TypeError as e:
        print(f"❌ TypeError with dict data: {e}")
        raise
    
    # Test with list as data
    tx = MockTx(data=[1, 2, 3])
    try:
        gas = intrinsic_gas(tx)
        assert gas >= 21000
        print(f"✅ intrinsic_gas with list data: {gas} gas")
    except TypeError as e:
        print(f"❌ TypeError with list data: {e}")
        raise
    
    # Test with int as data
    tx = MockTx(data=123)
    try:
        gas = intrinsic_gas(tx)
        assert gas >= 21000
        print(f"✅ intrinsic_gas with int data: {gas} gas")
    except TypeError as e:
        print(f"❌ TypeError with int data: {e}")
        raise
    
    # Test with None as data
    tx = MockTx(data=None)
    try:
        gas = intrinsic_gas(tx)
        assert gas == 21000  # Base gas with empty data
        print(f"✅ intrinsic_gas with None data: {gas} gas")
    except TypeError as e:
        print(f"❌ TypeError with None data: {e}")
        raise
    
    # Test with bytes as data (normal case)
    tx = MockTx(data=b"hello world")
    try:
        gas = intrinsic_gas(tx)
        assert gas > 21000  # Base gas plus data gas
        print(f"✅ intrinsic_gas with bytes data: {gas} gas")
    except TypeError as e:
        print(f"❌ TypeError with bytes data: {e}")
        raise
    
    # Test with hex string as data
    tx = MockTx(data="0x48656c6c6f")
    try:
        gas = intrinsic_gas(tx)
        assert gas > 21000  # Base gas plus data gas
        print(f"✅ intrinsic_gas with hex string data: {gas} gas")
    except TypeError as e:
        print(f"❌ TypeError with hex string data: {e}")
        raise
    
    print("✅ All intrinsic_gas tests passed!")


if __name__ == "__main__":
    print("Testing _safe_bytes_from_value...")
    test_safe_bytes_from_value()
    print()
    print("Testing intrinsic_gas with various data types...")
    test_intrinsic_gas_with_invalid_data()
    print()
    print("🎉 All tests passed! The fix prevents TypeError in mempool admission.")
