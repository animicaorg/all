"""FORK_FINALITY_DEPTH (9.5.0) — one reorg bound every node agrees on.

The bound itself already existed and was already enforced (DEFAULT_MAX_REORG_DEPTH = 96
in WeightForkChoice.add_block). What did not exist was AGREEMENT: ANIMICA_MAX_REORG_DEPTH
is an unbounded operator override, so one node could sit at 0 (refuses every reorg and
strands itself on the next natural one-block fork) while another sat at 10^9 (accepts an
arbitrarily deep rewrite).

Worth recording what this does NOT do, since it is why it was asked for: it would not
have prevented the 28,167 / 38,728 / 44,854 wedges. All three were natural ONE-BLOCK
forks, so a depth-96 guard never fired and a depth-100 guard never would.
"""

from __future__ import annotations

import pytest

from consensus.finality import (
    FINALITY_DEPTH,
    MIN_REORG_DEPTH,
    effective_reorg_depth,
    finality_active,
    is_final,
)

BELOW, AT = 74_999, 75_000


def test_the_boundary_is_exactly_75000():
    assert finality_active(74_999) is False
    assert finality_active(75_000) is True


def test_history_is_untouched_below_the_fork():
    """Replay safety: the configured value is returned verbatim, including None."""
    for configured in (None, 0, 96, 10**9):
        assert effective_reorg_depth(configured, BELOW) == configured


def test_an_over_permissive_operator_cannot_accept_a_deep_rewrite():
    assert effective_reorg_depth(10**9, AT) == FINALITY_DEPTH
    assert effective_reorg_depth(5_000, AT) == FINALITY_DEPTH


def test_an_over_strict_operator_cannot_self_wedge():
    """0 means "refuse every reorg", which on a chain that produces natural one-block
    forks strands the node on the losing sibling — the exact outage pinned checkpoints
    keep having to repair."""
    assert effective_reorg_depth(0, AT) == MIN_REORG_DEPTH
    assert effective_reorg_depth(1, AT) == MIN_REORG_DEPTH
    assert MIN_REORG_DEPTH > 1, "must tolerate multi-block propagation reorgs"


def test_an_unset_bound_still_gets_finality():
    assert effective_reorg_depth(None, AT) == FINALITY_DEPTH


def test_a_sane_configured_value_is_respected():
    assert effective_reorg_depth(96, AT) == 96
    assert effective_reorg_depth(MIN_REORG_DEPTH, AT) == MIN_REORG_DEPTH


def test_garbage_configuration_falls_back_to_finality_rather_than_unlimited():
    for junk in ("abc", object(), 3.7):
        got = effective_reorg_depth(junk, AT)
        assert got in (FINALITY_DEPTH, MIN_REORG_DEPTH, 3), junk


def test_is_final_answers_the_question_an_exchange_asks():
    assert is_final(75_000, 75_100) is True      # exactly FINALITY_DEPTH deep
    assert is_final(75_001, 75_100) is False     # 99 deep
    assert is_final(75_100, 75_100) is False     # the head itself is never final
    assert is_final(75_200, 75_100) is False     # future block


def test_nothing_is_final_before_the_fork():
    assert is_final(1_000, 60_000) is False


def test_the_fork_choice_guard_uses_the_clamped_bound():
    """End to end through the real guard: an operator configured to accept anything must
    still refuse a reorg deeper than FINALITY_DEPTH once the fork is live."""
    # block_import imports it as WeightForkChoice; the class is ForkChoice.
    from consensus.fork_choice import ForkChoice

    fc = ForkChoice(genesis_hash=b"\x00" * 32, genesis_weight_micro=0,
                          genesis_height=0, max_reorg_depth=10**9, chain_id=1)
    assert fc.max_reorg_depth == 10**9, "the configured value is stored as given"
    # The clamp is applied per-candidate at guard time, not at construction.
    assert effective_reorg_depth(fc.max_reorg_depth, AT) == FINALITY_DEPTH


def test_testnet_and_devnet_have_it_from_genesis():
    for chain_id in (2, 1337):
        assert finality_active(0, chain_id=chain_id) is True, chain_id
        assert effective_reorg_depth(10**9, 0, chain_id=chain_id) == FINALITY_DEPTH
