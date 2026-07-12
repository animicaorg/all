"""7.1.9 FORK_STATE_COMMITMENT enforcement (devnet: active from genesis).

A block that commits a NON-ZERO stateRoot must commit the REAL post-execution
root, or every node rejects it. Proven here end-to-end through import_block:
  * correct sealed root  → ACCEPTED, head advances;
  * wrong non-zero root  → REJECTED, head unchanged, block marked invalid,
                           re-import short-circuited (no head stall);
  * zero/uncommitted root → ACCEPTED (self-gate; today's miners are unaffected).
"""
from __future__ import annotations

from pathlib import Path

from core.chain.block_import import BlockImporter, ImportErrorCode, _theta_to_target
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams
from core.utils.hash import ZERO32

CHAIN_ID = 1337
SENDER = b"\x11" * 32

# Empty (coinbase-only-shaped) blocks: they carry no txs, so they skip signature
# validation, yet the post-apply state root is still NON-ZERO (it commits the
# funded SENDER account), which is exactly what the self-gate + enforcement key on.
# Execution-changes-state is proven separately in test_seal_state_root_roundtrip.


def _params() -> ChainParams:
    return ChainParams(
        chain_id=CHAIN_ID, chain_name="Dev", genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32, alg_policy_root=b"\x01" * 32, poies_policy_root=b"\x02" * 32,
        theta_initial=100, theta_min=100, theta_max=1_000_000, gamma_total_cap=1_000_000,
        retarget=RetargetParams(window=24, ema_alpha=0.2, bounds=RetargetBounds(min=0.5, max=2.0)),
        block=BlockLimits(target_seconds=12.0, max_bytes=1_500_000, max_gas=20_000_000,
                          tx_max_bytes=131_072, min_gas_price=0),
    )


def _seal(header: Header) -> Header:
    target = _theta_to_target(int(header.thetaMicro))
    for nonce in range(200000):
        cand = Header(
            v=header.v, chainId=header.chainId, height=header.height, parentHash=header.parentHash,
            timestamp=header.timestamp, stateRoot=header.stateRoot, txsRoot=header.txsRoot,
            receiptsRoot=header.receiptsRoot, proofsRoot=header.proofsRoot, daRoot=header.daRoot,
            mixSeed=header.mixSeed, poiesPolicyRoot=header.poiesPolicyRoot,
            pqAlgPolicyRoot=header.pqAlgPolicyRoot, thetaMicro=header.thetaMicro, nonce=nonce,
            extra=header.extra,
        )
        if int.from_bytes(cand.hash(), "big") <= target:
            return cand
    raise AssertionError("no nonce found")


def _header(*, height, parent, txs_root, state_root, timestamp=1000) -> Header:
    return _seal(Header(
        v=1, chainId=CHAIN_ID, height=height, parentHash=parent, timestamp=timestamp,
        stateRoot=state_root, txsRoot=txs_root, receiptsRoot=ZERO32, proofsRoot=ZERO32,
        daRoot=ZERO32, mixSeed=ZERO32, poiesPolicyRoot=ZERO32, pqAlgPolicyRoot=ZERO32,
        thetaMicro=100, nonce=0, extra=b"",
    ))


def _mk(tmp_path: Path):
    kv = SQLiteKV(tmp_path / "chain.db")
    imp = BlockImporter(params=_params(), block_db=BlockDB(kv), state_db=StateDB(kv))
    imp.state_db.set_balance(SENDER, 10_000_000)
    genesis = _header(height=0, parent=b"\x00" * 32, txs_root=ZERO32, state_root=ZERO32)
    assert imp.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None)).code == ImportErrorCode.ACCEPTED
    return imp, genesis.hash()


def _empty_block(genesis_hash, *, state_root, timestamp=1000) -> Block:
    hdr = _header(height=1, parent=genesis_hash, txs_root=ZERO32, state_root=state_root, timestamp=timestamp)
    return Block(header=hdr, txs=(), proofs=(), receipts=None)


def test_correct_sealed_root_is_accepted_and_advances_head(tmp_path: Path) -> None:
    imp, g = _mk(tmp_path)
    # Seal the REAL root the miner would commit (non-zero: commits funded SENDER).
    draft = _empty_block(g, state_root=ZERO32)
    sealed = imp.compute_sealed_state_root(draft)
    assert sealed != ZERO32
    block = _empty_block(g, state_root=sealed)

    res = imp.import_block(block)
    assert res.code == ImportErrorCode.ACCEPTED, res.reason
    assert imp.block_db.get_canonical_head()[0] == 1  # head advanced


def test_wrong_nonzero_root_is_rejected_head_unchanged_and_reimport_shortcircuits(tmp_path: Path) -> None:
    imp, g = _mk(tmp_path)
    bad = _empty_block(g, state_root=b"\xab" * 32)

    res = imp.import_block(bad)
    assert res.code == ImportErrorCode.INVALID, res.reason
    # Head stayed at genesis; the bad block never became canonical.
    assert imp.block_db.get_canonical_head()[0] == 0
    # Durably marked invalid; re-import short-circuits (no head-stall loop).
    assert bad.header.hash() in imp._invalid_blocks
    res2 = imp.import_block(bad)
    assert res2.code == ImportErrorCode.INVALID
    assert "previously rejected" in (res2.reason or "")


def test_zero_root_block_is_accepted_selfgate(tmp_path: Path) -> None:
    # A block that commits a ZERO stateRoot (today's miners) is NOT rejected.
    imp, g = _mk(tmp_path)
    res = imp.import_block(_empty_block(g, state_root=ZERO32))
    assert res.code == ImportErrorCode.ACCEPTED, res.reason
    assert imp.block_db.get_canonical_head()[0] == 1


# ── Adversarial-review regression tests (split / corruption / stall fixes) ──

def test_enforcement_lives_in_apply_block_state_chokepoint(tmp_path: Path) -> None:
    # Split fix: the check must live in _apply_block_state (which EVERY apply path
    # funnels through — normal attach, missing-snapshot rebuild, startup rebuild),
    # NOT only in the _apply_state_reorg attach loop. Call it directly.
    imp, g = _mk(tmp_path)
    good = _empty_block(g, state_root=imp.compute_sealed_state_root(_empty_block(g, state_root=ZERO32)))
    assert imp._apply_block_state(good) is True  # correct root applies

    imp2, g2 = _mk(tmp_path / "b")
    bad = _empty_block(g2, state_root=b"\xcd" * 32)
    assert imp2._apply_block_state(bad) is False           # wrong root rejected here
    assert bad.header.hash() in imp2._invalid_blocks        # and recorded durably


def test_restore_purges_poisoned_snapshots_above_head(tmp_path: Path) -> None:
    # Corruption F2 / liveness A: a failed reorg must not leave rejected-branch
    # snapshots at heights above the restored head (they poison later rebuilds).
    imp, g = _mk(tmp_path)
    good_snap = imp.state_db.snapshot()  # valid → avoids the rebuild fallback path
    imp._state_snapshots[5] = object()   # simulate poisoned captures above head 0
    imp._state_snapshots[6] = object()
    imp._restore_pre_reorg_state(
        old_head=(0, g), old_canonical_height=0, old_canonical_hashes={},
        old_state_snapshot=good_snap,
    )
    assert 5 not in imp._state_snapshots
    assert 6 not in imp._state_snapshots


def test_restore_purges_full_affected_range_below_head(tmp_path: Path) -> None:
    # Final re-review BLOCKER: a failed DEEP reorg poisons snapshots at heights
    # BELOW old_head too (down to fork_point+1), and the primary get(lca_height)
    # path fetches them bypassing the ancestor cap. Purge must clear the WHOLE
    # affected range (from min(old_canonical_hashes)), not just > old_head.
    imp, g = _mk(tmp_path)
    good = imp.state_db.snapshot()
    imp._state_snapshots[3] = object()   # poisoned, <= old_head (the bug)
    imp._state_snapshots[4] = object()   # poisoned, <= old_head
    imp._state_snapshots[7] = object()   # poisoned, > old_head
    old_canon = {3: b"\x33" * 32, 4: b"\x44" * 32, 5: b"\x55" * 32}  # affected_start=3
    imp._restore_pre_reorg_state(
        old_head=(5, b"\x55" * 32), old_canonical_height=5,
        old_canonical_hashes=old_canon, old_state_snapshot=good,
    )
    assert 3 not in imp._state_snapshots   # <= old_head, now purged (was the hole)
    assert 4 not in imp._state_snapshots
    assert 7 not in imp._state_snapshots
    assert imp._state_snapshots.get(5) is good  # old_head re-established


def test_restore_self_heals_state_when_snapshot_is_none(tmp_path: Path) -> None:
    # Corruption F1 (CRITICAL): if the in-memory snapshot is None, restore must
    # self-heal state from canonical instead of leaving the rejected branch applied
    # (silent balance divergence at the correct head).
    imp, g = _mk(tmp_path)
    imp._state_snapshots.setdefault(0, imp.state_db.snapshot())  # baseline at head 0
    imp.state_db.set_balance(SENDER, 999)  # simulate "rejected branch applied"
    imp._restore_pre_reorg_state(
        old_head=(0, g), old_canonical_height=0, old_canonical_hashes={},
        old_state_snapshot=None,  # the corruption trigger
    )
    # State recovered to head-0's real value, not left at the poisoned 999.
    assert imp.state_db.get_balance(SENDER) == 10_000_000


def test_rebuild_baseline_is_constrained_to_ancestor(tmp_path: Path) -> None:
    # Re-review HIGH fix: _rebuild_state_from_canonical must not revert to a
    # height-keyed snapshot that isn't a true ancestor of the target (an old-branch
    # snapshot from a prior reorg) — that would recompute an honest block against the
    # wrong pre-state and false-reject it. With max_baseline_height=LCA it refuses the
    # non-ancestor baseline and drops to a <=LCA ancestor (genesis here).
    imp, g = _mk(tmp_path)
    imp._state_snapshots.setdefault(0, imp.state_db.snapshot())  # genesis baseline
    imp._state_snapshots[10] = imp.state_db.snapshot()           # poisoned high snapshot

    # Unconstrained: picks baseline=10 (empty replay) → True.
    assert imp._rebuild_state_from_canonical(10) is True
    # Constrained to ancestor (LCA=3): rejects the height-10 baseline, falls to
    # genesis(0), then needs canonical hashes 1..10 which don't exist here → False.
    assert imp._rebuild_state_from_canonical(10, max_baseline_height=3) is False


def test_invalid_blocks_set_is_bounded(tmp_path: Path) -> None:
    # Liveness B: the durable invalid set is capped (eviction is safe — re-import
    # re-rejects deterministically).
    imp, _ = _mk(tmp_path)
    cap = imp._INVALID_BLOCKS_CAP
    for i in range(cap + 50):
        imp._record_invalid_block(i.to_bytes(32, "big"))
    assert len(imp._invalid_blocks) <= cap


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
