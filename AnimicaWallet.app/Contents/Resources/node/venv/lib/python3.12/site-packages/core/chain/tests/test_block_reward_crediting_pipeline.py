from __future__ import annotations

from pathlib import Path

import cbor2

from core.chain.block_import import BlockImporter, ImportErrorCode, _theta_to_target
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams

COINBASE_A = bytes.fromhex("11" * 32)
COINBASE_B = bytes.fromhex("22" * 32)
REWARD_300_ANM = 300_000_000_000


def _params() -> ChainParams:
    return ChainParams(
        chain_id=1,
        chain_name="Animica Mainnet",
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
            target_seconds=2.0,
            max_bytes=1_500_000,
            max_gas=20_000_000,
            tx_max_bytes=131_072,
            min_gas_price=1000,
        ),
    )


def _seal_header(header: Header) -> Header:
    target = _theta_to_target(int(header.thetaMicro))
    for nonce in range(200_000):
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
    raise AssertionError("failed to mine test header")


def _header(*, height: int, parent_hash: bytes, timestamp: int, coinbase: bytes) -> Header:
    extra = cbor2.dumps({"coinbase": coinbase})
    return _seal_header(
        Header(
            v=1,
            chainId=1,
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
            thetaMicro=100,
            nonce=0,
            extra=extra,
        )
    )


def _importer(tmp_path: Path) -> tuple[BlockImporter, StateDB]:
    bkv = SQLiteKV(tmp_path / "blocks.db")
    skv = SQLiteKV(tmp_path / "state.db")
    importer = BlockImporter(
        params=_params(),
        block_db=BlockDB(bkv),
        state_db=StateDB(skv),
    )
    return importer, importer.state_db


def test_block_reward_credited_on_apply_block_and_logs(tmp_path: Path, caplog) -> None:
    importer, state_db = _importer(tmp_path)

    genesis = Block(header=_header(height=0, parent_hash=b"\x00" * 32, timestamp=1_000, coinbase=COINBASE_A), txs=(), proofs=(), receipts=None)
    res0 = importer.import_block(genesis)
    assert res0.code == ImportErrorCode.ACCEPTED

    caplog.set_level("INFO", logger="animica.chain.block_import")
    block1 = Block(header=_header(height=1, parent_hash=res0.block_hash, timestamp=1_002, coinbase=COINBASE_A), txs=(), proofs=(), receipts=None)
    res1 = importer.import_block(block1)
    assert res1.code == ImportErrorCode.ACCEPTED

    assert state_db.get_balance(COINBASE_A) == REWARD_300_ANM
    assert any("STATE_CREDIT" in rec.message for rec in caplog.records)


def test_reward_survives_restart(tmp_path: Path) -> None:
    importer, state_db = _importer(tmp_path)
    genesis = Block(header=_header(height=0, parent_hash=b"\x00" * 32, timestamp=2_000, coinbase=COINBASE_A), txs=(), proofs=(), receipts=None)
    res0 = importer.import_block(genesis)
    block1 = Block(header=_header(height=1, parent_hash=res0.block_hash, timestamp=2_002, coinbase=COINBASE_A), txs=(), proofs=(), receipts=None)
    assert importer.import_block(block1).code == ImportErrorCode.ACCEPTED
    assert state_db.get_balance(COINBASE_A) == REWARD_300_ANM

    importer2, state_db2 = _importer(tmp_path)
    assert state_db2.get_balance(COINBASE_A) == REWARD_300_ANM


def test_reward_reverts_on_reorg(tmp_path: Path) -> None:
    importer, state_db = _importer(tmp_path)

    g = Block(header=_header(height=0, parent_hash=b"\x00" * 32, timestamp=3_000, coinbase=COINBASE_A), txs=(), proofs=(), receipts=None)
    g_res = importer.import_block(g)

    a1 = Block(header=_header(height=1, parent_hash=g_res.block_hash, timestamp=3_002, coinbase=COINBASE_A), txs=(), proofs=(), receipts=None)
    a1_res = importer.import_block(a1)
    assert a1_res.code == ImportErrorCode.ACCEPTED
    assert state_db.get_balance(COINBASE_A) == REWARD_300_ANM

    b1 = Block(header=_header(height=1, parent_hash=g_res.block_hash, timestamp=3_003, coinbase=COINBASE_B), txs=(), proofs=(), receipts=None)
    b1_res = importer.import_block(b1)
    assert b1_res.code == ImportErrorCode.ACCEPTED

    b2 = Block(header=_header(height=2, parent_hash=b1_res.block_hash, timestamp=3_005, coinbase=COINBASE_B), txs=(), proofs=(), receipts=None)
    b2_res = importer.import_block(b2)
    assert b2_res.code == ImportErrorCode.ACCEPTED

    assert state_db.get_balance(COINBASE_A) == 0
    assert state_db.get_balance(COINBASE_B) == REWARD_300_ANM * 2
