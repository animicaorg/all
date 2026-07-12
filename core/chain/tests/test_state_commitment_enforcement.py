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


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
