from __future__ import annotations

import pytest

from core.utils.time import (maybe_normalize_unix_timestamp_seconds,
                             normalize_unix_timestamp_seconds)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1_700_000_123, 1_700_000_123),  # seconds
        (1_700_000_123_000, 1_700_000_123),  # milliseconds
        (1_700_000_123_000_000, 1_700_000_123),  # microseconds
        (1_700_000_123_000_000_000, 1_700_000_123),  # nanoseconds
        ("1700000123", 1_700_000_123),  # decimal string
        ("0x6553f17b", 1_700_000_123),  # hex string
        (b"\x65\x53\xf1\x7b", 1_700_000_123),  # bytes (big-endian)
    ],
)
def test_normalize_unix_timestamp_seconds_handles_common_units(raw, expected):
    assert normalize_unix_timestamp_seconds(raw) == expected


def test_maybe_normalize_unix_timestamp_seconds_returns_none_for_bad_input():
    assert maybe_normalize_unix_timestamp_seconds(None) is None
    assert maybe_normalize_unix_timestamp_seconds("") is None
    assert maybe_normalize_unix_timestamp_seconds("not-a-number") is None
