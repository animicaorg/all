"""Unit tests: hex parsing, base-unit -> ANM string formatting (plain decimal,
never scientific notation), and the consensus-mirroring subsidy math."""
from __future__ import annotations

import pytest

import sources

COIN = 10**9


# --- parse_hex_amount --------------------------------------------------------

def test_parse_hex_amount_hex():
    assert sources.parse_hex_amount("0x17647d9a02d8abd") == 105350641310599869
    assert sources.parse_hex_amount("0x0") == 0
    assert sources.parse_hex_amount("0X9347338FD46D") == 161934017025133


def test_parse_hex_amount_int_and_decimal_str():
    assert sources.parse_hex_amount(42) == 42
    assert sources.parse_hex_amount("12345") == 12345


def test_parse_hex_amount_garbage_raises():
    with pytest.raises(sources.SourceError):
        sources.parse_hex_amount("banana")
    with pytest.raises(sources.SourceError):
        sources.parse_hex_amount(None)


# --- base_units_to_anm_str (1e9 conversion + plain-text formatting) ----------

def test_format_live_total_supply():
    assert sources.base_units_to_anm_str(105350641310599869) == "105350641.310599869"


def test_format_whole_values_no_decimal_point():
    assert sources.base_units_to_anm_str(900_000_000 * COIN) == "900000000"
    assert sources.base_units_to_anm_str(0) == "0"


def test_format_small_and_fractional():
    assert sources.base_units_to_anm_str(1) == "0.000000001"
    assert sources.base_units_to_anm_str(1_500_000_000) == "1.5"
    assert sources.base_units_to_anm_str(-1_500_000_000) == "-1.5"


def test_format_huge_values_never_scientific():
    huge = 10**27  # 1e18 ANM, far past any real value
    s = sources.base_units_to_anm_str(huge)
    assert s == "1" + "0" * 18
    assert "e" not in s.lower()
    assert "," not in s

    ugly = 987654321987654321987654321
    s2 = sources.base_units_to_anm_str(ugly)
    assert s2 == "987654321987654321.987654321"
    assert "e" not in s2.lower()


def test_format_roundtrips_exactly():
    for bu in (1, 999_999_999, COIN, COIN + 1, 105350641310599869, 10**26 + 7):
        s = sources.base_units_to_anm_str(bu)
        whole, _, frac = s.partition(".")
        back = int(whole) * COIN + (int(frac.ljust(9, "0")) if frac else 0)
        assert back == bu, s


# --- subsidy schedule (mirrors consensus/rewards.py) -------------------------

def test_subsidy_epoch0_split_after_foundation_fork():
    total, miner, foundation = sources.subsidy_split_base_units(63080)
    assert total == 300 * COIN
    assert miner == 255 * COIN
    assert foundation == 45 * COIN
    assert miner + foundation == total


def test_subsidy_before_foundation_fork_all_miner():
    total, miner, foundation = sources.subsidy_split_base_units(42000)
    assert (total, miner, foundation) == (300 * COIN, 300 * COIN, 0)


def test_subsidy_first_halving():
    assert sources.subsidy_total_base_units(1_350_000) == 300 * COIN   # last block of epoch 0
    assert sources.subsidy_total_base_units(1_350_001) == 150 * COIN   # first block of epoch 1


def test_subsidy_tail_floor():
    # epoch 22: floor(300e9 / 2^22) = 71,525 nANM < 100,000 tail -> tail applies
    h = 22 * 1_350_000 + 1
    assert sources.subsidy_total_base_units(h) == 100_000


def test_circulating_is_total_minus_noncirculating_sum():
    total = 105350641310599869
    balances = [161934017025133, 0, 0, 0, 0]
    assert total - sum(balances) == 105188707293574736
    assert sources.base_units_to_anm_str(total - sum(balances)) == "105188707.293574736"


# --- non_circulating_list: ANIMICA_NON_CIRCULATING is ADDITIVE ---------------

def test_non_circulating_default_without_env(monkeypatch):
    monkeypatch.delenv("ANIMICA_NON_CIRCULATING", raising=False)
    assert sources.non_circulating_list() == sources.DEFAULT_NON_CIRCULATING


def test_non_circulating_env_extends_defaults(monkeypatch):
    extra1 = "anim1extraoperationalwalletxxxxxxxxxxxxxxx"
    extra2 = "anim1anotherextrawalletxxxxxxxxxxxxxxxxxxx"
    monkeypatch.setenv("ANIMICA_NON_CIRCULATING", f" {extra1} ,, {extra2}")
    lst = sources.non_circulating_list()
    addrs = [a for a, _ in lst]
    # union: every default is still present — the env can only ADD
    for addr, _ in sources.DEFAULT_NON_CIRCULATING:
        assert addr in addrs
    assert extra1 in addrs and extra2 in addrs
    assert len(addrs) == len(sources.DEFAULT_NON_CIRCULATING) + 2
    assert len(set(addrs)) == len(addrs)  # no duplicates


def test_non_circulating_foundation_impossible_to_drop(monkeypatch):
    # even an env value that only re-lists a system address cannot evict the
    # foundation treasury (the old replace semantics would have)
    monkeypatch.setenv("ANIMICA_NON_CIRCULATING", "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    addrs = [a for a, _ in sources.non_circulating_list()]
    assert sources.FOUNDATION_TREASURY_ADDRESS in addrs
    assert addrs.count("anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx") == 1
    assert len(addrs) == len(sources.DEFAULT_NON_CIRCULATING)
