import json
import tempfile
from pathlib import Path

import pytest

from core.db.block_db import BlockDB
from core.db.snapshot import export_snapshot, import_snapshot
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block, compute_txs_root_from_txs
from core.types.header import Header


def _zero_hash() -> bytes:
    return b"\x00" * 32


def _build_db() -> tuple[BlockDB, StateDB]:
    block_kv = SQLiteKV(":memory:")
    state_kv = SQLiteKV(":memory:")
    block_db = BlockDB(block_kv)
    state_db = StateDB(state_kv)

    txs_root = compute_txs_root_from_txs(())
    header = Header(
        v=1,
        chainId=1,
        height=0,
        parentHash=_zero_hash(),
        timestamp=1000000,
        stateRoot=b"\x11" * 32,
        txsRoot=txs_root,
        receiptsRoot=_zero_hash(),
        proofsRoot=_zero_hash(),
        daRoot=_zero_hash(),
        mixSeed=_zero_hash(),
        poiesPolicyRoot=_zero_hash(),
        pqAlgPolicyRoot=_zero_hash(),
        thetaMicro=1000000,
        workType=0,
        nonce=0,
        extra=b"",
    )
    block = Block(header=header, txs=(), proofs=(), receipts=None)
    block_hash = header.hash()
    block_db.put_header(header)
    block_db.put_block(block)
    block_db.set_canonical(0, block_hash)
    block_db.set_head(0, block_hash)
    block_db.set_chain_id(1)
    state_db.kv.put(b"\x01account_key", b"account_data")
    return block_db, state_db


def _export_snapshot(snapshot_dir: Path) -> None:
    block_db, state_db = _build_db()
    export_snapshot(
        block_db=block_db,
        state_db=state_db,
        checkpoint_height=0,
        output_dir=snapshot_dir,
        compress=False,
    )


def test_snapshot_import_rejects_corrupt_chunk():
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = Path(tmpdir) / "snapshot"
        snapshot_dir.mkdir()
        _export_snapshot(snapshot_dir)

        manifest_path = snapshot_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        chunk_name = manifest["chunks"][0]["name"]
        chunk_path = snapshot_dir / chunk_name
        chunk_path.write_bytes(chunk_path.read_bytes() + b"corrupt")

        import_block_db = BlockDB(SQLiteKV(":memory:"))
        import_state_db = StateDB(SQLiteKV(":memory:"))

        with pytest.raises(ValueError, match="hash mismatch"):
            import_snapshot(
                block_db=import_block_db,
                state_db=import_state_db,
                snapshot_dir=snapshot_dir,
                verify_hashes=True,
            )


def test_snapshot_import_rejects_bad_checkpoint_hash():
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = Path(tmpdir) / "snapshot"
        snapshot_dir.mkdir()
        _export_snapshot(snapshot_dir)

        manifest_path = snapshot_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["checkpoint_hash"] = "0x" + "00" * 32
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        import_block_db = BlockDB(SQLiteKV(":memory:"))
        import_state_db = StateDB(SQLiteKV(":memory:"))

        with pytest.raises(ValueError, match="Checkpoint hash mismatch"):
            import_snapshot(
                block_db=import_block_db,
                state_db=import_state_db,
                snapshot_dir=snapshot_dir,
                verify_hashes=True,
            )


def test_snapshot_import_rejects_state_root_mismatch():
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = Path(tmpdir) / "snapshot"
        snapshot_dir.mkdir()
        _export_snapshot(snapshot_dir)

        manifest_path = snapshot_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["state_root"] = "0x" + "22" * 32
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        import_block_db = BlockDB(SQLiteKV(":memory:"))
        import_state_db = StateDB(SQLiteKV(":memory:"))

        with pytest.raises(ValueError, match="Checkpoint state root mismatch"):
            import_snapshot(
                block_db=import_block_db,
                state_db=import_state_db,
                snapshot_dir=snapshot_dir,
                verify_hashes=True,
            )
