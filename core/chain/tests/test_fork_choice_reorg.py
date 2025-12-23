from __future__ import annotations

from pathlib import Path

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


def _header(
    *,
    height: int,
    parent_hash: bytes,
    timestamp: int,
    theta_micro: int,
    chain_id: int = 1337,
) -> Header:
    header = Header(
        v=1,
        chainId=chain_id,
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
    return _seal_header(header)


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


def _block(header: Header) -> Block:
    return Block(header=header, txs=(), proofs=(), receipts=None)


def _db(tmp_path: Path) -> BlockDB:
    kv = SQLiteKV(tmp_path / "blocks.db")
    return BlockDB(kv)


def test_reorgs_to_heavier_chain(tmp_path: Path) -> None:
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

    # Competing fork B with higher cumulative weight after depth 2
    b1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1010, theta_micro=120)
    res_b1 = importer.import_block(_block(b1))
    assert res_b1.code == ImportErrorCode.ACCEPTED

    b2 = _header(height=2, parent_hash=res_b1.block_hash, timestamp=1020, theta_micro=120)
    res_b2 = importer.import_block(_block(b2))
    assert res_b2.code == ImportErrorCode.ACCEPTED

    head = bdb.get_canonical_head()
    assert head is not None
    assert head[0] == 2
    assert head[1] == res_b2.block_hash
    assert bdb.get_canonical_hash(1) == res_b1.block_hash


def test_orphan_block_resolves_when_parent_arrives(tmp_path: Path) -> None:
    params = _params()
    bdb = _db(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    res0 = importer.import_block(_block(genesis))
    assert res0.code == ImportErrorCode.ACCEPTED

    parent = _header(height=1, parent_hash=res0.block_hash, timestamp=1012, theta_micro=100)
    child = _header(height=2, parent_hash=parent.hash(), timestamp=1024, theta_micro=100)

    orphan_res = importer.import_block(_block(child))
    assert orphan_res.code == ImportErrorCode.ORPHAN

    parent_res = importer.import_block(_block(parent))
    assert parent_res.code == ImportErrorCode.ACCEPTED

    head = bdb.get_canonical_head()
    assert head is not None
    assert head[0] == 2


def test_fork_choice_tiebreaks_by_lowest_hash(tmp_path: Path) -> None:
    params = _params()
    bdb = _db(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100)
    res0 = importer.import_block(_block(genesis))
    assert res0.code == ImportErrorCode.ACCEPTED

    a1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1010, theta_micro=100)
    b1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1011, theta_micro=100)

    res_a1 = importer.import_block(_block(a1))
    res_b1 = importer.import_block(_block(b1))
    assert res_a1.code == ImportErrorCode.ACCEPTED
    assert res_b1.code == ImportErrorCode.ACCEPTED

    head = bdb.get_canonical_head()
    assert head is not None
    expected = min(res_a1.block_hash, res_b1.block_hash)
    assert head[1] == expected
