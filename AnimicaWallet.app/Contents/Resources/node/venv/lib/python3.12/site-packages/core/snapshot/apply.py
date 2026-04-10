from __future__ import annotations

import contextlib
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from core.db.block_db import BlockDB
from core.db.snapshot import import_snapshot
from core.db.state_db import StateDB


def _open_kv(db_uri: str):
    from core.db.rocksdb import RocksDBKV
    from core.db.sqlite import SQLiteKV

    if db_uri.startswith("sqlite:///"):
        return SQLiteKV(db_uri[len("sqlite:///") :])
    if db_uri.startswith("rocksdb:///"):
        return RocksDBKV(db_uri[len("rocksdb:///") :])
    return SQLiteKV(db_uri)


def _db_path(db_uri: str) -> Path:
    if db_uri.startswith("sqlite:///"):
        return Path(db_uri[len("sqlite:///") :])
    if db_uri.startswith("rocksdb:///"):
        return Path(db_uri[len("rocksdb:///") :])
    return Path(db_uri)


def _close_if_possible(handle: Optional[object]) -> None:
    if handle is None:
        return
    close = getattr(handle, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()


def apply_snapshot_atomic(
    snapshot_dir: Path,
    *,
    db_uri: str,
    expected_chain_id: Optional[int] = None,
    expected_network: Optional[str] = None,
    backup: bool = True,
) -> None:
    db_path = _db_path(db_uri)
    db_parent = db_path.parent
    db_parent.mkdir(parents=True, exist_ok=True)

    suffix = f".snapshot-{int(time.time())}"
    temp_path = db_parent / f"{db_path.name}{suffix}.tmp"

    if db_uri.startswith("rocksdb:///"):
        temp_path.mkdir(parents=True, exist_ok=True)

    kv = _open_kv(
        f"{db_uri.split(':///')[0]}:///{temp_path}"
        if db_uri.startswith(("sqlite:///", "rocksdb:///"))
        else str(temp_path)
    )
    block_db = BlockDB(kv)
    state_db = StateDB(kv)
    try:
        import_snapshot(
            block_db=block_db,
            state_db=state_db,
            snapshot_dir=snapshot_dir,
            verify_hashes=True,
            expected_chain_id=expected_chain_id,
            expected_network=expected_network,
        )
    finally:
        _close_if_possible(kv)

    if backup and db_path.exists():
        backup_path = db_parent / f"{db_path.name}{suffix}.bak"
        if db_uri.startswith("sqlite:///"):
            for suffix_extra in ("", "-wal", "-shm"):
                candidate = Path(f"{db_path}{suffix_extra}")
                if candidate.exists():
                    shutil.move(str(candidate), str(backup_path) + suffix_extra)
        else:
            shutil.move(db_path, backup_path)
    if db_uri.startswith("sqlite:///"):
        os.replace(temp_path, db_path)
    else:
        if db_path.exists():
            shutil.rmtree(db_path)
        shutil.move(temp_path, db_path)
