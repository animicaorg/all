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

from ..encoding.cbor import cbor_dumps, cbor_loads, cbor_loads_prefix
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
SNAPSHOT_VERSION = 2

# Chunk size for splitting large exports (UNCOMPRESSED bytes). This MUST stay below
# the P2P wire cap p2p.wire.encoding.MAX_PAYLOAD_BYTES (8 MiB), otherwise a chunk can
# never be served over GET_SNAPSHOT_CHUNK (the whole file is sent in one SnapshotChunk
# message and encoding rejects it with "payload too large"), which silently breaks
# P2P snapshot fast-sync for every peer. The split rotates BEFORE exceeding this bound
# and the chunk files are gzip-compressed, so 7 MiB guarantees compressed-chunk + wire
# envelope < 8 MiB even for incompressible data. (Was 128 MiB → produced 116 MiB chunks
# that were unservable; see the snapshot-chunk incident.)
DEFAULT_CHUNK_SIZE = 7 * 1024 * 1024


@dataclass
class SnapshotManifest:
    """Metadata for a chain snapshot."""

    version: int
    chain_id: int
    network: Optional[str]
    checkpoint_height: int
    checkpoint_hash: str
    timestamp: int
    created_at: str
    blocks_count: int
    headers_count: int
    accounts_count: int
    storage_keys_count: int
    code_contracts_count: int
    db_engine: Optional[str] = None
    db_version: Optional[str] = None
    total_size: int = 0
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
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
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
    network = os.environ.get("ANIMICA_NETWORK")
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest = SnapshotManifest(
        version=SNAPSHOT_VERSION,
        chain_id=chain_id,
        network=network,
        checkpoint_height=checkpoint_height,
        checkpoint_hash=checkpoint_hash,
        timestamp=int(time.time()),
        created_at=created_at,
        blocks_count=0,
        headers_count=0,
        accounts_count=0,
        storage_keys_count=0,
        code_contracts_count=0,
        compressed=compress,
    )
    manifest.db_engine = type(block_db.kv).__name__ if hasattr(block_db, "kv") else None
    manifest.db_version = getattr(block_db.kv, "version", None)

    def _chunk_name(prefix: str, index: int, *, compressed: bool) -> str:
        suffix = ".cbor.gz" if compressed else ".cbor"
        return f"{prefix}-{index:05d}{suffix}"

    def _open_chunk(prefix: str, index: int):
        name = _chunk_name(prefix, index, compressed=compress)
        path = output_dir / name
        handle = gzip.open(path, "wb") if compress else open(path, "wb")
        return path, handle

    def _finalize_chunk(path: Path) -> dict[str, Any]:
        chunk_hash = _hash_file(path)
        size = path.stat().st_size
        return {"name": path.name, "size": size, "hash": chunk_hash, "sha256": chunk_hash}

    def _append_chunk(chunk: dict[str, Any], chunk_type: str, index: int) -> None:
        manifest.chunks.append(
            {
                **chunk,
                "type": chunk_type,
                "index": index,
            }
        )

    # Export blocks and headers
    _log.info("Exporting blocks and headers...")
    blocks_index = 0
    blocks_written = 0
    blocks_path, blocks_handle = _open_chunk("blocks", blocks_index)

    def _write_blocks_entry(entry_bytes: bytes) -> None:
        nonlocal blocks_written, blocks_index, blocks_path, blocks_handle
        if blocks_written + len(entry_bytes) + 1 > chunk_size:
            blocks_handle.close()
            chunk_info = _finalize_chunk(blocks_path)
            _append_chunk(chunk_info, "blocks", blocks_index)
            blocks_index += 1
            blocks_written = 0
            blocks_path, blocks_handle = _open_chunk("blocks", blocks_index)
        blocks_handle.write(entry_bytes)
        blocks_handle.write(b"\n")
        blocks_written += len(entry_bytes) + 1

    for height in range(0, checkpoint_height + 1):
        block_hash = block_db.get_canonical_hash(height)
        if block_hash:
            header = block_db.get_header_by_hash(block_hash)
            if header:
                entry = {"type": "header", "height": height, "data": header.to_obj()}
                _write_blocks_entry(cbor_dumps(entry))
                manifest.headers_count += 1

            block = block_db.get_block_by_hash(block_hash)
            if block:
                entry = {"type": "block", "height": height, "data": block.to_obj()}
                _write_blocks_entry(cbor_dumps(entry))
                manifest.blocks_count += 1

        if height % 1000 == 0:
            _log.info(f"Exported {height}/{checkpoint_height} blocks")

    blocks_handle.close()
    if blocks_path.exists():
        chunk_info = _finalize_chunk(blocks_path)
        _append_chunk(chunk_info, "blocks", blocks_index)

    # Export state (accounts, code, storage)
    _log.info("Exporting state...")
    state_index = 0
    state_written = 0
    state_path, state_handle = _open_chunk("state", state_index)

    def _write_state_entry(entry_bytes: bytes) -> None:
        nonlocal state_written, state_index, state_path, state_handle
        if state_written + len(entry_bytes) + 1 > chunk_size:
            state_handle.close()
            chunk_info = _finalize_chunk(state_path)
            _append_chunk(chunk_info, "state", state_index)
            state_index += 1
            state_written = 0
            state_path, state_handle = _open_chunk("state", state_index)
        state_handle.write(entry_bytes)
        state_handle.write(b"\n")
        state_written += len(entry_bytes) + 1

    for key, value in state_db.kv.iter_prefix(PFX_ACC):
        entry = {"type": "account", "key": key, "value": value}
        _write_state_entry(cbor_dumps(entry))
        manifest.accounts_count += 1

    for key, value in state_db.kv.iter_prefix(PFX_CODE):
        entry = {"type": "code", "key": key, "value": value}
        _write_state_entry(cbor_dumps(entry))
        manifest.code_contracts_count += 1

    for key, value in state_db.kv.iter_prefix(PFX_STO):
        entry = {"type": "storage", "key": key, "value": value}
        _write_state_entry(cbor_dumps(entry))
        manifest.storage_keys_count += 1

        if manifest.storage_keys_count % 10000 == 0:
            _log.info(f"Exported {manifest.storage_keys_count} storage keys")

    state_handle.close()
    if state_path.exists():
        chunk_info = _finalize_chunk(state_path)
        _append_chunk(chunk_info, "state", state_index)

    manifest.total_size = sum(chunk["size"] for chunk in manifest.chunks)

    # Write manifest
    manifest_file = output_dir / "manifest.json"
    with open(manifest_file, "w") as f:
        json.dump(
            {
                "schema_version": manifest.version,
                "version": manifest.version,
                "chain_id": manifest.chain_id,
                "network": manifest.network,
                "head_height": manifest.checkpoint_height,
                "head_hash": manifest.checkpoint_hash,
                "checkpoint_height": manifest.checkpoint_height,
                "checkpoint_hash": manifest.checkpoint_hash,
                "timestamp": manifest.timestamp,
                "created_at": manifest.created_at,
                "blocks_count": manifest.blocks_count,
                "headers_count": manifest.headers_count,
                "accounts_count": manifest.accounts_count,
                "storage_keys_count": manifest.storage_keys_count,
                "code_contracts_count": manifest.code_contracts_count,
                "db_engine": manifest.db_engine,
                "db_version": manifest.db_version,
                "total_size": manifest.total_size,
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


def _count_non_instant_canonical(block_db: BlockDB, max_height: int) -> int:
    """Compute the canonical_height (META_CANONICAL_HEIGHT) for a chain whose head
    is at ``max_height``: the count of non-instant canonical blocks in heights
    ``[1, max_height]``.

    Genesis (height 0) is the base and is *always* canonical_height 0 (see
    ``block_import`` genesis handling), so it is NOT counted — block_import only
    increments canonical_height for non-instant blocks at height >= 1. The halving
    schedule (``consensus.rewards.compute_block_reward``) uses canonical_height
    precisely to EXCLUDE instant blocks, so this is NOT the head height when the
    chain contains instant blocks. For a chain with no instant blocks this equals
    ``max_height`` exactly.
    """
    from core.chain.block_import import _is_instant_block  # local: avoid cycle

    count = 0
    for h in range(1, int(max_height) + 1):
        bh = block_db.get_canonical_hash(h)
        if bh is None:
            continue
        hdr = block_db.get_header_by_hash(bh)
        if hdr is not None and not _is_instant_block(hdr):
            count += 1
    return count


def import_snapshot(
    block_db: BlockDB,
    state_db: StateDB,
    snapshot_dir: Path,
    verify_hashes: bool = True,
    expected_chain_id: Optional[int] = None,
    expected_network: Optional[str] = None,
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
        network=manifest_data.get("network"),
        checkpoint_height=manifest_data["checkpoint_height"],
        checkpoint_hash=manifest_data["checkpoint_hash"],
        timestamp=manifest_data["timestamp"],
        created_at=manifest_data.get("created_at", ""),
        blocks_count=manifest_data["blocks_count"],
        headers_count=manifest_data["headers_count"],
        accounts_count=manifest_data["accounts_count"],
        storage_keys_count=manifest_data["storage_keys_count"],
        code_contracts_count=manifest_data["code_contracts_count"],
        db_engine=manifest_data.get("db_engine"),
        db_version=manifest_data.get("db_version"),
        total_size=manifest_data.get("total_size", 0),
        state_root=manifest_data.get("state_root"),
        compressed=manifest_data.get("compressed", True),
        chunks=manifest_data["chunks"],
    )

    # Verify version
    if manifest.version not in (1, SNAPSHOT_VERSION):
        raise ValueError(
            f"Unsupported snapshot version {manifest.version}, expected {SNAPSHOT_VERSION}"
        )
    if expected_chain_id is not None and manifest.chain_id != expected_chain_id:
        raise ValueError(
            f"Snapshot chain_id mismatch: expected {expected_chain_id}, got {manifest.chain_id}"
        )
    if expected_network and manifest.network and manifest.network != expected_network:
        raise ValueError(
            f"Snapshot network mismatch: expected {expected_network}, got {manifest.network}"
        )

    # Verify and import chunks
    for chunk_info in manifest.chunks:
        chunk_file = snapshot_dir / chunk_info["name"]
        if not chunk_file.exists():
            raise ValueError(f"Chunk file not found: {chunk_info['name']}")

        # Verify hash if requested
        if verify_hashes:
            actual_hash = _hash_file(chunk_file)
            expected_hash = chunk_info.get("sha256") or chunk_info.get("hash")
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
    block_db.set_head(manifest.checkpoint_height, checkpoint_hash_bytes, allow_reorg=True)

    # Reconcile canonical_height (META_CANONICAL_HEIGHT). Snapshots import ALL
    # blocks including instant ones, and the manifest carries no canonical_height,
    # so recompute it as the count of non-instant canonical blocks. Setting it to
    # checkpoint_height would wrongly include instant blocks and shift the halving
    # schedule. Without this, the head jumps to the checkpoint but the counter
    # keeps its old (often inflated) value and the node thinks it is far behind.
    canonical_height = _count_non_instant_canonical(block_db, manifest.checkpoint_height)
    block_db.set_canonical_height(canonical_height)
    _log.info(
        f"Reconciled canonical_height={canonical_height} "
        f"(checkpoint_height={manifest.checkpoint_height})"
    )

    _log.info(
        f"Snapshot imported successfully: height {manifest.checkpoint_height}, "
        f"hash {manifest.checkpoint_hash}"
    )

    return manifest


# Separator bytes the exporter writes BETWEEN entries. CBOR is a binary format
# and its bytes freely include 0x0a ("\n"), so entries are NOT newline-delimited
# records — they are self-delimiting CBOR items that merely happen to be followed
# by a "\n". We therefore frame by each item's own length (see _iter_chunk_entries)
# and only skip these bytes between items. An entry never *starts* with one of
# these (every entry is a CBOR map, major type 5 => first byte 0xa0..0xbb).
_ENTRY_SEP = (0x0A, 0x0D)


def _iter_chunk_entries(f):
    """Yield decoded CBOR entries from an (already decompressed) chunk file object.

    The exporter writes ``cbor_dumps(entry) || b"\\n"`` per entry. The OLD reader
    used ``for line in f`` / ``line.strip()`` which splits on 0x0a — but 0x0a
    occurs inside almost every account key/value, so those entries were shredded
    and silently dropped (the bug behind "Error importing entry: trailing bytes /
    truncated / unsupported tag …" and partially-populated state). Here we instead
    decode ONE self-delimiting CBOR item at a time by its own length, then skip the
    trailing separator — which parses the very same on-disk chunks correctly, with
    no re-export needed. On a genuinely corrupt item we resync to the next
    separator so one bad entry can't abort the whole chunk.
    """
    data = f.read()
    mv = memoryview(data)
    n = len(data)
    off = 0
    while off < n:
        # Skip separator byte(s) between entries.
        while off < n and data[off] in _ENTRY_SEP:
            off += 1
        if off >= n:
            break
        try:
            entry, consumed = cbor_loads_prefix(mv[off:])
        except Exception as e:
            # Framing desync / corrupt entry: jump to the next entry boundary.
            nxt = data.find(b"\n", off)
            if nxt == -1:
                _log.warning(f"Error importing entry: {e}")
                break
            _log.warning(f"Error importing entry: {e} (resyncing to next boundary)")
            off = nxt + 1
            continue
        if consumed <= 0:  # defensive: never make no progress
            off += 1
            continue
        off += consumed
        yield entry


def _import_blocks_chunk(block_db: BlockDB, chunk_file: Path, compressed: bool):
    """Import blocks and headers from a chunk file."""
    _log.info(f"Importing blocks from {chunk_file.name}")

    open_fn = gzip.open if compressed else open
    imported_count = 0

    with open_fn(chunk_file, "rb") as f:
        for entry in _iter_chunk_entries(f):
            try:
                entry_type = entry.get("type")
                height = entry.get("height")
                data = entry.get("data")

                if entry_type == "header":
                    # Reconstruct header from dict and store
                    from core.types.header import Header
                    header_obj = Header.from_obj(data)
                    block_hash = block_db.put_header(header_obj)
                    # Update height index
                    block_db.set_canonical(height, block_hash)
                elif entry_type == "block":
                    # Reconstruct block from dict and store
                    from core.types.block import Block
                    block_obj = Block.from_obj(data)
                    block_db.put_block(block_obj)

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
        for entry in _iter_chunk_entries(f):
            try:
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

    # Callers (e.g. the orchestrator) may pass a str path; coerce so the `/`
    # path-join below doesn't raise "unsupported operand type(s) for /: 'str' and 'str'".
    snapshot_dir = Path(snapshot_dir)

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
    if version not in (1, SNAPSHOT_VERSION):
        errors.append(
            f"Unsupported snapshot version {version}, expected {SNAPSHOT_VERSION}"
        )

    # Verify chunks exist and match hashes
    chunks = manifest_data.get("chunks", [])
    for chunk_info in chunks:
        chunk_file = snapshot_dir / chunk_info["name"]
        if not chunk_file.exists():
            errors.append(f"Chunk file not found: {chunk_info['name']}")
            continue

        # Verify hash
        actual_hash = _hash_file(chunk_file)
        expected_hash = chunk_info.get("sha256") or chunk_info.get("hash")
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
