"""Unit tests for aicf.work.decimals."""

from __future__ import annotations

import pytest

from aicf.work.decimals import add_anm, anm_to_atomic, atomic_to_anm, split_reward


def test_round_trip_integer_amounts():
    for v in ["1", "100", "1000000"]:
        assert atomic_to_anm(anm_to_atomic(v)) == v


def test_round_trip_fractional_amounts():
    assert atomic_to_anm(anm_to_atomic("1.5")) == "1.5"
    assert atomic_to_anm(anm_to_atomic("0.000000001")) == "0.000000001"
    assert atomic_to_anm(anm_to_atomic("123.456789012")) == "123.456789012"


def test_rejects_bad_inputs():
    with pytest.raises(ValueError):
        anm_to_atomic("abc")
    with pytest.raises(ValueError):
        anm_to_atomic("-1")
    with pytest.raises(ValueError):
        anm_to_atomic("1.2.3")


def test_two_share_split_sums_back():
    shares = split_reward("1.0", [0.7, 0.3])
    assert shares == ["0.7", "0.3"]
    assert add_anm(shares[0], shares[1]) == "1"


def test_five_share_split_sums_back():
    shares = split_reward("1.0", [0.15, 0.3, 0.25, 0.15, 0.15])
    total = "0"
    for s in shares:
        total = add_anm(total, s)
    assert total == "1"


def test_dust_from_rounding_lands_on_first_share():
    # 1 nano-ANM split three ways: only the first gets it.
    shares = split_reward("0.000000001", [1, 1, 1])
    total = "0"
    for s in shares:
        total = add_anm(total, s)
    assert total == "0.000000001"
    assert shares == ["0.000000001", "0", "0"]


def test_zero_weight_fallback_is_equal_split():
    shares = split_reward("2", [0, 0, 0])
    total = "0"
    for s in shares:
        total = add_anm(total, s)
    assert total == "2"
