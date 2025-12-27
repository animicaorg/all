from __future__ import annotations

from core.utils.pow import compact_bits_to_target


def test_compact_bits_decodes_bitcoin_genesis() -> None:
    # Bitcoin genesis difficulty bits 0x1d00ffff
    bits = 0x1D00FFFF
    target = compact_bits_to_target(bits)
    expected = 0x00FFFF * (1 << (8 * (0x1D - 3)))
    assert target == expected


def test_compact_bits_handles_small_exponent() -> None:
    bits = 0x02008000  # exponent=0x02, mantissa=0x008000
    target = compact_bits_to_target(bits)
    expected = 0x8000 >> 8
    assert target == expected


def test_compact_bits_rejects_negative_or_zero() -> None:
    assert compact_bits_to_target(0) == 0
    assert compact_bits_to_target(0x1D800000) == 0  # sign bit set
