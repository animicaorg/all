from animica.cli import tx


def test_parse_value_to_base_units_from_anm() -> None:
    value_base, source = tx._parse_value_to_base_units("17", None)
    assert value_base == 17_000_000_000
    assert source == "anm"


def test_parse_value_to_base_units_from_nanm() -> None:
    value_base, source = tx._parse_value_to_base_units(None, 42)
    assert value_base == 42
    assert source == "nanm"


def test_parse_value_rejects_excess_decimals() -> None:
    try:
        tx._parse_value_to_base_units("0.0000000001", None)
    except ValueError as exc:
        assert "more than 9 decimal" in str(exc)
    else:
        raise AssertionError("Expected ValueError for excessive decimal places")
