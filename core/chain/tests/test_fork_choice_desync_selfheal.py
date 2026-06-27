"""
Regression test for the recurring "chain head frozen / chain reset" incident.

Root cause: the cached BlockImporter's in-memory fork-choice ``_best`` can desync
from the durable canonical head in the DB — e.g. a prior reorg advanced the
in-memory best to a tip, then the state-apply failed and ``_restore_pre_reorg_state``
reverted the *DB* head but left the in-memory ``_best`` pointing at the (now
phantom) tip. From then on, every freshly mined block that legitimately extends
the persisted canonical head loses the cumulative-weight / tie-break comparison
against the phantom best, so ``add_block`` reports ``became_best=False``,
``_apply_reorg`` is skipped and the head never advances — yet ``import_block``
still returns ACCEPTED. The chain stalls until the process is restarted (which
rebuilds the fork choice from the DB).

The fix makes ``_apply_fork_choice`` self-heal: a block whose parent IS the
persisted canonical head (with positive weight) must advance the head; if the
in-memory tree disagrees it is rebuilt from the authoritative DB and re-evaluated.
"""

from __future__ import annotations

from pathlib import Path

from consensus.fork_choice import BestTip, Node
from core.chain.block_import import BlockImporter, ImportErrorCode, _theta_to_target
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


def test_head_advances_despite_phantom_in_memory_best(tmp_path: Path) -> None:
    params = _params()
    bdb = _db(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    res0 = importer.import_block(_block(genesis))
    assert res0.code == ImportErrorCode.ACCEPTED

    a1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1012, theta_micro=100)
    res_a1 = importer.import_block(_block(a1))
    assert res_a1.code == ImportErrorCode.ACCEPTED

    a2 = _header(height=2, parent_hash=res_a1.block_hash, timestamp=1024, theta_micro=100)
    res_a2 = importer.import_block(_block(a2))
    assert res_a2.code == ImportErrorCode.ACCEPTED

    head = bdb.get_canonical_head()
    assert head is not None and head[0] == 2 and head[1] == res_a2.block_hash

    # --- Inject the desync: a phantom, heavier in-memory best at height 3 that is
    # NOT reflected in the DB canonical head (which is still a2 @ height 2). This is
    # the exact state left behind by a failed reorg whose DB head was restored but
    # whose in-memory `_best` was not.
    fc = importer.fork_choice
    assert fc is not None
    a2_node = fc.nodes[res_a2.block_hash]
    phantom_hash = b"\xcc" * 32
    phantom_cum = a2_node.cum_weight_micro + 10_000_000
    fc.nodes[phantom_hash] = Node(
        h=phantom_hash,
        parent=res_a2.block_hash,
        height=3,
        weight_micro=10_000_000,
        cum_weight_micro=phantom_cum,
    )
    fc._best = BestTip(h=phantom_hash, height=3, cum_weight_micro=phantom_cum)

    # Now mine the *real* next block extending the canonical head a2. Its cumulative
    # weight (a2.cum + 100) is far below the phantom best, so without the self-heal
    # it would be ACCEPTED-but-not-canonical and the head would stay frozen at 2.
    a3 = _header(height=3, parent_hash=res_a2.block_hash, timestamp=1036, theta_micro=100)
    res_a3 = importer.import_block(_block(a3))
    assert res_a3.code == ImportErrorCode.ACCEPTED

    # With the fix, the head must advance to the real a3.
    assert res_a3.head_changed is True
    head = bdb.get_canonical_head()
    assert head is not None
    assert head[0] == 3, f"head stuck at {head[0]} — fork-choice desync not self-healed"
    assert head[1] == res_a3.block_hash
    assert bdb.get_canonical_hash(3) == res_a3.block_hash
