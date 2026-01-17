# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import cbor2

from consensus.rewards import compute_block_reward
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


def _params(chain_id: int) -> ChainParams:
    return ChainParams(
        chain_id=chain_id,
        chain_name="Mainnet",
        genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x01" * 32,
        poies_policy_root=b"\x02" * 32,
        theta_initial=1_000,
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


def _db_bundle(db_path: Path) -> tuple[SQLiteKV, BlockDB, StateDB]:
    kv = SQLiteKV(db_path)
    return kv, BlockDB(kv), StateDB(kv)


def _seal_header(header: Header) -> Header:
    target = _theta_to_target(int(header.thetaMicro))
    for nonce in range(100_000):
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


def _import_genesis(importer: BlockImporter, params: ChainParams) -> Header:
    genesis = Header.genesis(
        chain_id=params.chain_id,
        timestamp=1_700_000_000,
        state_root=ZERO32,
        txs_root=ZERO32,
        receipts_root=ZERO32,
        proofs_root=ZERO32,
        da_root=ZERO32,
        mix_seed=ZERO32,
        poies_policy_root=ZERO32,
        pq_alg_policy_root=ZERO32,
        theta_micro=params.theta_initial,
    )
    res0 = importer.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None))
    assert res0.code == ImportErrorCode.ACCEPTED
    return genesis


def _import_reward_block(
    importer: BlockImporter, genesis: Header, params: ChainParams, coinbase: bytes
) -> int:
    rewards = compute_block_reward(
        chain_id=params.chain_id,
        height=1,
        params=importer.full_params_dict,
    )
    assert rewards
    miner_reward = rewards[0][1]
    reward_txs = [
        Tx(
            unsigned=UnsignedTx.build_coinbase(
                chain_id=params.chain_id,
                height=1,
                to=coinbase,
                amount=miner_reward,
            ),
            sigs=tuple(),
        )
    ]
    txs_root = compute_txs_root_from_txs(reward_txs)
    extra = cbor2.dumps({"coinbase": coinbase})
    header1 = Header(
        v=1,
        chainId=params.chain_id,
        height=1,
        parentHash=genesis.hash(),
        timestamp=1_700_000_010,
        stateRoot=ZERO32,
        txsRoot=txs_root,
        receiptsRoot=ZERO32,
        proofsRoot=ZERO32,
        daRoot=ZERO32,
        mixSeed=ZERO32,
        poiesPolicyRoot=ZERO32,
        pqAlgPolicyRoot=ZERO32,
        thetaMicro=params.theta_initial,
        nonce=0,
        extra=extra,
    )
    header1 = _seal_header(header1)
    block1 = Block.from_components(
        header=header1, txs=tuple(reward_txs), proofs=(), receipts=None
    )
    res1 = importer.import_block(block1)
    assert res1.code == ImportErrorCode.ACCEPTED

    return miner_reward


def test_reward_credited_and_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "chain-0" / "animica.db"
    params = _params(chain_id=0)

    kv, block_db, state_db = _db_bundle(db_path)
    importer = BlockImporter(params=params, block_db=block_db, state_db=state_db)
    genesis = _import_genesis(importer, params)

    coinbase = b"\x11" * 32
    miner_reward = _import_reward_block(importer, genesis, params, coinbase)

    assert state_db.get_balance(coinbase) == miner_reward

    kv.close()

    reopened_kv, _, reopened_state = _db_bundle(db_path)
    assert reopened_state.get_balance(coinbase) == miner_reward
    reopened_kv.close()
