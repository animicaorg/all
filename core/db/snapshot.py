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


# ---------------------------------------------------------------------------
# Snapshot import hardening (node-local; ANM-C11 / H02 / H05 / H06 / M03)
# ---------------------------------------------------------------------------
# These controls make ``import_snapshot`` fail-closed against a partial / bloated /
# untrusted snapshot WITHOUT changing behaviour for a well-formed legit snapshot:
#   * resource caps (chunk count, on-disk chunk size, decompressed size) prevent
#     a hostile manifest/chunk from OOM-ing or wedging the node;
#   * chunk-name path-safety prevents a manifest from escaping the snapshot dir;
#   * a strong manifest digest (merkle-style hash over the ordered chunk set) can
#     be PINNED (and optionally PQ-signature-verified) via env — off by default so
#     the current unsigned mainnet snapshot still imports, loud-warns when absent;
#   * a STRICT completeness gate refuses to advance the head unless every declared
#     block/header/account/code/storage entry actually imported (the direct fix for
#     the silent state-divergence / too-low-balance incident).
# Defaults are generous: a real snapshot chunks state at DEFAULT_CHUNK_SIZE (7 MiB)
# so even a multi-TB chain stays far below these bounds.

# Max number of chunks a manifest may declare.
MAX_MANIFEST_CHUNKS = 200_000
# Max on-disk (compressed) size of a single chunk file, checked before it is read.
MAX_CHUNK_FILE_BYTES = 64 * 1024 * 1024
# Max decompressed bytes read from a single chunk (gzip-bomb guard).
MAX_CHUNK_DECOMPRESSED_BYTES = 128 * 1024 * 1024

# Env knobs (all optional; safe defaults preserve current external behaviour):
#   ANIMICA_SNAPSHOT_MAX_CHUNKS                    -> MAX_MANIFEST_CHUNKS
#   ANIMICA_SNAPSHOT_MAX_CHUNK_BYTES              -> MAX_CHUNK_FILE_BYTES
#   ANIMICA_SNAPSHOT_MAX_CHUNK_DECOMPRESSED_BYTES -> MAX_CHUNK_DECOMPRESSED_BYTES
#   ANIMICA_SNAPSHOT_MANIFEST_DIGEST              -> pinned expected manifest digest
#   ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS              -> CSV of hex PQ pubkeys (signature)
#   ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE            -> require a valid signature
#   ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE             -> downgrade completeness abort to a
#                                                    LOUD warning (emergency recovery)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = int(str(raw).strip())
    except ValueError:
        _log.warning("Invalid %s=%r (not an int); using default %d", name, raw, default)
        return default
    if val <= 0:
        _log.warning("Non-positive %s=%r; using default %d", name, raw, default)
        return default
    return val


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_env(name: str) -> List[str]:
    raw = os.environ.get(name) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _max_manifest_chunks() -> int:
    return _env_int("ANIMICA_SNAPSHOT_MAX_CHUNKS", MAX_MANIFEST_CHUNKS)


def _max_chunk_file_bytes() -> int:
    return _env_int("ANIMICA_SNAPSHOT_MAX_CHUNK_BYTES", MAX_CHUNK_FILE_BYTES)


def _max_chunk_decompressed_bytes() -> int:
    return _env_int(
        "ANIMICA_SNAPSHOT_MAX_CHUNK_DECOMPRESSED_BYTES", MAX_CHUNK_DECOMPRESSED_BYTES
    )


def _safe_chunk_name(name: Any) -> str:
    """Return ``name`` iff it is a plain filename inside the snapshot dir.

    Manifests are attacker-influenced (they arrive over P2P / RPC / disk). A name
    like ``../../etc/passwd`` or an absolute path would make ``snapshot_dir / name``
    resolve OUTSIDE the snapshot — an arbitrary-file read (downloadChunk) or an
    import that ingests unrelated files. Only accept a single path component with
    no separators, no ``..``, and no NUL.
    """
    raw = str(name)
    if (
        not raw
        or raw in (".", "..")
        or "/" in raw
        or "\\" in raw
        or "\x00" in raw
        or raw != os.path.basename(raw)
    ):
        raise ValueError(f"Unsafe snapshot chunk name (possible path traversal): {name!r}")
    return raw


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


def _raise_or_warn_incomplete(kind: str, problems: List[str]) -> None:
    """Fail-closed on an incomplete restore (or loud-warn if the operator opted out).

    A truncated/dropped restore that still advances the head is the exact mechanism
    behind the silent state-divergence incident (accounts missing from the trie read
    too-low forever because this chain commits no state root in headers). Default is
    to abort. ``ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE=1`` is an explicit, LOUD escape
    hatch for emergency operator recovery only.
    """
    if not problems:
        return
    msg = (
        f"Snapshot {kind} restore is INCOMPLETE — refusing to advance the head onto "
        f"partial data (missing entries read too-low / absent forever, undetected). "
        + "; ".join(problems)
        + ". Re-export/redownload a complete snapshot and retry."
    )
    if _env_flag("ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE"):
        _log.error(
            "ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE is set — importing a PROVABLY INCOMPLETE "
            "snapshot ANYWAY; this node may serve too-low balances / diverged state. %s",
            msg,
        )
        return
    raise ValueError(msg)


def _verify_state_import_complete(manifest, imported: dict) -> None:
    """Abort the import if the restored state is not provably complete.

    Compares the per-type counts actually written to state against the counts the
    manifest recorded at export time. Any dropped entry, or any per-type shortfall,
    means the trie is missing accounts/code/storage — restoring onto that and
    advancing the head is exactly what produces silent too-low balances. We only
    assert a count the manifest actually carries (>0), so pre-count legacy
    snapshots still import (they simply get no extra guarantee).
    """
    problems: List[str] = []
    if int(imported.get("dropped", 0) or 0) > 0:
        problems.append(f"{imported['dropped']} state entries failed to import")
    checks = (
        ("account", "accounts_count"),
        ("code", "code_contracts_count"),
        ("storage", "storage_keys_count"),
    )
    for got_key, manifest_attr in checks:
        expected = int(getattr(manifest, manifest_attr, 0) or 0)
        got = int(imported.get(got_key, 0) or 0)
        if expected and got != expected:
            problems.append(f"{got_key}: imported {got} != manifest {expected}")
    _raise_or_warn_incomplete("state", problems)


def _verify_blocks_import_complete(manifest, imported: dict) -> None:
    """Abort the import if the restored blocks/headers are not provably complete.

    Symmetric with :func:`_verify_state_import_complete` but for the block chunks.
    Previously ``_import_blocks_chunk`` swallowed per-entry errors and a truncated
    blocks chunk could yield FEWER entries silently, yet the head was still set to
    the checkpoint — leaving the node claiming a height whose blocks it does not
    actually hold. We now count drops + compare header/block counts to the manifest.
    """
    problems: List[str] = []
    if int(imported.get("dropped", 0) or 0) > 0:
        problems.append(f"{imported['dropped']} block/header entries failed to import")
    for got_key, manifest_attr in (("header", "headers_count"), ("block", "blocks_count")):
        expected = int(getattr(manifest, manifest_attr, 0) or 0)
        got = int(imported.get(got_key, 0) or 0)
        if expected and got != expected:
            problems.append(f"{got_key}: imported {got} != manifest {expected}")
    _raise_or_warn_incomplete("blocks", problems)


def _decode_bytes_field(value: Any) -> bytes:
    """Decode a hex (optionally 0x-prefixed) or base64 byte field."""
    s = str(value).strip()
    if s.startswith("0x") or s.startswith("0X"):
        return bytes.fromhex(s[2:])
    try:
        return bytes.fromhex(s)
    except ValueError:
        import base64

        return base64.b64decode(s)


def _compute_manifest_digest(manifest: "SnapshotManifest") -> str:
    """Strong content digest binding the ORDERED chunk set + head identity + counts.

    This is the value a pin / signature commits to: adding, removing, reordering, or
    mutating any chunk's declared name/type/size/hash — OR altering any declared
    count (which the completeness gate relies on) — changes the digest. Domain-tagged
    and field-length-delimited so distinct manifests cannot collide.
    """
    h = hashlib.sha256()

    def upd(part: Any) -> None:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x1f")

    upd("animica-snapshot-manifest-v1")
    upd(manifest.version)
    upd(manifest.chain_id)
    upd(manifest.network or "")
    upd(manifest.checkpoint_height)
    upd(manifest.checkpoint_hash or "")
    upd(manifest.blocks_count)
    upd(manifest.headers_count)
    upd(manifest.accounts_count)
    upd(manifest.storage_keys_count)
    upd(manifest.code_contracts_count)
    upd(len(manifest.chunks))
    for chunk in manifest.chunks:
        upd(chunk.get("index"))
        upd(chunk.get("type"))
        upd(chunk.get("name"))
        upd(chunk.get("size"))
        upd(chunk.get("sha256") or chunk.get("hash") or "")
    return "0x" + h.hexdigest()


def _verify_manifest_signature(
    manifest: "SnapshotManifest",
    manifest_data: dict,
    digest: str,
    trusted: List[str],
    require_sig: bool,
) -> None:
    """Verify a PQ signature over the manifest digest against trusted pubkeys.

    Only invoked when the operator configures ``ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS`` or
    ``ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE`` — off by default so the current unsigned
    snapshot still imports. When enabled it is fail-closed: a missing/undecodable/
    mismatched signature aborts the import.
    """
    if require_sig and not trusted:
        raise ValueError(
            "ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE is set but no "
            "ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS are configured — refusing import"
        )
    sig_obj = manifest_data.get("signature")
    if not isinstance(sig_obj, dict):
        raise ValueError("Snapshot manifest signature required but missing")
    alg_id = sig_obj.get("alg_id")
    if alg_id is None:
        alg_id = sig_obj.get("alg")
    sig_raw = sig_obj.get("sig") or sig_obj.get("signature")
    if alg_id is None or not sig_raw:
        raise ValueError("Snapshot manifest signature missing alg_id or sig")
    try:
        alg_id_int = int(alg_id)
    except (TypeError, ValueError):
        raise ValueError(f"Snapshot manifest signature has non-integer alg_id: {alg_id!r}")
    try:
        sig_bytes = _decode_bytes_field(sig_raw)
    except Exception as exc:
        raise ValueError(f"Snapshot manifest signature is not decodable: {exc}")

    try:
        from pq.py.verify import verify as pq_verify
    except Exception as exc:
        raise ValueError(
            f"Snapshot signature verification requested but PQ backend unavailable: {exc}"
        )

    message = digest.encode("utf-8")
    for pk_hex in trusted:
        try:
            pubkey = _decode_bytes_field(pk_hex)
        except Exception:
            continue
        try:
            if pq_verify(alg_id_int, pubkey, sig_bytes, message):
                _log.info(
                    "Snapshot manifest signature verified against a trusted pubkey "
                    "(alg_id=%s, digest=%s)",
                    alg_id_int,
                    digest,
                )
                return
        except Exception as exc:  # noqa: BLE001 - never trust the verifier to not raise
            _log.debug("Snapshot signature verify error: %s", exc)
            continue
    raise ValueError("Snapshot manifest signature did not match any trusted pubkey")


def _verify_manifest_integrity(manifest: "SnapshotManifest", manifest_data: dict) -> str:
    """Verify manifest integrity (digest pin + optional PQ signature). Returns digest.

    Both controls are OFF by default (loud-warns when no pin is configured) so the
    current unsigned mainnet snapshot still imports; per-chunk sha256 hashes and the
    structural completeness gate are ALWAYS enforced regardless.
    """
    digest = _compute_manifest_digest(manifest)

    pinned = (os.environ.get("ANIMICA_SNAPSHOT_MANIFEST_DIGEST") or "").strip().lower()
    if pinned:
        want = pinned if pinned.startswith("0x") else ("0x" + pinned)
        if digest.lower() != want:
            raise ValueError(
                f"Snapshot manifest digest mismatch: expected {want}, got {digest} "
                "— refusing to import an untrusted/altered manifest"
            )
        _log.info("Snapshot manifest digest matches pinned value %s", digest)
    else:
        _log.warning(
            "No ANIMICA_SNAPSHOT_MANIFEST_DIGEST pin configured — importing snapshot "
            "manifest WITHOUT an integrity pin (per-chunk hashes + structural "
            "completeness still enforced). Set a pin for defence-in-depth."
        )

    trusted = _parse_csv_env("ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS")
    require_sig = _env_flag("ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE")
    if trusted or require_sig:
        _verify_manifest_signature(manifest, manifest_data, digest, trusted, require_sig)

    return digest


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

    # RESOURCE CAPS (ANM-H06) — bound the manifest before touching any chunk so a
    # hostile/corrupt manifest that declares an absurd number of chunks can't wedge
    # or OOM the node.
    max_chunks = _max_manifest_chunks()
    n_chunks = len(manifest.chunks)
    if n_chunks > max_chunks:
        raise ValueError(
            f"Snapshot manifest declares {n_chunks} chunks which exceeds the cap "
            f"{max_chunks} (ANIMICA_SNAPSHOT_MAX_CHUNKS) — refusing import"
        )

    # MANIFEST INTEGRITY (ANM-C11) — verify a pinned digest and/or PQ signature when
    # configured. Off by default (loud-warns) so the current unsigned snapshot still
    # imports; per-chunk hashes + completeness below are always enforced.
    _verify_manifest_integrity(manifest, manifest_data)

    max_chunk_file = _max_chunk_file_bytes()
    max_decompressed = _max_chunk_decompressed_bytes()

    # Verify and import chunks
    imported_state = {"account": 0, "code": 0, "storage": 0, "other": 0, "dropped": 0}
    imported_blocks = {"header": 0, "block": 0, "other": 0, "dropped": 0}
    for chunk_info in manifest.chunks:
        # Path-safety (ANM-M03): reject a manifest chunk name that escapes the dir.
        safe_name = _safe_chunk_name(chunk_info["name"])
        chunk_file = snapshot_dir / safe_name
        if not chunk_file.exists():
            raise ValueError(f"Chunk file not found: {safe_name}")

        # On-disk size cap (ANM-H02) — checked before the file is read.
        file_size = chunk_file.stat().st_size
        if file_size > max_chunk_file:
            raise ValueError(
                f"Chunk {safe_name} on-disk size {file_size} exceeds cap "
                f"{max_chunk_file} (ANIMICA_SNAPSHOT_MAX_CHUNK_BYTES) — refusing import"
            )

        # Verify hash if requested
        if verify_hashes:
            actual_hash = _hash_file(chunk_file)
            expected_hash = chunk_info.get("sha256") or chunk_info.get("hash")
            if actual_hash != expected_hash:
                raise ValueError(
                    f"Chunk {safe_name} hash mismatch: "
                    f"expected {expected_hash}, got {actual_hash}"
                )

        # Import chunk based on type
        if chunk_info["type"] == "blocks":
            chunk_counts = _import_blocks_chunk(
                block_db, chunk_file, manifest.compressed, max_decompressed
            )
            for k in imported_blocks:
                imported_blocks[k] += int(chunk_counts.get(k, 0))
        elif chunk_info["type"] == "state":
            chunk_counts = _import_state_chunk(
                state_db, chunk_file, manifest.compressed, max_decompressed
            )
            for k in imported_state:
                imported_state[k] += int(chunk_counts.get(k, 0))
        else:
            _log.warning(f"Unknown chunk type: {chunk_info['type']}")

    # COMPLETENESS GATE — never advance the head onto a partial blocks restore.
    _verify_blocks_import_complete(manifest, imported_blocks)

    # COMPLETENESS GATE — never advance the head onto a partial state restore.
    # A truncated/corrupt state chunk (dropped entries, or fewer entries yielded
    # after a truncation) would leave accounts missing from the trie while the
    # head still points at the checkpoint; forward block application then layers
    # valid deltas onto the incomplete base and those accounts read too-low
    # forever, undetected (this chain commits no state root in its headers, so
    # nothing else catches it). Refuse to set the head unless the imported
    # per-type counts match the manifest exactly and nothing was dropped.
    _verify_state_import_complete(manifest, imported_state)

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


def _read_all_bounded(f, max_bytes: Optional[int]) -> bytes:
    """Read a decompressed stream but abort past ``max_bytes`` (gzip-bomb guard).

    ``gzip.open(...).read(n)`` only inflates ``n`` bytes, so reading ``max_bytes+1``
    caps memory: a chunk that decompresses beyond the bound never gets fully
    inflated into RAM. ``max_bytes<=0``/``None`` disables the cap (callers pass a
    positive cap in the import path).
    """
    if max_bytes is None or max_bytes <= 0:
        return f.read()
    data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(
            f"Snapshot chunk exceeds the decompressed-size cap ({max_bytes} bytes; "
            "ANIMICA_SNAPSHOT_MAX_CHUNK_DECOMPRESSED_BYTES) — possible decompression "
            "bomb; aborting import"
        )
    return data


def _iter_chunk_entries(f, max_bytes: Optional[int] = None):
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

    ``max_bytes`` bounds how much decompressed data is read into memory at once,
    guarding against a gzip bomb (see :func:`_read_all_bounded`).
    """
    data = _read_all_bounded(f, max_bytes)
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


def _import_blocks_chunk(
    block_db: BlockDB,
    chunk_file: Path,
    compressed: bool,
    max_decompressed: Optional[int] = None,
) -> dict:
    """Import blocks and headers from a chunk file.

    Returns per-type counts ``{"header","block","other","dropped"}`` so
    :func:`import_snapshot` can prove the blocks restore is COMPLETE before advancing
    the head. Previously a per-entry error here was swallowed with a bare ``continue``
    and the head still advanced to the checkpoint even though blocks were missing;
    now the caller aborts on any drop or count shortfall.
    """
    _log.info(f"Importing blocks from {chunk_file.name}")

    open_fn = gzip.open if compressed else open
    counts = {"header": 0, "block": 0, "other": 0, "dropped": 0}
    imported_count = 0

    with open_fn(chunk_file, "rb") as f:
        for entry in _iter_chunk_entries(f, max_decompressed):
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
                    counts["header"] += 1
                elif entry_type == "block":
                    # Reconstruct block from dict and store
                    from core.types.block import Block
                    block_obj = Block.from_obj(data)
                    block_db.put_block(block_obj)
                    counts["block"] += 1
                else:
                    counts["other"] += 1

                imported_count += 1
                if imported_count % 1000 == 0:
                    _log.info(f"Imported {imported_count} entries from {chunk_file.name}")

            except Exception as e:
                _log.warning(f"Error importing entry: {e}")
                counts["dropped"] += 1
                continue

    _log.info(
        f"Imported {imported_count} entries from {chunk_file.name} "
        f"(headers={counts['header']} blocks={counts['block']} dropped={counts['dropped']})"
    )
    return counts


def _import_state_chunk(
    state_db: StateDB,
    chunk_file: Path,
    compressed: bool,
    max_decompressed: Optional[int] = None,
) -> dict:
    """Import state (accounts, code, storage) from a chunk file.

    Returns per-type counts ``{"account","code","storage","other","dropped"}`` so
    :func:`import_snapshot` can prove the restore is COMPLETE before advancing the
    head. Previously a per-entry error here just logged a warning and continued,
    leaving the account missing from state while the head still advanced to the
    checkpoint — forward block application then layered valid deltas onto the
    incomplete base and the dropped accounts read too-low forever (the
    balances-too-low divergence class). We now count drops and per-type totals and
    let the caller abort on any shortfall. (Note: a truncated chunk can also cause
    :func:`_iter_chunk_entries` to yield FEWER entries without raising here; that
    is caught by the caller's per-type count vs. manifest comparison.)
    """
    _log.info(f"Importing state from {chunk_file.name}")

    open_fn = gzip.open if compressed else open
    counts = {"account": 0, "code": 0, "storage": 0, "other": 0, "dropped": 0}

    with open_fn(chunk_file, "rb") as f:
        for entry in _iter_chunk_entries(f, max_decompressed):
            try:
                entry_type = entry.get("type")
                key = entry.get("key")
                value = entry.get("value")

                # Write directly to underlying KV store
                state_db.kv.put(key, value)

                counts[entry_type if entry_type in counts else "other"] += 1
                total = counts["account"] + counts["code"] + counts["storage"] + counts["other"]
                if total % 10000 == 0:
                    _log.info(f"Imported {total} state entries from {chunk_file.name}")

            except Exception as e:
                _log.warning(f"Error importing entry: {e}")
                counts["dropped"] += 1
                continue

    total = counts["account"] + counts["code"] + counts["storage"] + counts["other"]
    _log.info(
        f"Imported {total} state entries from {chunk_file.name} "
        f"(accounts={counts['account']} code={counts['code']} "
        f"storage={counts['storage']} dropped={counts['dropped']})"
    )
    return counts


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
    max_chunks = _max_manifest_chunks()
    if len(chunks) > max_chunks:
        errors.append(
            f"Manifest declares {len(chunks)} chunks which exceeds the cap {max_chunks}"
        )
    for chunk_info in chunks:
        # Path-safety: a traversal name must never be joined/opened.
        try:
            safe_name = _safe_chunk_name(chunk_info["name"])
        except ValueError as exc:
            errors.append(str(exc))
            continue
        chunk_file = snapshot_dir / safe_name
        if not chunk_file.exists():
            errors.append(f"Chunk file not found: {safe_name}")
            continue

        # Verify hash
        actual_hash = _hash_file(chunk_file)
        expected_hash = chunk_info.get("sha256") or chunk_info.get("hash")
        if actual_hash != expected_hash:
            errors.append(
                f"Chunk {safe_name} hash mismatch: "
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
    "MAX_MANIFEST_CHUNKS",
    "MAX_CHUNK_FILE_BYTES",
    "MAX_CHUNK_DECOMPRESSED_BYTES",
    "_safe_chunk_name",
    "_compute_manifest_digest",
]
