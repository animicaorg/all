from __future__ import annotations

"""
Chain Snapshot Export/Import for Fast Sync
===========================================

This module provides functionality to create and restore chain snapshots
at checkpoint heights, enabling new nodes to sync faster by downloading
pre-built chain state instead of syncing from genesis.

A snapshot includes:
- All blocks up to checkpoint height
- All headers up to checkpoint height  
- Complete state (accounts, storage, code) at checkpoint
- Metadata (chain_id, checkpoint height/hash, timestamp)

Snapshot format:
- Directory structure with chunked data files
- Manifest JSON with metadata and chunk hashes
- CBOR-encoded state/block data for deterministic encoding
"""

import gzip
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..encoding.cbor import cbor_dumps, cbor_loads
from ..utils.hash import sha3_256
from .block_db import (
    BlockDB,
    PFX_BLK,
    PFX_HDR,
    PFX_HIX,
    META_CHAIN_ID,
    META_HEAD_HASH,
    META_HEAD_HEIGHT,
    _from_u64be,
)
from .state_db import StateDB, PFX_ACC, PFX_CODE, PFX_STO

_log = logging.getLogger("animica.snapshot")

# Snapshot format version
SNAPSHOT_VERSION = 1

# Chunk size for splitting large exports (in bytes, ~100MB chunks)
DEFAULT_CHUNK_SIZE = 100 * 1024 * 1024


@dataclass
class SnapshotManifest:
    """Metadata for a chain snapshot."""

    version: int
    chain_id: int
    checkpoint_height: int
    checkpoint_hash: str
    timestamp: int
    blocks_count: int
    headers_count: int
    accounts_count: int
    storage_keys_count: int
    code_contracts_count: int
    state_root: Optional[str] = None
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    compressed: bool = True


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def _unhex(s: str) -> bytes:
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


def _hash_file(path: Path) -> str:
    """Compute SHA3-256 hash of a file."""
    h = hashlib.sha3_256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return "0x" + h.hexdigest()


def export_snapshot(
    block_db: BlockDB,
    state_db: StateDB,
    checkpoint_height: int,
    output_dir: Path,
    compress: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> SnapshotManifest:
    """
    Export a chain snapshot at the specified checkpoint height.

    Args:
        block_db: Block database instance
        state_db: State database instance
        checkpoint_height: Height to create snapshot at
        output_dir: Directory to write snapshot files
        compress: Whether to gzip compress chunks
        chunk_size: Size of each chunk in bytes

    Returns:
        SnapshotManifest with metadata and chunk info

    Raises:
        ValueError: If checkpoint height is invalid or data missing
    """
    _log.info(f"Creating snapshot at height {checkpoint_height} in {output_dir}")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get checkpoint block hash
    checkpoint_hash_bytes = block_db.get_canonical_hash(checkpoint_height)
    if not checkpoint_hash_bytes:
        raise ValueError(f"No block found at height {checkpoint_height}")

    checkpoint_hash = _hex(checkpoint_hash_bytes)

    # Get chain ID
    chain_id = block_db.get_chain_id()
    if chain_id is None:
        chain_id = 0

    # Initialize manifest
    manifest = SnapshotManifest(
        version=SNAPSHOT_VERSION,
        chain_id=chain_id,
        checkpoint_height=checkpoint_height,
        checkpoint_hash=checkpoint_hash,
        timestamp=int(time.time()),
        blocks_count=0,
        headers_count=0,
        accounts_count=0,
        storage_keys_count=0,
        code_contracts_count=0,
        compressed=compress,
    )

    # Export blocks and headers
    _log.info("Exporting blocks and headers...")
    blocks_file = output_dir / "blocks.cbor"
    if compress:
        blocks_file = output_dir / "blocks.cbor.gz"

    with gzip.open(blocks_file, "wb") if compress else open(blocks_file, "wb") as f:
        for height in range(0, checkpoint_height + 1):
            # Export header at this height
            block_hash = block_db.get_canonical_hash(height)
            if block_hash:
                header = block_db.get_header_by_hash(block_hash)
                if header:
                    # Write height-prefixed entry
                    # Format: [entry_cbor_bytes]\n for easy delimiting
                    entry = {"type": "header", "height": height, "data": header}
                    entry_bytes = cbor_dumps(entry)
                    f.write(entry_bytes)
                    f.write(b"\n")  # Delimiter for easier parsing
                    manifest.headers_count += 1

                # Export block at this height
                block = block_db.get_block_by_hash(block_hash)
                if block:
                    entry = {"type": "block", "height": height, "data": block}
                    entry_bytes = cbor_dumps(entry)
                    f.write(entry_bytes)
                    f.write(b"\n")  # Delimiter
                    manifest.blocks_count += 1

            if height % 1000 == 0:
                _log.info(f"Exported {height}/{checkpoint_height} blocks")

    blocks_hash = _hash_file(blocks_file)
    manifest.chunks.append(
        {
            "name": blocks_file.name,
            "type": "blocks",
            "size": blocks_file.stat().st_size,
            "hash": blocks_hash,
        }
    )

    # Export state (accounts, code, storage)
    _log.info("Exporting state...")
    state_file = output_dir / "state.cbor"
    if compress:
        state_file = output_dir / "state.cbor.gz"

    with gzip.open(state_file, "wb") if compress else open(state_file, "wb") as f:
        # Export accounts
        for key, value in state_db.kv.iter_prefix(PFX_ACC):
            entry = {"type": "account", "key": key, "value": value}
            entry_bytes = cbor_dumps(entry)
            f.write(entry_bytes)
            f.write(b"\n")  # Delimiter
            manifest.accounts_count += 1

        # Export code
        for key, value in state_db.kv.iter_prefix(PFX_CODE):
            entry = {"type": "code", "key": key, "value": value}
            entry_bytes = cbor_dumps(entry)
            f.write(entry_bytes)
            f.write(b"\n")  # Delimiter
            manifest.code_contracts_count += 1

        # Export storage
        for key, value in state_db.kv.iter_prefix(PFX_STO):
            entry = {"type": "storage", "key": key, "value": value}
            entry_bytes = cbor_dumps(entry)
            f.write(entry_bytes)
            f.write(b"\n")  # Delimiter
            manifest.storage_keys_count += 1

            if manifest.storage_keys_count % 10000 == 0:
                _log.info(f"Exported {manifest.storage_keys_count} storage keys")

    state_hash = _hash_file(state_file)
    manifest.chunks.append(
        {
            "name": state_file.name,
            "type": "state",
            "size": state_file.stat().st_size,
            "hash": state_hash,
        }
    )

    # Write manifest
    manifest_file = output_dir / "manifest.json"
    with open(manifest_file, "w") as f:
        json.dump(
            {
                "version": manifest.version,
                "chain_id": manifest.chain_id,
                "checkpoint_height": manifest.checkpoint_height,
                "checkpoint_hash": manifest.checkpoint_hash,
                "timestamp": manifest.timestamp,
                "blocks_count": manifest.blocks_count,
                "headers_count": manifest.headers_count,
                "accounts_count": manifest.accounts_count,
                "storage_keys_count": manifest.storage_keys_count,
                "code_contracts_count": manifest.code_contracts_count,
                "state_root": manifest.state_root,
                "compressed": manifest.compressed,
                "chunks": manifest.chunks,
            },
            f,
            indent=2,
        )

    _log.info(
        f"Snapshot created: {manifest.blocks_count} blocks, "
        f"{manifest.accounts_count} accounts, "
        f"{manifest.storage_keys_count} storage keys"
    )

    return manifest


def import_snapshot(
    block_db: BlockDB,
    state_db: StateDB,
    snapshot_dir: Path,
    verify_hashes: bool = True,
) -> SnapshotManifest:
    """
    Import a chain snapshot into the databases.

    Args:
        block_db: Block database instance
        state_db: State database instance
        snapshot_dir: Directory containing snapshot files
        verify_hashes: Whether to verify chunk hashes

    Returns:
        SnapshotManifest that was imported

    Raises:
        ValueError: If snapshot is invalid or corrupted
    """
    _log.info(f"Importing snapshot from {snapshot_dir}")

    # Load manifest
    manifest_file = snapshot_dir / "manifest.json"
    if not manifest_file.exists():
        raise ValueError("Snapshot manifest not found")

    with open(manifest_file) as f:
        manifest_data = json.load(f)

    manifest = SnapshotManifest(
        version=manifest_data["version"],
        chain_id=manifest_data["chain_id"],
        checkpoint_height=manifest_data["checkpoint_height"],
        checkpoint_hash=manifest_data["checkpoint_hash"],
        timestamp=manifest_data["timestamp"],
        blocks_count=manifest_data["blocks_count"],
        headers_count=manifest_data["headers_count"],
        accounts_count=manifest_data["accounts_count"],
        storage_keys_count=manifest_data["storage_keys_count"],
        code_contracts_count=manifest_data["code_contracts_count"],
        state_root=manifest_data.get("state_root"),
        compressed=manifest_data.get("compressed", True),
        chunks=manifest_data["chunks"],
    )

    # Verify version
    if manifest.version != SNAPSHOT_VERSION:
        raise ValueError(
            f"Unsupported snapshot version {manifest.version}, expected {SNAPSHOT_VERSION}"
        )

    # Verify and import chunks
    for chunk_info in manifest.chunks:
        chunk_file = snapshot_dir / chunk_info["name"]
        if not chunk_file.exists():
            raise ValueError(f"Chunk file not found: {chunk_info['name']}")

        # Verify hash if requested
        if verify_hashes:
            actual_hash = _hash_file(chunk_file)
            expected_hash = chunk_info["hash"]
            if actual_hash != expected_hash:
                raise ValueError(
                    f"Chunk {chunk_info['name']} hash mismatch: "
                    f"expected {expected_hash}, got {actual_hash}"
                )

        # Import chunk based on type
        if chunk_info["type"] == "blocks":
            _import_blocks_chunk(block_db, chunk_file, manifest.compressed)
        elif chunk_info["type"] == "state":
            _import_state_chunk(state_db, chunk_file, manifest.compressed)
        else:
            _log.warning(f"Unknown chunk type: {chunk_info['type']}")

    # Update block DB head to checkpoint
    checkpoint_hash_bytes = _unhex(manifest.checkpoint_hash)
    block_db.set_head(manifest.checkpoint_height, checkpoint_hash_bytes)

    _log.info(
        f"Snapshot imported successfully: height {manifest.checkpoint_height}, "
        f"hash {manifest.checkpoint_hash}"
    )

    return manifest


def _import_blocks_chunk(block_db: BlockDB, chunk_file: Path, compressed: bool):
    """Import blocks and headers from a chunk file."""
    _log.info(f"Importing blocks from {chunk_file.name}")

    open_fn = gzip.open if compressed else open
    imported_count = 0

    with open_fn(chunk_file, "rb") as f:
        # Read line by line (entries are newline-delimited)
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                # Decode CBOR entry
                entry = cbor_loads(line)

                entry_type = entry.get("type")
                height = entry.get("height")
                data = entry.get("data")

                if entry_type == "header":
                    # Store header (data should already be in proper format)
                    block_hash = block_db.put_header(data)
                    # Update height index
                    block_db.set_canonical(height, block_hash)
                elif entry_type == "block":
                    # Store block
                    block_db.put_block(data)

                imported_count += 1
                if imported_count % 1000 == 0:
                    _log.info(f"Imported {imported_count} entries from {chunk_file.name}")

            except Exception as e:
                _log.warning(f"Error importing entry: {e}")
                continue

    _log.info(f"Imported {imported_count} entries from {chunk_file.name}")


def _import_state_chunk(state_db: StateDB, chunk_file: Path, compressed: bool):
    """Import state (accounts, code, storage) from a chunk file."""
    _log.info(f"Importing state from {chunk_file.name}")

    open_fn = gzip.open if compressed else open
    imported_count = 0

    with open_fn(chunk_file, "rb") as f:
        # Read line by line (entries are newline-delimited)
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                # Decode CBOR entry
                entry = cbor_loads(line)

                entry_type = entry.get("type")
                key = entry.get("key")
                value = entry.get("value")

                # Write directly to underlying KV store
                state_db.kv.put(key, value)

                imported_count += 1
                if imported_count % 10000 == 0:
                    _log.info(f"Imported {imported_count} state entries from {chunk_file.name}")

            except Exception as e:
                _log.warning(f"Error importing entry: {e}")
                continue

    _log.info(f"Imported {imported_count} state entries from {chunk_file.name}")


def verify_snapshot(snapshot_dir: Path) -> Tuple[bool, List[str]]:
    """
    Verify a snapshot's integrity without importing it.

    Args:
        snapshot_dir: Directory containing snapshot files

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check manifest exists
    manifest_file = snapshot_dir / "manifest.json"
    if not manifest_file.exists():
        errors.append("Manifest file not found")
        return False, errors

    # Load manifest
    try:
        with open(manifest_file) as f:
            manifest_data = json.load(f)
    except Exception as e:
        errors.append(f"Failed to load manifest: {e}")
        return False, errors

    # Verify version
    version = manifest_data.get("version")
    if version != SNAPSHOT_VERSION:
        errors.append(f"Unsupported snapshot version {version}, expected {SNAPSHOT_VERSION}")

    # Verify chunks exist and match hashes
    chunks = manifest_data.get("chunks", [])
    for chunk_info in chunks:
        chunk_file = snapshot_dir / chunk_info["name"]
        if not chunk_file.exists():
            errors.append(f"Chunk file not found: {chunk_info['name']}")
            continue

        # Verify hash
        actual_hash = _hash_file(chunk_file)
        expected_hash = chunk_info["hash"]
        if actual_hash != expected_hash:
            errors.append(
                f"Chunk {chunk_info['name']} hash mismatch: "
                f"expected {expected_hash}, got {actual_hash}"
            )

    return len(errors) == 0, errors


__all__ = [
    "SnapshotManifest",
    "export_snapshot",
    "import_snapshot",
    "verify_snapshot",
    "SNAPSHOT_VERSION",
    "DEFAULT_CHUNK_SIZE",
]
