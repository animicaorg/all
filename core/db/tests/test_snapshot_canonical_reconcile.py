"""Regression tests for canonical_height reconciliation on snapshot import.

The mainnet bug: canonical_height (META_CANONICAL_HEIGHT) is a running counter of
non-instant canonical blocks (drives the halving schedule). On snapshot import the
head jumps to the checkpoint but the counter kept its old (often inflated) value,
so the node believed it was thousands of blocks behind and refused to mine.

Fix: import_snapshot now recomputes canonical_height as the count of non-instant
canonical blocks in heights [1, checkpoint_height] (genesis is the 0 base and is
NOT counted — matching block_import). These tests lock that in, including the
genesis off-by-one and the instant-block exclusion.
"""

import tempfile
from pathlib import Path

import cbor2

from core.db.block_db import BlockDB
from core.db.snapshot import (
    _count_non_instant_canonical,
    export_snapshot,
    import_snapshot,
)
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header

ZERO = b"\x00" * 32


def _mk_header(height: int, parent: bytes, *, instant: bool = False) -> Header:
    extra = cbor2.dumps({"instant_block": True}) if instant else b""
    return Header(
        v=1, chainId=1, height=height, parentHash=parent,
        timestamp=1_000_000 + height, stateRoot=ZERO, txsRoot=ZERO,
        receiptsRoot=ZERO, proofsRoot=ZERO, daRoot=ZERO, mixSeed=ZERO,
        poiesPolicyRoot=ZERO, pqAlgPolicyRoot=ZERO, thetaMicro=1_000_000,
        workType=0, nonce=height, extra=extra,
    )


def _build_chain(bdb: BlockDB, *, top: int, instant_heights: set[int]) -> dict[int, bytes]:
    """Build a canonical chain of heights 0..top; mark instant_heights as instant."""
    parent = ZERO
    hashes: dict[int, bytes] = {}
    for h in range(0, top + 1):
        hdr = _mk_header(h, parent, instant=(h in instant_heights))
        hh = hdr.hash()
        bdb.put_header(hdr)
        bdb.put_block(Block(header=hdr, txs=(), proofs=(), receipts=None))
        bdb.set_canonical(h, hh)
        hashes[h] = hh
        parent = hh
    bdb.set_head(top, hashes[top])
    bdb.set_chain_id(1)
    return hashes


def test_count_non_instant_excludes_genesis_and_instant_blocks():
    bdb = BlockDB(SQLiteKV(":memory:"))
    _build_chain(bdb, top=5, instant_heights={2, 4})
    # heights 1..5 minus instant {2,4} => {1,3,5} => 3 (genesis 0 NOT counted)
    assert _count_non_instant_canonical(bdb, 5) == 3
    # inclusive upper bound, genesis excluded: height 1 only
    assert _count_non_instant_canonical(bdb, 1) == 1
    # only genesis -> 0
    assert _count_non_instant_canonical(bdb, 0) == 0
    # heights beyond the chain are simply absent (no crash, not counted)
    assert _count_non_instant_canonical(bdb, 50) == 3


def test_count_non_instant_no_instant_equals_height():
    bdb = BlockDB(SQLiteKV(":memory:"))
    _build_chain(bdb, top=7, instant_heights=set())
    # no instant blocks => canonical_height == head height exactly
    assert _count_non_instant_canonical(bdb, 7) == 7


def test_import_snapshot_reconciles_inflated_canonical_height():
    """THE mainnet regression: import must RESET an inflated canonical_height to
    the true non-instant count (the fix's contract), restoring canonical <= head.

    Asserted deterministically against the helper applied to the imported DB,
    because import_snapshot's chunk reader (newline-delimited CBOR) can drop the
    odd entry whose CBOR contains 0x0a — a separate pre-existing format issue. The
    exact-value correctness of the count lives in the direct helper tests above."""
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "snap"
        snap.mkdir()
        src_b = BlockDB(SQLiteKV(":memory:"))
        src_s = StateDB(SQLiteKV(":memory:"))
        _build_chain(src_b, top=5, instant_heights={2, 4})
        export_snapshot(block_db=src_b, state_db=src_s, checkpoint_height=5,
                        output_dir=snap, compress=False)

        dst_b = BlockDB(SQLiteKV(":memory:"))
        dst_s = StateDB(SQLiteKV(":memory:"))
        dst_b.set_canonical_height(99_999)  # the corrupt/inflated value

        import_snapshot(block_db=dst_b, state_db=dst_s, snapshot_dir=snap,
                        verify_hashes=True)

        head_h = dst_b.get_head()[0]
        ch = dst_b.get_canonical_height()
        assert head_h == 5                                # head set to checkpoint
        assert ch != 99_999                               # reconciled, not left inflated
        assert ch <= head_h                               # invariant restored
        # set to exactly the non-instant count of the imported canonical chain
        assert ch == _count_non_instant_canonical(dst_b, head_h)


def test_import_snapshot_sets_canonical_at_or_below_checkpoint():
    """With no instant blocks the reconciled canonical_height equals the count of
    imported canonical heights in [1, checkpoint] — never above checkpoint, and
    not the stale pre-set value (guards the genesis off-by-one too)."""
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "snap"
        snap.mkdir()
        src_b = BlockDB(SQLiteKV(":memory:"))
        src_s = StateDB(SQLiteKV(":memory:"))
        _build_chain(src_b, top=6, instant_heights=set())
        export_snapshot(block_db=src_b, state_db=src_s, checkpoint_height=6,
                        output_dir=snap, compress=False)

        dst_b = BlockDB(SQLiteKV(":memory:"))
        dst_s = StateDB(SQLiteKV(":memory:"))
        dst_b.set_canonical_height(500)

        import_snapshot(block_db=dst_b, state_db=dst_s, snapshot_dir=snap,
                        verify_hashes=True)

        head_h = dst_b.get_head()[0]
        ch = dst_b.get_canonical_height()
        assert head_h == 6
        assert ch != 500                                  # reconciled
        assert ch <= 6                                    # never exceeds checkpoint (no genesis +1)
        assert ch == _count_non_instant_canonical(dst_b, head_h)


def test_p2p_reorg_down_prune_reconciles_canonical_height():
    """Fix A: the P2P reorg-down paths (_reorg_canonical_to_verifier_limit and
    _reset_chain_to_genesis) roll the head down via _prune_canonical_heights — the
    exact path that left canonical_height inflated in the incident. The prune must
    now reconcile the counter to the rolled-down chain (genesis-excluded count)."""
    from p2p.node.p2p_service import P2PService

    bdb = BlockDB(SQLiteKV(":memory:"))
    _build_chain(bdb, top=8, instant_heights={3, 6})
    bdb.set_canonical_height(99_999)  # the inflated/corrupt counter

    # roll the canonical chain down to height 4 (verifier-limit style)
    P2PService._prune_canonical_heights(object(), bdb, above_height=4, batch=None)

    ch = bdb.get_canonical_height()
    # heights 1..4 minus instant {3} => {1,2,4} => 3 (genesis 0 excluded)
    assert ch == 3
    assert ch == _count_non_instant_canonical(bdb, 4)
    assert ch <= 4  # invariant: canonical_height <= head_height


def test_p2p_reset_to_genesis_zeros_canonical_height():
    """Fix A: reset-to-genesis (above_height=0) must drive canonical_height to 0,
    not leave it at the prior inflated value."""
    from p2p.node.p2p_service import P2PService

    bdb = BlockDB(SQLiteKV(":memory:"))
    _build_chain(bdb, top=5, instant_heights=set())
    bdb.set_canonical_height(5_000)

    P2PService._prune_canonical_heights(object(), bdb, above_height=0, batch=None)

    assert bdb.get_canonical_height() == 0
