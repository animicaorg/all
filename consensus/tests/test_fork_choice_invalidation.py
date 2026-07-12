"""ForkChoice.mark_invalid: a state-rejected block (+descendants) must be
excluded from best-tip selection, so it is not re-selected forever (head stall).

Used by the 7.1.9 FORK_STATE_COMMITMENT enforcement path: when a block commits a
stateRoot that does not match the recomputed post-execution root, block_import
marks it invalid so fork choice falls back to the heaviest VALID tip.
"""
from __future__ import annotations

import consensus.fork_choice as fc


def _add(fork, h, parent, height, w):
    return fork.add_block(h=h, parent=parent, height=height, weight_micro=w)


def test_marking_tip_invalid_falls_back_to_parent():
    fork = fc.ForkChoice(genesis_hash="0x00", genesis_weight_micro=0, genesis_height=0)
    _add(fork, "0xa1", "0x00", 1, 100)
    _add(fork, "0xa2", "0xa1", 2, 100)
    _add(fork, "0xa3", "0xa2", 3, 100)
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xa3"))

    fork.mark_invalid("0xa3")
    # a3 excluded; best falls back to a2 (heaviest valid tip).
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xa2"))
    assert fork.is_invalid("0xa3")


def test_marking_block_invalidates_its_descendants():
    fork = fc.ForkChoice(genesis_hash="0x00")
    _add(fork, "0xa1", "0x00", 1, 100)
    _add(fork, "0xa2", "0xa1", 2, 100)
    _add(fork, "0xa3", "0xa2", 3, 100)

    fork.mark_invalid("0xa1")  # invalidates a1, a2, a3
    for h in ("0xa1", "0xa2", "0xa3"):
        assert fork.is_invalid(h)
    # Only genesis remains valid.
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0x00"))
    assert fork.best_tip.height == 0


def test_reorg_to_valid_fork_when_heavier_tip_is_invalid():
    fork = fc.ForkChoice(genesis_hash="0x00")
    # Light valid fork A.
    _add(fork, "0xa1", "0x00", 1, 100)
    _add(fork, "0xa2", "0xa1", 2, 100)  # cum 200
    # Heavier fork B becomes best.
    _add(fork, "0xb1", "0x00", 1, 500)  # cum 500
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xb1"))

    # B proven invalid → best must reorg to the heaviest VALID tip (a2).
    fork.mark_invalid("0xb1")
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xa2"))
    assert fork.best_tip.cum_weight_micro == 200


def test_premark_excludes_block_that_arrives_later():
    fork = fc.ForkChoice(genesis_hash="0x00")
    _add(fork, "0xa1", "0x00", 1, 100)  # current best

    fork.mark_invalid("0xb1")  # pre-mark before b1 exists
    r = _add(fork, "0xb1", "0x00", 1, 999)  # heavier, but pre-marked invalid
    assert not r.became_best
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xa1"))
    # A child of the pre-marked block is also excluded.
    _add(fork, "0xb2", "0xb1", 2, 999)
    assert fork.is_invalid("0xb2")
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xa1"))


def test_genesis_cannot_be_invalidated():
    fork = fc.ForkChoice(genesis_hash="0x00")
    _add(fork, "0xa1", "0x00", 1, 100)
    fork.mark_invalid("0x00")  # no-op
    assert not fork.is_invalid("0x00")
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xa1"))


def test_empty_invalid_set_is_zero_behavior_change():
    # Sanity: without any mark_invalid, add/reorg behaves exactly as before.
    fork = fc.ForkChoice(genesis_hash="0x00")
    _add(fork, "0xa1", "0x00", 1, 100)
    _add(fork, "0xb1", "0x00", 1, 200)
    assert fork.best_tip.hex == fc._bytes_to_hex(fc._hex_to_bytes("0xb1"))
    assert fork.invalid == set()


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
