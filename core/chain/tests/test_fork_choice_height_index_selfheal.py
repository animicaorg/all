"""
Regression tests for the by-height-index corruption that froze node operators on
a single block ("stuck on block N").

Root cause: a legacy reorg left an ORPHAN SIBLING block in the canonical by-height
index (k_hix) at a fork-point height — a losing-fork block whose header.height
equals the queried height but which is NOT on the head's parentHash chain. Because
peers sync BY HEIGHT, a node serving such an index returns a block whose hash does
not match the next block's parentHash, so every by-height peer freezes at the lowest
broken height. The previous _reconcile_height_index used a sparse sample + a
header.height==height check, so it could neither sample the lone broken height nor
recognise an orphan-at-correct-height as corrupt.

The fix makes _reconcile_height_index linkage-correct (walk head->genesis by
parentHash and rewrite every disagreeing k_hix entry), hardens _apply_reorg to
re-index the full canonical branch, and wires validate_head_against_checkpoint so a
node wedged on a dead fork rolls its head back below the fork point and re-syncs.
"""

from __future__ import annotations

from pathlib import Path

from core.chain.block_import import BlockImporter, ImportErrorCode, _theta_to_target
from core.chain.head import _reconcile_height_index, validate_head_against_checkpoint
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams


def _params() -> ChainParams:
    return ChainParams(
        chain_id=1337,
        chain_name="Test Chain",
        genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x01" * 32,
        poies_policy_root=b"\x02" * 32,
        theta_initial=100,
        theta_min=100,
        theta_max=1_000_000,
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(
            window=24,
            ema_alpha=0.2,
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=12.0,
            max_bytes=1_500_000,
            max_gas=20_000_000,
            tx_max_bytes=131_072,
            min_gas_price=1000,
        ),
    )


def _seal_header(header: Header) -> Header:
    target = _theta_to_target(int(header.thetaMicro))
    for nonce in range(10000):
        candidate = Header(
            v=header.v,
            chainId=header.chainId,
            height=header.height,
            parentHash=header.parentHash,
            timestamp=header.timestamp,
            stateRoot=header.stateRoot,
            txsRoot=header.txsRoot,
            receiptsRoot=header.receiptsRoot,
            proofsRoot=header.proofsRoot,
            daRoot=header.daRoot,
            mixSeed=header.mixSeed,
            poiesPolicyRoot=header.poiesPolicyRoot,
            pqAlgPolicyRoot=header.pqAlgPolicyRoot,
            thetaMicro=header.thetaMicro,
            nonce=nonce,
            extra=header.extra,
        )
        if int.from_bytes(candidate.hash(), "big") <= target:
            return candidate
    raise AssertionError("Unable to find a valid nonce for test header")


def _header(*, height: int, parent_hash: bytes, timestamp: int, theta_micro: int) -> Header:
    return _seal_header(
        Header(
            v=1,
            chainId=1337,
            height=height,
            parentHash=parent_hash,
            timestamp=timestamp,
            stateRoot=b"\x00" * 32,
            txsRoot=b"\x00" * 32,
            receiptsRoot=b"\x00" * 32,
            proofsRoot=b"\x00" * 32,
            daRoot=b"\x00" * 32,
            mixSeed=b"\x00" * 32,
            poiesPolicyRoot=b"\x00" * 32,
            pqAlgPolicyRoot=b"\x00" * 32,
            thetaMicro=theta_micro,
            nonce=0,
            extra=b"",
        )
    )


def _block(header: Header) -> Block:
    return Block(header=header, txs=(), proofs=(), receipts=None)


def _db(tmp_path: Path) -> BlockDB:
    return BlockDB(SQLiteKV(tmp_path / "blocks.db"))


def _chain_to_fork(importer: BlockImporter):
    """Build genesis -> p(1) -> B(2) -> c(3); head (c@3) descends from B. Returns
    (genesis_res, p_res, B_res, c_res)."""
    g = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    r0 = importer.import_block(_block(g))
    assert r0.code == ImportErrorCode.ACCEPTED
    p = _header(height=1, parent_hash=r0.block_hash, timestamp=1012, theta_micro=100)
    rp = importer.import_block(_block(p))
    assert rp.code == ImportErrorCode.ACCEPTED
    bb = _header(height=2, parent_hash=rp.block_hash, timestamp=1024, theta_micro=100)
    rb = importer.import_block(_block(bb))
    assert rb.code == ImportErrorCode.ACCEPTED
    c = _header(height=3, parent_hash=rb.block_hash, timestamp=1036, theta_micro=100)
    rc = importer.import_block(_block(c))
    assert rc.code == ImportErrorCode.ACCEPTED
    return r0, rp, rb, rc


def test_reconcile_repairs_orphan_sibling_at_fork_height(tmp_path: Path) -> None:
    """The exact 28167 case: an orphan sibling sits in the by-height index at the
    fork height while the head descends from the canonical sibling. The deep
    reconcile must replace the orphan with the canonical block."""
    importer = BlockImporter(params=_params(), block_db=(bdb := _db(tmp_path)))
    _r0, rp, rb, rc = _chain_to_fork(importer)
    B, FORK = rb.block_hash, 2

    # Orphan sibling A at the fork height: same parent (p), different timestamp =>
    # different hash, NOT on the head's parentHash chain. Store it so it resolves
    # by-hash with header.height == FORK (this is what defeats the old probe).
    a = _header(height=FORK, parent_hash=rp.block_hash, timestamp=1025, theta_micro=100)
    A = a.hash()
    assert A != B
    bdb.put_block(_block(a))

    # Corrupt the by-height index: k_hix[FORK] -> orphan A. Head pointer untouched.
    bdb.set_canonical(FORK, A, allow_overwrite=True)
    assert bdb.get_canonical_hash(FORK) == A
    assert bdb.get_head() == (3, rc.block_hash)  # head still descends from B

    _reconcile_height_index(bdb)

    assert (
        bdb.get_canonical_hash(FORK) == B
    ), "orphan sibling at fork height was not repaired to the canonical block"
    # the rest of the index is intact
    assert bdb.get_canonical_hash(3) == rc.block_hash


def test_reconcile_fills_index_hole(tmp_path: Path) -> None:
    """A missing (deleted) k_hix entry is rebuilt from the parentHash walk."""
    importer = BlockImporter(params=_params(), block_db=(bdb := _db(tmp_path)))
    _r0, _rp, rb, _rc = _chain_to_fork(importer)
    from core.db.block_db import k_hix

    bdb.kv.delete(k_hix(2))
    assert bdb.get_canonical_hash(2) is None  # hole

    _reconcile_height_index(bdb)

    assert bdb.get_canonical_hash(2) == rb.block_hash


def test_reorg_rewrites_fork_point_height_index(tmp_path: Path) -> None:
    """A reorg to a heavier branch must overwrite the by-height index at the fork
    point (and every attached height), never leaving the displaced sibling."""
    importer = BlockImporter(params=_params(), block_db=(bdb := _db(tmp_path)))
    g = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    r0 = importer.import_block(_block(g))
    assert r0.code == ImportErrorCode.ACCEPTED
    a1 = _header(height=1, parent_hash=r0.block_hash, timestamp=1012, theta_micro=100)
    ra1 = importer.import_block(_block(a1))
    assert ra1.code == ImportErrorCode.ACCEPTED
    a2 = _header(height=2, parent_hash=ra1.block_hash, timestamp=1024, theta_micro=100)
    ra2 = importer.import_block(_block(a2))
    assert ra2.code == ImportErrorCode.ACCEPTED
    assert bdb.get_canonical_hash(1) == ra1.block_hash  # fork point currently a1

    # Heavier competing branch rooted at genesis (higher theta => higher weight).
    b1 = _header(height=1, parent_hash=r0.block_hash, timestamp=1010, theta_micro=120)
    rb1 = importer.import_block(_block(b1))
    assert rb1.code == ImportErrorCode.ACCEPTED
    b2 = _header(height=2, parent_hash=rb1.block_hash, timestamp=1020, theta_micro=120)
    rb2 = importer.import_block(_block(b2))
    assert rb2.code == ImportErrorCode.ACCEPTED
    assert rb2.head_changed is True
    assert bdb.get_canonical_head() == (2, rb2.block_hash)

    assert bdb.get_canonical_hash(1) == rb1.block_hash  # fork-point index rewritten
    assert bdb.get_canonical_hash(2) == rb2.block_hash


def test_checkpoint_rolls_back_fork_stuck_node(tmp_path: Path) -> None:
    """A node whose head is on the wrong fork at a pinned height rolls its head back
    to just below the checkpoint so it can re-sync the canonical chain."""
    importer = BlockImporter(params=_params(), block_db=(bdb := _db(tmp_path)))
    g = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    r0 = importer.import_block(_block(g))
    p = _header(height=1, parent_hash=r0.block_hash, timestamp=1012, theta_micro=100)
    rp = importer.import_block(_block(p))
    a2 = _header(height=2, parent_hash=rp.block_hash, timestamp=1024, theta_micro=100)
    ra2 = importer.import_block(_block(a2))
    assert bdb.get_canonical_head() == (2, ra2.block_hash)

    pinned_canonical = b"\xbb" * 32  # a different (canonical) hash at height 2
    rolled = validate_head_against_checkpoint(bdb, 2, pinned_canonical)
    assert rolled is True
    head = bdb.get_canonical_head()
    assert head is not None and head[0] == 1 and head[1] == rp.block_hash


def test_checkpoint_noop_for_canonical_node(tmp_path: Path) -> None:
    """A node already on the canonical block at the pinned height is never rolled
    back (cannot brick healthy nodes)."""
    importer = BlockImporter(params=_params(), block_db=(bdb := _db(tmp_path)))
    g = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    r0 = importer.import_block(_block(g))
    p = _header(height=1, parent_hash=r0.block_hash, timestamp=1012, theta_micro=100)
    rp = importer.import_block(_block(p))
    a2 = _header(height=2, parent_hash=rp.block_hash, timestamp=1024, theta_micro=100)
    ra2 = importer.import_block(_block(a2))
    assert bdb.get_canonical_head() == (2, ra2.block_hash)

    rolled = validate_head_against_checkpoint(bdb, 2, ra2.block_hash)
    assert rolled is False
    assert bdb.get_canonical_head() == (2, ra2.block_hash)


def test_checkpoint_noop_when_walk_cannot_reach_height(tmp_path: Path) -> None:
    """If the parentHash walk cannot resolve the checkpoint height (incomplete
    data), the node is NOT rolled back — never act on uncertainty. Even a stale
    by-height index pointing at a wrong hash must not trigger a rollback unless the
    authoritative walk confirms the divergence."""
    importer = BlockImporter(params=_params(), block_db=(bdb := _db(tmp_path)))
    g = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    r0 = importer.import_block(_block(g))
    p = _header(height=1, parent_hash=r0.block_hash, timestamp=1012, theta_micro=100)
    rp = importer.import_block(_block(p))
    a2 = _header(height=2, parent_hash=rp.block_hash, timestamp=1024, theta_micro=100)
    ra2 = importer.import_block(_block(a2))
    # Corrupt the by-height index to a phantom hash at the checkpoint height. The
    # authoritative walk still resolves a2 (the real block), so a pin == a2 is a
    # no-op despite the index lie — proving the decision ignores the index.
    bdb.set_canonical(2, b"\xee" * 32, allow_overwrite=True)
    rolled = validate_head_against_checkpoint(bdb, 2, ra2.block_hash)
    assert rolled is False
    assert bdb.get_canonical_head() == (2, ra2.block_hash)


def test_import_rejects_orphan_at_pinned_checkpoint_height(tmp_path, monkeypatch) -> None:
    """Import-time enforcement: at a pinned height only the canonical block is
    accepted; any other block (the wedging orphan) is rejected, so a re-syncing
    node converges on the pin instead of re-accepting the orphan."""
    import core.network_params as np

    # The canonical block B at height 2 (deterministic seal => stable hash).
    g = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    p = _header(height=1, parent_hash=g.hash(), timestamp=1012, theta_micro=100)
    bb = _header(height=2, parent_hash=p.hash(), timestamp=1024, theta_micro=100)
    B = bb.hash()

    # Pin height 2 -> B for the test chain (devnet/1337), then build a fresh node.
    monkeypatch.setitem(np.PINNED_CHECKPOINTS_BY_NETWORK, ("devnet", 1337), {2: B})
    importer = BlockImporter(params=_params(), block_db=(bdb := _db(tmp_path)))
    assert importer.import_block(_block(g)).code == ImportErrorCode.ACCEPTED
    assert importer.import_block(_block(p)).code == ImportErrorCode.ACCEPTED

    # The orphan A at the pinned height must be REJECTED.
    a = _header(height=2, parent_hash=p.hash(), timestamp=1025, theta_micro=100)
    assert a.hash() != B
    res_a = importer.import_block(_block(a))
    assert res_a.code == ImportErrorCode.INVALID
    assert "checkpoint" in (res_a.reason or "").lower()

    # The canonical B at the pinned height is accepted and becomes head.
    res_b = importer.import_block(_block(bb))
    assert res_b.code == ImportErrorCode.ACCEPTED
    assert bdb.get_canonical_head() == (2, B)


def test_already_stored_orphan_at_pinned_height_rejected_not_duplicate(tmp_path, monkeypatch) -> None:
    """An orphan A already stored at a pinned height (the wedged-node state) must be
    REJECTED on re-import — not silently treated as a DUPLICATE that re-enters fork
    choice and re-takes the head. Pin enforcement runs BEFORE the duplicate path."""
    import core.network_params as np

    bdb = _db(tmp_path)
    importer = BlockImporter(params=_params(), block_db=bdb)
    g = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    assert importer.import_block(_block(g)).code == ImportErrorCode.ACCEPTED
    p = _header(height=1, parent_hash=g.hash(), timestamp=1012, theta_micro=100)
    assert importer.import_block(_block(p)).code == ImportErrorCode.ACCEPTED
    # Orphan A becomes head BEFORE any pin exists (a legacy-wedged node).
    a = _header(height=2, parent_hash=p.hash(), timestamp=1025, theta_micro=100)
    assert importer.import_block(_block(a)).code == ImportErrorCode.ACCEPTED
    A = a.hash()
    assert bdb.get_canonical_head() == (2, A)

    # Canonical B (the pin) — a different hash at the same height. A is now stored.
    bb = _header(height=2, parent_hash=p.hash(), timestamp=1024, theta_micro=100)
    B = bb.hash()
    assert B != A
    monkeypatch.setitem(np.PINNED_CHECKPOINTS_BY_NETWORK, ("devnet", 1337), {2: B})

    # Fresh importer (picks up the pin) re-imports the already-stored orphan A.
    importer2 = BlockImporter(params=_params(), block_db=bdb)
    res = importer2.import_block(_block(a))
    assert (
        res.code == ImportErrorCode.INVALID
    ), "re-import of an already-stored orphan at a pinned height must be rejected, not DUPLICATE"
    assert "checkpoint" in (res.reason or "").lower()
