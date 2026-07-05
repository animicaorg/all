from __future__ import annotations

from pathlib import Path

import pytest

from core.chain.block_import import BlockImporter, ImportErrorCode, _theta_to_target
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams
from core.types.tx import Tx, UnsignedTx
from core.utils.hash import ZERO32
from core.utils.merkle import compute_txs_root_from_txs


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
            min_gas_price=0,
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
    raise AssertionError("Unable to find valid nonce for test header")


def _header(
    *,
    height: int,
    parent_hash: bytes,
    timestamp: int,
    theta_micro: int,
    txs_root: bytes,
    chain_id: int = 1337,
) -> Header:
    header = Header(
        v=1,
        chainId=chain_id,
        height=height,
        parentHash=parent_hash,
        timestamp=timestamp,
        stateRoot=ZERO32,
        txsRoot=txs_root,
        receiptsRoot=ZERO32,
        proofsRoot=ZERO32,
        daRoot=ZERO32,
        mixSeed=ZERO32,
        poiesPolicyRoot=ZERO32,
        pqAlgPolicyRoot=ZERO32,
        thetaMicro=theta_micro,
        nonce=0,
        extra=b"",
    )
    return _seal_header(header)


def _db_bundle(tmp_path: Path) -> tuple[BlockDB, StateDB]:
    kv = SQLiteKV(tmp_path / "chain.db")
    return BlockDB(kv), StateDB(kv)


def _transfer_tx(*, chain_id: int, sender: bytes, to: bytes, nonce: int, amount: int) -> Tx:
    unsigned = UnsignedTx.build_transfer(
        chain_id=chain_id,
        sender=sender,
        nonce=nonce,
        gas_price=0,
        gas_limit=21_000,
        to=to,
        amount=amount,
        version=1,
    )
    return Tx(unsigned=unsigned, sigs=())


def test_reorg_state_failure_auto_recovers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # This test exercises reorg state-failure auto-recovery, not signature/root
    # policy. Push the height-gated PQ-hardening and root-commitment checks past
    # the test's block heights so the unsigned devnet fixture blocks import.
    # Dedicated tests cover the sig/root gates.
    monkeypatch.setenv("ANIMICA_FORK_PQ_HARDENING_HEIGHT", "999999999")
    monkeypatch.setenv("ANIMICA_FORK_ROOT_COMMITMENT_HEIGHT", "999999999")

    params = _params()
    bdb, state_db = _db_bundle(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb, state_db=state_db)

    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    state_db.set_balance(sender, 1000)

    genesis = _header(
        height=0,
        parent_hash=b"\x00" * 32,
        timestamp=1000,
        theta_micro=100,
        txs_root=ZERO32,
    )
    res0 = importer.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None))
    assert res0.code == ImportErrorCode.ACCEPTED

    tx = _transfer_tx(chain_id=params.chain_id, sender=sender, to=recipient, nonce=0, amount=100)
    txs_root = compute_txs_root_from_txs((tx,))
    h1 = _header(
        height=1,
        parent_hash=res0.block_hash,
        timestamp=1010,
        theta_micro=100,
        txs_root=txs_root,
    )
    block1 = Block.from_components(header=h1, txs=(tx,), proofs=(), receipts=None)

    original_apply = BlockImporter._apply_block_state
    calls = {"count": 0}

    def _fail_once(self: BlockImporter, block: Block) -> bool:
        if int(getattr(block.header, "height", 0) or 0) == 1 and calls["count"] == 0:
            calls["count"] += 1
            return False
        return original_apply(self, block)

    monkeypatch.setattr(BlockImporter, "_apply_block_state", _fail_once)

    res1 = importer.import_block(block1)
    assert res1.code == ImportErrorCode.ACCEPTED
    assert calls["count"] == 1
    assert state_db.get_balance(sender) == 900
    assert state_db.get_balance(recipient) == 100
