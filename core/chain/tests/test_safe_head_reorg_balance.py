from __future__ import annotations

from pathlib import Path

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
from core.chain import block_import


def _params() -> ChainParams:
    return ChainParams(
        chain_id=1337,
        chain_name="Test Chain",
        genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x01" * 32,
        poies_policy_root=b"\x02" * 32,
        theta_initial=100,
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
    raise AssertionError("Unable to find a valid nonce for test header")


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
    )
    return Tx(unsigned=unsigned, sigs=())


def test_safe_head_snapshot_stable_on_tip_reorg(tmp_path: Path) -> None:
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

    parent_hash = res0.block_hash
    for height in range(1, 4):
        header = _header(
            height=height,
            parent_hash=parent_hash,
            timestamp=1000 + height * 10,
            theta_micro=100,
            txs_root=ZERO32,
        )
        res = importer.import_block(Block(header=header, txs=(), proofs=(), receipts=None))
        assert res.code == ImportErrorCode.ACCEPTED
        parent_hash = header.hash()

    tx = _transfer_tx(chain_id=params.chain_id, sender=sender, to=recipient, nonce=0, amount=100)
    txs_root = compute_txs_root_from_txs((tx,))
    a4 = _header(
        height=4,
        parent_hash=parent_hash,
        timestamp=1040,
        theta_micro=100,
        txs_root=txs_root,
    )
    block_a4 = Block.from_components(header=a4, txs=(tx,), proofs=(), receipts=None)
    res_a4 = importer.import_block(block_a4)
    assert res_a4.code == ImportErrorCode.ACCEPTED

    assert state_db.get_balance(recipient) == 100

    safe_snapshot = block_import.get_state_snapshot(
        bdb,
        state_db,
        None,
        genesis_path=None,
        height=2,
    )
    assert safe_snapshot is not None
    assert safe_snapshot.get_balance(recipient) == 0

    b4 = _header(
        height=4,
        parent_hash=parent_hash,
        timestamp=1041,
        theta_micro=200,
        txs_root=ZERO32,
    )
    res_b4 = importer.import_block(Block(header=b4, txs=(), proofs=(), receipts=None))
    assert res_b4.code == ImportErrorCode.ACCEPTED

    assert state_db.get_balance(recipient) == 0

    safe_snapshot_after = block_import.get_state_snapshot(
        bdb,
        state_db,
        None,
        genesis_path=None,
        height=2,
    )
    assert safe_snapshot_after is not None
    assert safe_snapshot_after.get_balance(recipient) == 0
