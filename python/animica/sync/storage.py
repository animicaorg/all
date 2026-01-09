from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .merkle import merkle_root, sha256
from .schemas import ChunkInfo, EpochPackManifest, SnapshotManifest


@dataclass
class ChunkedFile:
    path: Path
    chunk_size: int

    def iter_chunks(self) -> Iterable[Tuple[int, bytes]]:
        with self.path.open("rb") as handle:
            index = 0
            while True:
                data = handle.read(self.chunk_size)
                if not data:
                    break
                yield index, data
                index += 1


class SnapshotStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir = self.base_dir / "manifests"
        self.manifests_dir.mkdir(exist_ok=True)
        self.chunks_dir = self.base_dir / "chunks"
        self.chunks_dir.mkdir(exist_ok=True)

    def write_manifest(self, manifest: SnapshotManifest) -> Path:
        path = self.manifests_dir / f"snapshot_{manifest.snapshot_id()}.json"
        path.write_text(manifest.model_dump_json(indent=2))
        return path

    def write_chunk(self, snapshot_id: str, index: int, data: bytes) -> Path:
        chunk_dir = self.chunks_dir / snapshot_id
        chunk_dir.mkdir(exist_ok=True)
        path = chunk_dir / f"{index}.chunk"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path

    def assemble(self, snapshot_id: str, manifest: SnapshotManifest) -> Path:
        output = self.base_dir / f"snapshot_{snapshot_id}.bin"
        tmp = output.with_suffix(".tmp")
        with tmp.open("wb") as out:
            for chunk in manifest.chunks:
                path = self.chunks_dir / snapshot_id / f"{chunk.index}.chunk"
                out.write(path.read_bytes())
        os.replace(tmp, output)
        return output

    def verify_manifest(self, manifest: SnapshotManifest) -> None:
        chunk_hashes = [bytes.fromhex(chunk.sha256) for chunk in manifest.chunks]
        commitment = merkle_root(chunk_hashes).hex()
        if commitment != manifest.commitment_root:
            raise ValueError("snapshot commitment root mismatch")

    def verify_file(self, path: Path, manifest: SnapshotManifest) -> None:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != manifest.whole_file_sha256:
            raise ValueError("snapshot file hash mismatch")

    def restore_atomic(self, snapshot_file: Path, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="snapshot_restore_", dir=target_dir.parent))
        shutil.unpack_archive(str(snapshot_file), str(tmp_dir))
        marker = tmp_dir / "snapshot_restored.json"
        marker.write_text(json.dumps({"restored_from": snapshot_file.name}, indent=2))
        if target_dir.exists():
            backup_dir = target_dir.with_suffix(".bak")
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            os.replace(target_dir, backup_dir)
        os.replace(tmp_dir, target_dir)
        return target_dir


class EpochPackStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir = self.base_dir / "manifests"
        self.manifests_dir.mkdir(exist_ok=True)
        self.chunks_dir = self.base_dir / "chunks"
        self.chunks_dir.mkdir(exist_ok=True)
        self.progress_path = self.base_dir / "backfill_progress.json"

    def write_manifest(self, manifest: EpochPackManifest) -> Path:
        path = self.manifests_dir / f"epoch_{manifest.pack_id()}.json"
        path.write_text(manifest.model_dump_json(indent=2))
        return path

    def write_chunk(self, pack_id: str, index: int, data: bytes) -> Path:
        chunk_dir = self.chunks_dir / pack_id
        chunk_dir.mkdir(exist_ok=True)
        path = chunk_dir / f"{index}.chunk"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path

    def assemble(self, pack_id: str, manifest: EpochPackManifest) -> Path:
        output = self.base_dir / f"epoch_{pack_id}.epk"
        tmp = output.with_suffix(".tmp")
        with tmp.open("wb") as out:
            for chunk in manifest.chunks:
                path = self.chunks_dir / pack_id / f"{chunk.index}.chunk"
                out.write(path.read_bytes())
        os.replace(tmp, output)
        return output

    def verify_manifest(self, manifest: EpochPackManifest) -> None:
        chunk_hashes = [bytes.fromhex(chunk.sha256) for chunk in manifest.chunks]
        commitment = merkle_root(chunk_hashes).hex()
        if commitment != manifest.commitment_root:
            raise ValueError("epoch commitment root mismatch")

    def verify_file(self, path: Path, manifest: EpochPackManifest) -> None:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != manifest.whole_file_sha256:
            raise ValueError("epoch pack file hash mismatch")

    def load_progress(self) -> dict:
        if not self.progress_path.exists():
            return {}
        return json.loads(self.progress_path.read_text())

    def save_progress(self, progress: dict) -> None:
        tmp = self.progress_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(progress, indent=2))
        os.replace(tmp, self.progress_path)


def build_chunk_manifest(path: Path, chunk_size: int) -> Tuple[int, List[ChunkInfo]]:
    chunks: List[ChunkInfo] = []
    size = path.stat().st_size
    with path.open("rb") as handle:
        index = 0
        while True:
            data = handle.read(chunk_size)
            if not data:
                break
            digest = hashlib.sha256(data).hexdigest()
            chunks.append(ChunkInfo(index=index, sha256=digest, size=len(data)))
            index += 1
    return size, chunks


def compute_commitment_root(chunks: Iterable[ChunkInfo]) -> str:
    hashes = [bytes.fromhex(chunk.sha256) for chunk in chunks]
    return merkle_root(hashes).hex()
