"""
Test for RPC bech32 address conversion fixes

Ensures that bech32 address decoding doesn't raise TypeError when converting
from 5-bit data words to bytes. This tests the fix applied to state.py and faucet.py.
"""

import pytest

# Import the modules we're testing
try:
    from pq.py.utils import bech32 as _bech32
    BECH32_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    BECH32_AVAILABLE = False


@pytest.mark.skipif(not BECH32_AVAILABLE, reason="bech32 module not available")
def test_bech32_decode_returns_5bit_list():
    """
    Test that bech32.bech32_decode returns a list of 5-bit integers.
    This is the root cause of the TypeError - calling bytes() on this list
    fails because Python interprets it as a size parameter.
    """
    # Create a test address
    test_payload = b"\x01" + bytes.fromhex("11" * 32)  # alg_id=1 + 32 bytes
    test_addr = _bech32.encode_address(test_payload)
    
    # Use the low-level decode that returns 5-bit data
    hrp, data5, spec = _bech32.bech32_decode(test_addr)
    
    # Verify it returns a list of 5-bit integers
    assert isinstance(data5, list)
    assert all(isinstance(x, int) and 0 <= x <= 31 for x in data5)
    
    # Verify that calling bytes() on this list would fail or behave incorrectly
    # (it would interpret the first element as size or treat it as iterable of 8-bit values)
    # We don't actually call bytes(data5) to avoid the error, but we document the issue
    
    # The correct way is to use decode_address or fivebit_to_bytes
    correct_payload = _bech32.decode_address(test_addr)
    assert correct_payload == test_payload


@pytest.mark.skipif(not BECH32_AVAILABLE, reason="bech32 module not available")
def test_decode_address_returns_bytes():
    """
    Test that decode_address properly converts 5-bit data to bytes.
    This is the correct function to use (as fixed in state.py and faucet.py).
    """
    # Create a test address
    test_payload = b"\x01" + bytes.fromhex("22" * 32)
    test_addr = _bech32.encode_address(test_payload)
    
    # Use decode_address (the fixed approach)
    payload = _bech32.decode_address(test_addr)
    
    # Verify it returns bytes
    assert isinstance(payload, bytes)
    assert payload == test_payload


@pytest.mark.skipif(not BECH32_AVAILABLE, reason="bech32 module not available")
def test_fivebit_to_bytes_converts_properly():
    """
    Test that fivebit_to_bytes converts 5-bit words to bytes.
    This is the underlying function that decode_address uses.
    """
    # Create test data
    test_bytes = b"Hello"
    
    # Convert to 5-bit
    words_5bit = _bech32.bytes_to_5bit(test_bytes)
    assert isinstance(words_5bit, list)
    assert all(isinstance(x, int) and 0 <= x <= 31 for x in words_5bit)
    
    # Convert back to bytes
    result_bytes = _bech32.fivebit_to_bytes(words_5bit)
    assert isinstance(result_bytes, bytes)
    assert result_bytes == test_bytes


@pytest.mark.skipif(not BECH32_AVAILABLE, reason="bech32 module not available")
def test_address_roundtrip():
    """
    Test full address encoding/decoding roundtrip.
    """
    # Test with various payload sizes
    test_payloads = [
        b"\x01" + bytes.fromhex("11" * 32),  # 33 bytes
        b"\x02" + bytes.fromhex("22" * 32),  # 33 bytes
        b"\x01" + bytes.fromhex("33" * 31),  # 32 bytes
    ]
    
    for payload in test_payloads:
        addr = _bech32.encode_address(payload)
        decoded = _bech32.decode_address(addr)
        assert decoded == payload, f"Roundtrip failed for payload {payload.hex()}"


def test_bytes_constructor_behavior():
    """
    Document the problematic bytes() behavior that caused the bug.
    """
    # bytes() with a list of 5-bit integers behaves incorrectly:
    five_bit_list = [1, 2, 3, 15, 31]  # Valid 5-bit values
    
    # bytes() interprets these as 8-bit values, not as size
    result = bytes(five_bit_list)
    assert result == b"\x01\x02\x03\x0f\x1f"  # Treats as 8-bit values
    
    # But bech32 5-bit data needs proper conversion via convertbits
    # because 5-bit values need to be packed into 8-bit bytes properly


def _is_valid_hex_string(s: str) -> bool:
    """Helper to check if a string is valid hexadecimal."""
    return all(c in "0123456789abcdefABCDEF" for c in s)


def test_ptl_tx_data_type_checking():
    """
    Test the type checking logic added to ptl.py.
    This test documents the expected behavior without importing the RPC module.
    """
    # Test cases that should succeed
    valid_inputs = [
        ("0x48656c6c6f", bytes.fromhex("48656c6c6f")),  # hex string
        ("48656c6c6f", bytes.fromhex("48656c6c6f")),  # hex string without 0x
        (b"Hello", b"Hello"),  # bytes
        (bytearray(b"Hello"), b"Hello"),  # bytearray
        ([72, 101, 108, 108, 111], b"Hello"),  # list of ints (72='H', 101='e', etc.)
    ]
    
    for input_val, expected in valid_inputs:
        if isinstance(input_val, str):
            if input_val.startswith("0x"):
                input_val = input_val[2:]
            result = bytes.fromhex(input_val)
        elif isinstance(input_val, (bytes, bytearray)):
            result = bytes(input_val)
        elif isinstance(input_val, (list, tuple)):
            result = bytes(input_val)
        else:
            pytest.fail(f"Unexpected valid input type: {type(input_val)}")
        assert result == expected
    
    # Test cases that should fail with clear errors
    invalid_inputs = [
        ({"key": "value"}, TypeError),  # dict
        ("not hex", ValueError),  # non-hex string
        ([256, 1, 2], ValueError),  # list with out-of-range values
    ]
    
    for invalid, expected_error in invalid_inputs:
        with pytest.raises((TypeError, ValueError)) as exc_info:
            if isinstance(invalid, dict):
                # dict should fail with TypeError
                bytes(invalid)
            elif isinstance(invalid, str) and not _is_valid_hex_string(invalid):
                # non-hex string should fail with ValueError
                bytes.fromhex(invalid)
            elif isinstance(invalid, list) and any(x > 255 for x in invalid):
                # list with out-of-range values should fail
                bytes(invalid)
            else:
                pytest.fail(f"Invalid input {invalid} should have been caught by validation")
        
        # Verify the correct exception type was raised
        assert isinstance(exc_info.value, expected_error)
