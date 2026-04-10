import pytest

from animica.coin import (
    COIN_DECIMALS,
    COIN_UNIT,
    format_amount,
    from_base_units,
    to_base_units,
)


def test_to_base_units_uses_nine_decimals():
    assert COIN_DECIMALS == 9
    assert COIN_UNIT == 10**COIN_DECIMALS
    assert to_base_units("1") == COIN_UNIT
    assert to_base_units("0.000000001") == 1


def test_format_amount_roundtrip():
    raw = 123_456_789
    human = format_amount(raw)
    assert (
        human
        == "0.123456789 ANM (123,456,789 base units; 1 ANM = 1,000,000,000 base units)"
    )
    assert to_base_units("0.123456789") == raw
    assert float(from_base_units(raw)) == pytest.approx(0.123456789)


def test_roundtrip_decimal_conversions():
    cases = ["0", "1", "0.5", "1234.000000001"]
    for value in cases:
        base = to_base_units(value)
        assert float(from_base_units(base)) == pytest.approx(float(value))
