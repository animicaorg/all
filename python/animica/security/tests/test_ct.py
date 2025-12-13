"""
Tests for constant-time comparison helpers.
"""

import pytest

from animica.security.ct import (
    ct_all_checks,
    ct_any_check,
    ct_eq_bytes,
    ct_eq_str,
    ct_memcmp,
    ct_select,
)


class TestCtEqBytes:
    """Tests for ct_eq_bytes() function."""

    def test_equal_bytes(self):
        assert ct_eq_bytes(b"secret", b"secret") is True

    def test_unequal_bytes(self):
        assert ct_eq_bytes(b"secret", b"guess") is False

    def test_different_lengths(self):
        assert ct_eq_bytes(b"short", b"longer string") is False

    def test_empty_bytes(self):
        assert ct_eq_bytes(b"", b"") is True
        assert ct_eq_bytes(b"", b"nonempty") is False

    def test_none_inputs(self):
        assert ct_eq_bytes(None, b"secret") is False
        assert ct_eq_bytes(b"secret", None) is False
        assert ct_eq_bytes(None, None) is False

    def test_special_bytes(self):
        # Test with binary data (not just ASCII)
        data1 = bytes(range(256))
        data2 = bytes(range(256))
        data3 = bytes(range(255, -1, -1))
        assert ct_eq_bytes(data1, data2) is True
        assert ct_eq_bytes(data1, data3) is False


class TestCtEqStr:
    """Tests for ct_eq_str() function."""

    def test_equal_strings(self):
        assert ct_eq_str("password", "password") is True

    def test_unequal_strings(self):
        assert ct_eq_str("password", "guess") is False

    def test_empty_strings(self):
        assert ct_eq_str("", "") is True
        assert ct_eq_str("", "nonempty") is False

    def test_none_inputs(self):
        assert ct_eq_str(None, "password") is False
        assert ct_eq_str("password", None) is False
        assert ct_eq_str(None, None) is False

    def test_unicode_strings(self):
        # Test with Unicode characters
        assert ct_eq_str("hello 世界", "hello 世界") is True
        assert ct_eq_str("hello 世界", "hello world") is False

    def test_case_sensitivity(self):
        # Should be case-sensitive
        assert ct_eq_str("Password", "password") is False


class TestCtSelect:
    """Tests for ct_select() function."""

    def test_true_mask(self):
        assert ct_select(True, 42, 0) == 42

    def test_false_mask(self):
        assert ct_select(False, 42, 0) == 0

    def test_various_values(self):
        assert ct_select(True, 100, 200) == 100
        assert ct_select(False, 100, 200) == 200

    def test_zero_values(self):
        assert ct_select(True, 0, 1) == 0
        assert ct_select(False, 0, 1) == 1

    def test_negative_values(self):
        assert ct_select(True, -1, -2) == -1
        assert ct_select(False, -1, -2) == -2


class TestCtMemcmp:
    """Tests for ct_memcmp() function."""

    def test_equal_memoryviews(self):
        buf1 = bytearray(b"secret")
        buf2 = bytearray(b"secret")
        assert ct_memcmp(memoryview(buf1), memoryview(buf2)) is True

    def test_unequal_memoryviews(self):
        buf1 = bytearray(b"secret")
        buf2 = bytearray(b"guess")
        assert ct_memcmp(memoryview(buf1), memoryview(buf2)) is False

    def test_different_lengths(self):
        buf1 = bytearray(b"short")
        buf2 = bytearray(b"longer string")
        assert ct_memcmp(memoryview(buf1), memoryview(buf2)) is False

    def test_empty_memoryviews(self):
        buf1 = bytearray(b"")
        buf2 = bytearray(b"")
        assert ct_memcmp(memoryview(buf1), memoryview(buf2)) is True

    def test_sliced_memoryviews(self):
        buf = bytearray(b"0123456789")
        mv1 = memoryview(buf)[2:5]
        mv2 = memoryview(buf)[2:5]
        mv3 = memoryview(buf)[3:6]
        assert ct_memcmp(mv1, mv2) is True
        assert ct_memcmp(mv1, mv3) is False


class TestCtAllChecks:
    """Tests for ct_all_checks() function."""

    def test_all_true(self):
        assert ct_all_checks(True, True, True) is True

    def test_one_false(self):
        assert ct_all_checks(True, False, True) is False

    def test_all_false(self):
        assert ct_all_checks(False, False, False) is False

    def test_single_check(self):
        assert ct_all_checks(True) is True
        assert ct_all_checks(False) is False

    def test_no_checks(self):
        assert ct_all_checks() is True  # Vacuous truth

    def test_many_checks(self):
        checks = [True] * 10
        assert ct_all_checks(*checks) is True
        checks[5] = False
        assert ct_all_checks(*checks) is False


class TestCtAnyCheck:
    """Tests for ct_any_check() function."""

    def test_all_true(self):
        assert ct_any_check(True, True, True) is True

    def test_one_true(self):
        assert ct_any_check(False, True, False) is True

    def test_all_false(self):
        assert ct_any_check(False, False, False) is False

    def test_single_check(self):
        assert ct_any_check(True) is True
        assert ct_any_check(False) is False

    def test_no_checks(self):
        assert ct_any_check() is False  # No checks means none are true

    def test_many_checks(self):
        checks = [False] * 10
        assert ct_any_check(*checks) is False
        checks[5] = True
        assert ct_any_check(*checks) is True


class TestIntegrationScenarios:
    """Integration tests for realistic usage scenarios."""

    def test_password_verification(self):
        """Simulate password verification."""
        stored_hash = b"hashed_password_123"
        input_hash_correct = b"hashed_password_123"
        input_hash_wrong = b"hashed_password_456"

        assert ct_eq_bytes(stored_hash, input_hash_correct) is True
        assert ct_eq_bytes(stored_hash, input_hash_wrong) is False

    def test_hmac_verification(self):
        """Simulate HMAC tag verification."""
        computed_hmac = b"\x01\x02\x03\x04"
        provided_hmac_correct = b"\x01\x02\x03\x04"
        provided_hmac_wrong = b"\x01\x02\x03\x05"

        assert ct_eq_bytes(computed_hmac, provided_hmac_correct) is True
        assert ct_eq_bytes(computed_hmac, provided_hmac_wrong) is False

    def test_token_verification(self):
        """Simulate API token verification."""
        valid_token = "sk_live_1234567890abcdef"
        provided_token_correct = "sk_live_1234567890abcdef"
        provided_token_wrong = "sk_live_1234567890abcdeg"

        assert ct_eq_str(valid_token, provided_token_correct) is True
        assert ct_eq_str(valid_token, provided_token_wrong) is False

    def test_multi_field_verification(self):
        """Simulate verification with multiple fields."""
        # All fields must match
        checks = [
            ct_eq_bytes(b"field1", b"field1"),
            ct_eq_bytes(b"field2", b"field2"),
            ct_eq_bytes(b"field3", b"field3"),
        ]
        assert ct_all_checks(*checks) is True

        # One field mismatch
        checks = [
            ct_eq_bytes(b"field1", b"field1"),
            ct_eq_bytes(b"field2", b"WRONG"),
            ct_eq_bytes(b"field3", b"field3"),
        ]
        assert ct_all_checks(*checks) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
