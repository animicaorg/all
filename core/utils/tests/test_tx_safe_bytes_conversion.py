"""
Test suite for safe bytes conversion in transaction normalization.

This test suite validates that the _safe_to_bytes() function properly handles
various input types without raising TypeError, addressing the issue where
unsafe bytes() conversions in normalize_tx_body() were causing mempool admission failures.
"""

import pytest
from core.utils.tx import _safe_to_bytes, normalize_tx_body


class TestSafeToBytesConversion:
    """Test _safe_to_bytes() handles various input types without TypeError."""

    def test_none_returns_empty_bytes(self):
        """Test that None returns empty bytes."""
        assert _safe_to_bytes(None) == b""

    def test_bytes_returns_bytes(self):
        """Test that bytes are returned as-is."""
        assert _safe_to_bytes(b"hello") == b"hello"
        assert _safe_to_bytes(b"") == b""

    def test_bytearray_returns_bytes(self):
        """Test that bytearray is converted to bytes."""
        assert _safe_to_bytes(bytearray(b"world")) == b"world"

    def test_hex_string_with_prefix(self):
        """Test that hex strings with 0x prefix are decoded."""
        assert _safe_to_bytes("0x48656c6c6f") == b"Hello"
        assert _safe_to_bytes("0X48656c6c6f") == b"Hello"

    def test_hex_string_without_prefix(self):
        """Test that hex strings without prefix are decoded."""
        assert _safe_to_bytes("48656c6c6f") == b"Hello"

    def test_empty_string(self):
        """Test that empty strings return empty bytes."""
        assert _safe_to_bytes("") == b""
        assert _safe_to_bytes("  ") == b""

    def test_utf8_string_fallback(self):
        """Test that non-hex strings are UTF-8 encoded."""
        # "hello" is not valid hex, so it should be UTF-8 encoded
        assert _safe_to_bytes("hello") == b"hello"

    def test_list_with_valid_integers(self):
        """Test that list of valid integers (0-255) is converted to bytes."""
        assert _safe_to_bytes([1, 2, 3]) == b"\x01\x02\x03"
        assert _safe_to_bytes([72, 101, 108, 108, 111]) == b"Hello"

    def test_list_with_invalid_elements_returns_empty(self):
        """Test that list with non-integer elements returns empty bytes (no TypeError)."""
        # This is the key test - previously this would raise TypeError
        assert _safe_to_bytes(["a", "b"]) == b""
        assert _safe_to_bytes([{"key": "value"}]) == b""
        assert _safe_to_bytes([None]) == b""

    def test_tuple_with_valid_integers(self):
        """Test that tuple of valid integers is converted to bytes."""
        assert _safe_to_bytes((1, 2, 3)) == b"\x01\x02\x03"

    def test_tuple_with_invalid_elements_returns_empty(self):
        """Test that tuple with non-integer elements returns empty bytes (no TypeError)."""
        assert _safe_to_bytes(("a", "b")) == b""

    def test_dict_returns_empty(self):
        """Test that dict returns empty bytes (no TypeError)."""
        # Previously: bytes({"key": "value"}) would raise TypeError
        assert _safe_to_bytes({"key": "value"}) == b""

    def test_int_returns_empty(self):
        """Test that int returns empty bytes (no TypeError)."""
        # Previously: bytes(123) would create 123 zero bytes, not what we want
        assert _safe_to_bytes(123) == b""

    def test_float_returns_empty(self):
        """Test that float returns empty bytes (no TypeError)."""
        assert _safe_to_bytes(12.34) == b""


class TestNormalizeTxBodyWithSafeBytes:
    """Test that normalize_tx_body() handles edge cases for data and salt fields."""

    def test_data_field_with_dict(self):
        """Test that data field with dict doesn't raise TypeError."""
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "nonce": 0,
            "value": 0,
            "data": {"invalid": "type"},  # This would previously cause TypeError
        }
        result = normalize_tx_body(body)
        assert isinstance(result, dict)
        assert result["payload"]["v"]["data"] == b""  # Should be empty bytes

    def test_data_field_with_list_of_strings(self):
        """Test that data field with list of strings doesn't raise TypeError."""
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "nonce": 0,
            "value": 0,
            "data": ["a", "b", "c"],  # Invalid list for bytes()
        }
        result = normalize_tx_body(body)
        assert result["payload"]["v"]["data"] == b""

    def test_data_field_with_valid_list(self):
        """Test that data field with valid list of integers works."""
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "nonce": 0,
            "value": 0,
            "data": [72, 101, 108, 108, 111],  # Valid bytes
        }
        result = normalize_tx_body(body)
        assert result["payload"]["v"]["data"] == b"Hello"

    def test_data_field_with_hex_string(self):
        """Test that data field with hex string works."""
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "nonce": 0,
            "value": 0,
            "data": "0x48656c6c6f",
        }
        result = normalize_tx_body(body)
        assert result["payload"]["v"]["data"] == b"Hello"

    def test_salt_field_with_dict(self):
        """Test that salt field with dict doesn't raise TypeError."""
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "v": 2,
            "validAfter": 100,
            "validUntil": 200,
            "salt": {"invalid": "type"},  # This would previously cause TypeError
        }
        result = normalize_tx_body(body)
        assert result["salt"] == b""

    def test_salt_field_with_list_of_strings(self):
        """Test that salt field with list of strings doesn't raise TypeError."""
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "v": 2,
            "validAfter": 100,
            "validUntil": 200,
            "salt": ["a", "b"],  # Invalid list for bytes()
        }
        result = normalize_tx_body(body)
        assert result["salt"] == b""

    def test_salt_field_with_valid_hex_string(self):
        """Test that salt field with valid hex string works."""
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "v": 2,
            "validAfter": 100,
            "validUntil": 200,
            "salt": "0x1234",
        }
        result = normalize_tx_body(body)
        assert result["salt"] == b"\x12\x34"

    def test_version_detection_with_invalid_salt(self):
        """Test that version detection still works when salt_raw is checked."""
        # When salt is provided but invalid, version should still be detected correctly
        body = {
            "from": "0x" + "00" * 32,
            "to": "0x" + "00" * 32,
            "v": 2,
            "validAfter": 100,
            "validUntil": 200,
            "salt": {"invalid": "dict"},
        }
        result = normalize_tx_body(body)
        # Version should be 2 since v=2 is explicitly set and all v2 fields are present
        assert result["v"] == 2
        assert "validAfter" in result
        assert "validUntil" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
