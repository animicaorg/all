from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from .canonical import canonical_bytes
from .merkle import merkle_root
from .schemas import ChunkInfo, EpochPackManifest
from .storage import build_chunk_manifest, compute_commitment_root


INDEX_STRUCT = struct.Struct(">QQQ32s")  # height, offset, length, hash


@dataclass(frozen=True)
class PackEntry:
    height: int
    offset: int
    length: int
    item_hash: bytes


def build_index(entries: Sequence[PackEntry]) -> bytes:
    return b"".join(
        INDEX_STRUCT.pack(entry.height, entry.offset, entry.length, entry.item_hash)
        for entry in entries
    )


def parse_index(data: bytes) -> List[PackEntry]:
    entries: List[PackEntry] = []
    for offset in range(0, len(data), INDEX_STRUCT.size):
        height, item_offset, length, item_hash = INDEX_STRUCT.unpack_from(data, offset)
        entries.append(PackEntry(height=height, offset=item_offset, length=length, item_hash=item_hash))
    return entries


def build_epoch_pack(
    *,
    chain_id: int,
    kind: str,
    epoch_id: int,
    start_height: int,
    end_height: int,
    start_hash: str,
    end_hash: str,
    total_work: int,
    items: Sequence[object],
    item_hashes: Sequence[bytes],
    output_path: Path,
    chunk_size: int = 1024 * 1024,
    producer_node_id: str = "local",
) -> EpochPackManifest:
    payload = bytearray()
    entries: List[PackEntry] = []
    for height, item, item_hash in zip(range(start_height, end_height + 1), items, item_hashes):
        payload_bytes = canonical_bytes(item)
        offset = len(payload)
        payload.extend(payload_bytes)
        entries.append(PackEntry(height=height, offset=offset, length=len(payload_bytes), item_hash=item_hash))

    index_bytes = build_index(entries)
    meta = {
        "kind": kind,
        "epoch_id": epoch_id,
        "start_height": start_height,
        "end_height": end_height,
        "start_hash": start_hash,
        "end_hash": end_hash,
        "total_work": total_work,
    }
    meta_bytes = json.dumps(meta, sort_keys=True).encode()

    with output_path.open("wb") as handle:
        handle.write(struct.pack(">I", len(meta_bytes)))
        handle.write(meta_bytes)
        handle.write(struct.pack(">Q", len(index_bytes)))
        handle.write(index_bytes)
        handle.write(struct.pack(">Q", len(payload)))
        handle.write(payload)

    file_size, chunks = build_chunk_manifest(output_path, chunk_size)
    commitment_root = compute_commitment_root(chunks)
    payload_root = merkle_root(item_hashes).hex()
    pcp_root = payload_root

    manifest = EpochPackManifest(
        chain_id=chain_id,
        kind=kind,
        epoch_id=epoch_id,
        start_height=start_height,
        end_height=end_height,
        start_hash=start_hash,
        end_hash=end_hash,
        total_work=total_work,
        file_size=file_size,
        chunk_size=chunk_size,
        chunks=chunks,
        whole_file_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
        commitment_root=commitment_root,
        payload_root=payload_root,
        pcp_root=pcp_root,
        producer_node_id=producer_node_id,
        signature="",
    )
    return manifest


def read_pack_sections(path: Path) -> Tuple[dict, bytes, bytes]:
    with path.open("rb") as handle:
        meta_len = struct.unpack(">I", handle.read(4))[0]
        meta_bytes = handle.read(meta_len)
        index_len = struct.unpack(">Q", handle.read(8))[0]
        index_bytes = handle.read(index_len)
        payload_len = struct.unpack(">Q", handle.read(8))[0]
        payload_bytes = handle.read(payload_len)
    return json.loads(meta_bytes.decode()), index_bytes, payload_bytes
