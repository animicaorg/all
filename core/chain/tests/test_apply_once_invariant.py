from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.chain.block_import import BlockImporter, ImportErrorCode, _theta_to_target
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.encoding.canonical import tx_signing_bytes
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams
from core.types.tx import Tx, UnsignedTx
from core.utils.hash import sha3_256
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
        retarget=RetargetParams(window=24, ema_alpha=0.2, bounds=RetargetBounds(min=0.5, max=2.0)),
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


def _header(*, height: int, parent_hash: bytes, timestamp: int, theta_micro: int, txs_root: bytes) -> Header:
    return _seal_header(
        Header(
            v=1,
            chainId=1337,
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
    )


def _db_bundle(tmp_path: Path) -> tuple[BlockDB, StateDB]:
    kv = SQLiteKV(tmp_path / "chain.db")
    return BlockDB(kv), StateDB(kv)


def _transfer_tx(*, sender: bytes, to: bytes, nonce: int, amount: int) -> Tx:
    unsigned = UnsignedTx.build_transfer(
        chain_id=1337,
        sender=sender,
        nonce=nonce,
        gas_price=0,
        gas_limit=21_000,
        to=to,
        amount=amount,
        version=1,
    )
    return Tx(unsigned=unsigned, sigs=())


def test_block_import_rejects_duplicate_tx_hash_in_single_block(tmp_path: Path) -> None:
    params = _params()
    bdb, state_db = _db_bundle(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb, state_db=state_db)

    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    state_db.set_balance(sender, 1000)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100, txs_root=ZERO32)
    res0 = importer.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None))
    assert res0.code == ImportErrorCode.ACCEPTED

    tx = _transfer_tx(sender=sender, to=recipient, nonce=0, amount=10)
    txs_root = compute_txs_root_from_txs((tx, tx))
    h1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1012, theta_micro=100, txs_root=txs_root)
    block = Block.from_components(header=h1, txs=(tx, tx), proofs=(), receipts=None)

    res = importer.import_block(block)
    assert res.code == ImportErrorCode.INVALID
    assert "duplicate tx hash" in (res.reason or "")
    assert state_db.get_balance(sender) == 1000
    assert state_db.get_balance(recipient) == 0


def test_block_import_duplicate_tx_triggers_mempool_quarantine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import rpc.deps as rpc_deps

    class _MempoolStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def quarantine_hashes(
            self,
            tx_hashes,
            *,
            reason: str = "duplicate_canonical",
            details=None,
            permanent: bool = True,
        ) -> dict[str, int]:
            tx_hashes_list = list(tx_hashes)
            self.calls.append(
                {
                    "tx_hashes": tx_hashes_list,
                    "reason": reason,
                    "details": dict(details or {}),
                    "permanent": bool(permanent),
                }
            )
            return {"quarantined": len(tx_hashes_list), "removed": 0}

    mempool_stub = _MempoolStub()
    monkeypatch.setattr(rpc_deps, "get_ctx", lambda: SimpleNamespace(mempool=mempool_stub))

    params = _params()
    bdb, state_db = _db_bundle(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb, state_db=state_db)

    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    state_db.set_balance(sender, 1000)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100, txs_root=ZERO32)
    res0 = importer.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None))
    assert res0.code == ImportErrorCode.ACCEPTED

    tx = _transfer_tx(sender=sender, to=recipient, nonce=0, amount=10)
    txs_root = compute_txs_root_from_txs((tx, tx))
    h1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1012, theta_micro=100, txs_root=txs_root)
    block = Block.from_components(header=h1, txs=(tx, tx), proofs=(), receipts=None)

    res = importer.import_block(block)
    assert res.code == ImportErrorCode.INVALID
    assert mempool_stub.calls, "duplicate resolver should quarantine offending tx hash"
    first_call = mempool_stub.calls[0]
    assert first_call["reason"] == "duplicate_canonical"
    assert first_call["permanent"] is True
    tx_hashes = first_call["tx_hashes"]
    assert isinstance(tx_hashes, list)
    assert len(tx_hashes) == 1
    assert str(tx_hashes[0]).startswith("0x")


def test_block_import_legacy_tx_index_replay_still_quarantines_canonical_tx_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import rpc.deps as rpc_deps

    class _MempoolStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def quarantine_hashes(
            self,
            tx_hashes,
            *,
            reason: str = "duplicate_canonical",
            details=None,
            permanent: bool = True,
        ) -> dict[str, int]:
            tx_hashes_list = list(tx_hashes)
            self.calls.append(
                {
                    "tx_hashes": tx_hashes_list,
                    "reason": reason,
                    "details": dict(details or {}),
                    "permanent": bool(permanent),
                }
            )
            return {"quarantined": len(tx_hashes_list), "removed": 0}

    class _LegacyTxIndex:
        def __init__(self, seen_hashes: set[bytes]) -> None:
            self.seen_hashes = set(seen_hashes)

        def exists(self, tx_hash: bytes) -> bool:
            return bytes(tx_hash) in self.seen_hashes

    mempool_stub = _MempoolStub()
    monkeypatch.setattr(rpc_deps, "get_ctx", lambda: SimpleNamespace(mempool=mempool_stub))

    params = _params()
    bdb, state_db = _db_bundle(tmp_path)

    sender = b"\x51" * 32
    recipient = b"\x52" * 32
    state_db.set_balance(sender, 1000)

    tx = _transfer_tx(sender=sender, to=recipient, nonce=0, amount=10)
    legacy_signing_hash = sha3_256(tx_signing_bytes(tx))
    tx_index = _LegacyTxIndex({legacy_signing_hash})
    importer = BlockImporter(params=params, block_db=bdb, state_db=state_db, tx_index=tx_index)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100, txs_root=ZERO32)
    res0 = importer.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None))
    assert res0.code == ImportErrorCode.ACCEPTED

    txs_root = compute_txs_root_from_txs((tx,))
    h1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1012, theta_micro=100, txs_root=txs_root)
    block = Block.from_components(header=h1, txs=(tx,), proofs=(), receipts=None)

    res = importer.import_block(block)
    assert res.code == ImportErrorCode.INVALID
    assert "tx already applied on canonical chain" in (res.reason or "")
    assert mempool_stub.calls, "duplicate resolver should quarantine canonical tx hash"
    first_call = mempool_stub.calls[0]
    tx_hashes = first_call["tx_hashes"]
    assert isinstance(tx_hashes, list)
    assert "0x" + tx.txid().hex() in tx_hashes


def test_block_import_rejects_duplicate_sender_nonce_even_with_distinct_hashes(tmp_path: Path) -> None:
    params = _params()
    bdb, state_db = _db_bundle(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb, state_db=state_db)

    sender = b"\x33" * 32
    recipient = b"\x44" * 32
    state_db.set_balance(sender, 1000)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100, txs_root=ZERO32)
    res0 = importer.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None))
    assert res0.code == ImportErrorCode.ACCEPTED

    tx_a = _transfer_tx(sender=sender, to=recipient, nonce=0, amount=10)
    tx_b = _transfer_tx(sender=sender, to=recipient, nonce=0, amount=11)

    txs_root = compute_txs_root_from_txs((tx_a, tx_b))
    h1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1012, theta_micro=100, txs_root=txs_root)
    block = Block.from_components(header=h1, txs=(tx_a, tx_b), proofs=(), receipts=None)

    res = importer.import_block(block)
    assert res.code == ImportErrorCode.INVALID
    assert "duplicate sender+nonce" in (res.reason or "")
    assert state_db.get_balance(sender) == 1000
    assert state_db.get_balance(recipient) == 0


def test_importing_same_block_twice_does_not_reapply_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # This test exercises the apply-once invariant (importing the same block
    # twice must not re-apply state), not signature/root policy. Push the
    # height-gated PQ-hardening and root-commitment checks past the test's
    # block heights so the unsigned devnet fixture block imports. Dedicated
    # tests cover the sig/root gates.
    monkeypatch.setenv("ANIMICA_FORK_PQ_HARDENING_HEIGHT", "999999999")
    monkeypatch.setenv("ANIMICA_FORK_ROOT_COMMITMENT_HEIGHT", "999999999")

    params = _params()
    bdb, state_db = _db_bundle(tmp_path)
    importer = BlockImporter(params=params, block_db=bdb, state_db=state_db)

    sender = b"\x41" * 32
    recipient = b"\x42" * 32
    state_db.set_balance(sender, 1000)

    genesis = _header(height=0, parent_hash=b"\x00" * 32, timestamp=1000, theta_micro=100, txs_root=ZERO32)
    res0 = importer.import_block(Block(header=genesis, txs=(), proofs=(), receipts=None))
    assert res0.code == ImportErrorCode.ACCEPTED

    tx = _transfer_tx(sender=sender, to=recipient, nonce=0, amount=10)
    txs_root = compute_txs_root_from_txs((tx,))
    h1 = _header(height=1, parent_hash=res0.block_hash, timestamp=1012, theta_micro=100, txs_root=txs_root)
    block = Block.from_components(header=h1, txs=(tx,), proofs=(), receipts=None)

    first = importer.import_block(block)
    assert first.code == ImportErrorCode.ACCEPTED
    assert state_db.get_balance(sender) == 990
    assert state_db.get_balance(recipient) == 10

    second = importer.import_block(block)
    assert second.code == ImportErrorCode.DUPLICATE
    assert state_db.get_balance(sender) == 990
    assert state_db.get_balance(recipient) == 10
