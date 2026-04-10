import pytest

from randomness.commit_reveal.params import WindowParams
# These imports reflect the intended API surface of the randomness module.
# If names shift slightly during implementation, update the imports/uses here.
from randomness.commit_reveal.round_manager import RoundManager


def mk_mgr(
    commit_secs: int = 10,
    reveal_secs: int = 7,
    grace_secs: int = 3,
    round0_start: int = 1_000,
) -> tuple[RoundManager, WindowParams, int]:
    params = WindowParams(
        commit_secs=commit_secs,
        reveal_secs=reveal_secs,
        reveal_grace_secs=grace_secs,
        round0_start_ts=round0_start,
    )
    return RoundManager(params), params, round0_start


def window_numbers(params: WindowParams, round_id: int) -> dict[str, int]:
    """
    Compute expected window boundaries for a given round deterministically,
    mirroring the spec: each round has a contiguous commit window followed
    by a reveal window; grace applies after reveal end.
    """
    stride = params.commit_secs + params.reveal_secs
    round_start = params.round0_start_ts + round_id * stride
    commit_start = round_start
    commit_end = commit_start + params.commit_secs  # end-exclusive
    reveal_start = commit_end
    reveal_end = reveal_start + params.reveal_secs  # end-exclusive
    grace_end = reveal_end + params.reveal_grace_secs  # end-exclusive
    return dict(
        commit_start=commit_start,
        commit_end=commit_end,
        reveal_start=reveal_start,
        reveal_end=reveal_end,
        grace_end=grace_end,
    )


def test_window_boundaries_match_spec():
    mgr, params, _ = mk_mgr(
        commit_secs=10, reveal_secs=7, grace_secs=3, round0_start=1_000
    )

    # Check a few rounds
    for rid in (0, 1, 5):
        exp = window_numbers(params, rid)
        win = mgr.windows(rid)
        assert win.commit_start == exp["commit_start"]
        assert win.commit_end == exp["commit_end"]
        assert win.reveal_start == exp["reveal_start"]
        assert win.reveal_end == exp["reveal_end"]
        assert win.grace_end == exp["grace_end"]
        # Non-overlap & contiguity
        assert (
            win.commit_start
            < win.commit_end
            <= win.reveal_start
            < win.reveal_end
            <= win.grace_end
        )


@pytest.mark.parametrize(
    "offset,expected",
    [
        (0, True),  # exactly at commit_start → allowed
        (5, True),  # strictly inside commit window
        (9, True),  # last second inside commit window (commit_secs=10)
        (10, False),  # exactly at commit_end → NOT allowed (end-exclusive)
        (-1, False),  # one second before window opens → NOT allowed
    ],
)
def test_can_commit_boundaries(offset: int, expected: bool):
    mgr, params, _ = mk_mgr(
        commit_secs=10, reveal_secs=7, grace_secs=0, round0_start=2_000
    )
    rid = 0
    win = window_numbers(params, rid)
    ts = win["commit_start"] + offset
    assert mgr.can_commit(rid, ts) is expected

    # Ensure reveal is NOT allowed during commit window (including the start boundary)
    if ts < win["commit_end"]:
        assert mgr.can_reveal(rid, ts, include_grace=False) is False


@pytest.mark.parametrize(
    "offset,expected",
    [
        (0, True),  # exactly at reveal_start → allowed
        (3, True),  # inside reveal window
        (6, True),  # last second inside reveal window (reveal_secs=7)
        (7, False),  # exactly at reveal_end → NOT allowed unless grace
        (-1, False),  # one second before reveal opens → NOT allowed
    ],
)
def test_can_reveal_without_grace(offset: int, expected: bool):
    mgr, params, _ = mk_mgr(
        commit_secs=10, reveal_secs=7, grace_secs=0, round0_start=3_000
    )
    rid = 1
    win = window_numbers(params, rid)
    ts = win["reveal_start"] + offset
    assert mgr.can_reveal(rid, ts, include_grace=False) is expected


@pytest.mark.parametrize(
    "offset,expected",
    [
        (0, True),  # reveal_start
        (6, True),  # just before reveal_end
        (7, True),  # exactly at reveal_end → allowed due to grace
        (9, True),  # within grace
        (10, False),  # exactly at grace end → NOT allowed (end-exclusive)
    ],
)
def test_can_reveal_with_grace(offset: int, expected: bool):
    mgr, params, _ = mk_mgr(
        commit_secs=10, reveal_secs=7, grace_secs=3, round0_start=4_000
    )
    rid = 2
    win = window_numbers(params, rid)
    ts = win["reveal_start"] + offset
    assert mgr.can_reveal(rid, ts, include_grace=True) is expected


def test_cross_round_edges_commit_of_next_round_not_allowed_until_prev_reveal_done():
    # Contiguous windows, no gap. Commit(n+1) must not start until reveal(n) closes.
    mgr, params, _ = mk_mgr(
        commit_secs=8, reveal_secs=5, grace_secs=0, round0_start=10_000
    )
    r0 = 0
    r1 = 1
    w0 = window_numbers(params, r0)
    w1 = window_numbers(params, r1)

    # At the last valid moment of r0.reveal, next-round commit should NOT yet be open.
    ts_last_reveal_r0 = w0["reveal_end"] - 1
    assert mgr.can_commit(r1, ts_last_reveal_r0) is False

    # At r0.reveal_end boundary, r1.commit_start == ts; start is inclusive.
    assert w1["commit_start"] == w0["reveal_end"]
    assert mgr.can_commit(r1, w1["commit_start"]) is True


def test_reveal_for_wrong_round_rejected_even_if_in_some_window():
    mgr, params, _ = mk_mgr(
        commit_secs=6, reveal_secs=6, grace_secs=2, round0_start=20_000
    )
    r_target = 3
    r_wrong = 2
    w_target = window_numbers(params, r_target)

    # Choose a timestamp valid for revealing r_target
    ts = w_target["reveal_start"] + 1
    assert mgr.can_reveal(r_target, ts, include_grace=True) is True

    # But the same timestamp should be invalid if you try to reveal for a different round
    assert mgr.can_reveal(r_wrong, ts, include_grace=True) is False


def test_validation_helpers_raise_meaningful_errors_when_available():
    """
    If the implementation exposes strict validators (ensure_can_commit / ensure_can_reveal)
    that raise domain errors, check a couple of representative cases. If not available,
    silently skip.
    """
    mgr, params, _ = mk_mgr(
        commit_secs=10, reveal_secs=7, grace_secs=0, round0_start=30_000
    )
    rid = 0
    wins = window_numbers(params, rid)

    # Too early commit
    ts = wins["commit_start"] - 1
    ensure_commit = getattr(mgr, "ensure_can_commit", None)
    if ensure_commit is None:
        pytest.skip("ensure_can_commit not exposed")
    else:
        with pytest.raises(
            Exception
        ):  # CommitTooEarly not standardized; any domain error is fine
            ensure_commit(rid, ts)

    # Too late commit
    ts_late = wins["commit_end"]
    with pytest.raises(Exception):
        ensure_commit(rid, ts_late)

    # Reveal too early
    ensure_reveal = getattr(mgr, "ensure_can_reveal", None)
    if ensure_reveal is None:
        pytest.skip("ensure_can_reveal not exposed")
    else:
        with pytest.raises(Exception):  # RevealTooEarly or equivalent
            ensure_reveal(rid, wins["reveal_start"] - 1, include_grace=False)


def test_current_round_computation_monotonic():
    mgr, params, start = mk_mgr(
        commit_secs=4, reveal_secs=4, grace_secs=0, round0_start=50_000
    )
    # Walk through several seconds and ensure current_round is non-decreasing.
    now = start - 1
    r_prev = mgr.current_round(now)
    for step in range(0, 80):
        now = start + step
        r_now = mgr.current_round(now)
        assert r_now >= r_prev
        r_prev = r_now


def test_can_methods_are_pure_boolean_and_side_effect_free():
    mgr, params, _ = mk_mgr(
        commit_secs=5, reveal_secs=5, grace_secs=2, round0_start=60_000
    )
    rid = 4
    w = window_numbers(params, rid)

    # Snapshot a few probes and ensure repeated calls give consistent answers.
    probes = [
        w["commit_start"] - 1,
        w["commit_start"],
        w["commit_end"] - 1,
        w["commit_end"],
        w["reveal_start"],
        w["reveal_end"] - 1,
        w["reveal_end"],
        w["grace_end"] - 1,
        w["grace_end"],
    ]
    for ts in probes:
        c1, r1 = mgr.can_commit(rid, ts), mgr.can_reveal(rid, ts, include_grace=True)
        c2, r2 = mgr.can_commit(rid, ts), mgr.can_reveal(rid, ts, include_grace=True)
        assert (c1, r1) == (c2, r2), f"inconsistent results at ts={ts}"
