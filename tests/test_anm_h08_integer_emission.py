"""ANM-H08/M04: block emission is exact-integer (no float determinism hazard) and
value-preserving vs the legacy float computation for the shipped 50% decay."""
from consensus.rewards import _integer_subsidy


def test_integer_subsidy_matches_legacy_float_for_50pct():
    for start in (300_000_000_000, 5_000_000_000):
        for epoch in range(0, 70):
            legacy_float = int(start * (((100.0 - 50.0) / 100.0) ** epoch))
            assert _integer_subsidy(start, 50.0, epoch) == legacy_float, (start, epoch)


def test_integer_subsidy_is_true_halving():
    start = 300_000_000_000
    assert _integer_subsidy(start, 50.0, 0) == start
    assert _integer_subsidy(start, 50.0, 1) == start // 2
    assert _integer_subsidy(start, 50.0, 2) == start // 4
    assert _integer_subsidy(start, 50.0, 10) == start >> 10


def test_result_is_pure_int():
    v = _integer_subsidy(300_000_000_000, 50.0, 3)
    assert type(v) is int
