from __future__ import annotations

from typing import Iterable, Tuple

import consensus.fork_choice as fc


def _add_chain(
    fork: fc.ForkChoice, parent: str | bytes, blocks: Iterable[Tuple[str, int]]
):
    """Attach a sequence of (hash, weight) blocks under `parent`."""
    current_parent = parent
    current_height = fork.nodes[fc._hex_to_bytes(parent)].height  # type: ignore[attr-defined]
    for h, weight in blocks:
        current_height += 1
        fork.add_block(h=h, parent=current_parent, height=current_height, weight_micro=weight)
        current_parent = h


def test_reorgs_to_heaviest_fork_when_shorter_seen_first():
    fork = fc.ForkChoice(genesis_hash="0x00", genesis_weight_micro=0, genesis_height=0)

    # Fork A: arrive first, 3 blocks of modest weight
    _add_chain(
        fork,
        fork.best_tip.h,
        [("0xa1", 100), ("0xa2", 100), ("0xa3", 100)],
    )
    first_tip = fork.best_tip
    assert first_tip.height == 3
    assert first_tip.cum_weight_micro == 300

    # Fork B: heavier cumulative work (4 blocks) arriving after A
    _add_chain(
        fork,
        "0x00",
        [("0xb1", 150), ("0xb2", 200), ("0xb3", 200), ("0xb4", 200)],
    )
    best = fork.best_tip
    assert best.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xb4"))
    assert best.cum_weight_micro == 750
    # Should reorg from A to B even though A arrived first
    assert best.height == 4


def test_prefers_heavier_work_even_if_shorter_chain():
    fork = fc.ForkChoice(genesis_hash="0x99", genesis_weight_micro=0, genesis_height=0)

    # Long but lighter fork (5 blocks)
    _add_chain(
        fork,
        fork.best_tip.h,
        [
            ("0xc1", 80),
            ("0xc2", 80),
            ("0xc3", 80),
            ("0xc4", 80),
            ("0xc5", 80),
        ],
    )
    long_tip = fork.best_tip
    assert long_tip.height == 5
    assert long_tip.cum_weight_micro == 400

    # Shorter fork but significantly heavier per-block weight
    _add_chain(
        fork,
        "0x99",
        [("0xd1", 300), ("0xd2", 300), ("0xd3", 300)],
    )
    best = fork.best_tip
    assert best.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xd3"))
    assert best.cum_weight_micro == 900
    # Height is shorter than the initial chain, but work is higher
    assert best.height == 3
