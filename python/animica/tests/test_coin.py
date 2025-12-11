import pytest

from animica.coin import COIN_DECIMALS, COIN_UNIT, format_amount, to_base_units


def test_to_base_units_uses_nine_decimals():
    assert COIN_DECIMALS == 9
    assert COIN_UNIT == 10**COIN_DECIMALS
    assert to_base_units("1") == COIN_UNIT
    assert to_base_units("0.000000001") == 1


def test_format_amount_roundtrip():
    raw = 123_456_789
    human = format_amount(raw)
    assert human == "0.123456789 ANM (123456789 units)"
    assert to_base_units("0.123456789") == raw
