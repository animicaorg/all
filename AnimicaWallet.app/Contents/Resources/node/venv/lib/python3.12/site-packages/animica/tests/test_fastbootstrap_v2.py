from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

import pytest

from animica.sync.canonical import canonical_bytes
from animica.sync.epoch_pack import build_epoch_pack, parse_index, read_pack_sections
from animica.sync.fastbootstrap import FastBootstrapEngine, PeerError, SyncConfig
from animica.sync.merkle import merkle_root
from animica.sync.pcp import build_proof, hash_payload, verify_pcp_proof
from animica.sync.schemas import EpochPackManifest, PCPSample, SnapshotManifest
from animica.sync.storage import EpochPackStore, SnapshotStore, build_chunk_manifest, compute_commitment_root


@dataclass
class FakePeer:
    peer_id: str
    snapshots: Dict[str, SnapshotManifest]
    snapshot_chunks: Dict[str, Dict[int, bytes]]
    epoch_packs: Dict[str, EpochPackManifest]
    epoch_chunks: Dict[str, Dict[int, bytes]]
    headers_behavior: List[object]
    blocks_behavior: List[object]
    pack_payloads: Dict[str, bytes]
    pack_index: Dict[str, bytes]

    def list_snapshots(self) -> Sequence[SnapshotManifest]:
        return list(self.snapshots.values())

    def get_snapshot_manifest(self, snapshot_id: str) -> SnapshotManifest:
        return self.snapshots[snapshot_id]

    def get_snapshot_chunk(self, snapshot_id: str, chunk_index: int) -> bytes:
        return self.snapshot_chunks[snapshot_id][chunk_index]

    def list_epoch_packs(self, kind: str, min_epoch_id: int) -> Sequence[EpochPackManifest]:
        return [m for m in self.epoch_packs.values() if m.kind == kind and m.epoch_id >= min_epoch_id]

    def get_epoch_manifest(self, pack_id: str) -> EpochPackManifest:
        return self.epoch_packs[pack_id]

    def get_epoch_chunk(self, pack_id: str, chunk_index: int) -> bytes:
        return self.epoch_chunks[pack_id][chunk_index]

    def get_epoch_index(self, pack_id: str) -> bytes:
        return self.pack_index[pack_id]

    def get_pcp_proof(self, pack_id: str, height: int):
        index = parse_index(self.pack_index[pack_id])
        entries = index
        payload = self.pack_payloads[pack_id]
        item_hashes = [hash_payload(payload[e.offset : e.offset + e.length]) for e in entries]
        for idx, entry in enumerate(entries):
            if entry.height == height:
                proof = build_proof(item_hashes, idx)
                return proof.leaf_hash, list(proof.proof)
        raise KeyError

    def get_pcp_sample(self, pack_id: str, seed: int, k: int) -> PCPSample:
        index = parse_index(self.pack_index[pack_id])
        payload = self.pack_payloads[pack_id]
        rng = random.Random(seed)
        sample_entries = rng.sample(index, min(k, len(index)))
        item_hashes = [hash_payload(payload[e.offset : e.offset + e.length]) for e in index]
        items = []
        for entry in sample_entries:
            idx = index.index(entry)
            proof = build_proof(item_hashes, idx)
            items.append(
                {
                    "height": entry.height,
                    "payload": payload[entry.offset : entry.offset + entry.length],
                    "proof": {
                        "leaf_hash": proof.leaf_hash.hex(),
                        "root": proof.root.hex(),
                        "steps": [
                            {"hash": step.sibling.hex(), "direction": step.direction}
                            for step in proof.proof
                        ],
                    },
                }
            )
        return PCPSample(pack_id=pack_id, seed=seed, k=k, items=items)

    def get_headers(self, start_height: int, limit: int) -> Sequence[int]:
        if self.headers_behavior:
            action = self.headers_behavior.pop(0)
            if isinstance(action, Exception):
                raise action
            return action
        return list(range(start_height, start_height + limit))

    def get_blocks(self, heights: Sequence[int]) -> Sequence[int]:
        if self.blocks_behavior:
            action = self.blocks_behavior.pop(0)
            if isinstance(action, Exception):
                raise action
            return action
        return list(heights)


@pytest.fixture()
def snapshot_manifest(tmp_path: Path) -> SnapshotManifest:
    snapshot_dir = tmp_path / "snapshot_data"
    snapshot_dir.mkdir()
    (snapshot_dir / "state.json").write_text(json.dumps({"height": 100}, indent=2))
    archive_path = shutil.make_archive(str(tmp_path / "snapshot"), "zip", snapshot_dir)
    archive_path = Path(archive_path)
    file_size, chunks = build_chunk_manifest(archive_path, 1024)
    manifest = SnapshotManifest(
        chain_id=1,
        snapshot_height=100,
        snapshot_hash="aa" * 32,
        state_root="bb" * 32,
        total_work=123,
        created_at="2024-01-01T00:00:00Z",
        format_version=2,
        file_size=file_size,
        chunk_size=1024,
        chunks=chunks,
        whole_file_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        commitment_root=compute_commitment_root(chunks),
        producer_node_id="peerA",
        signature="sig",
        base_epoch_id=0,
    )
    return manifest


def _make_engine(tmp_path: Path, peers: Sequence[FakePeer]) -> FastBootstrapEngine:
    snapshot_store = SnapshotStore(tmp_path / "snapshots")
    epoch_store = EpochPackStore(tmp_path / "epoch_packs")
    return FastBootstrapEngine(
        peers=peers,
        snapshot_store=snapshot_store,
        epoch_store=epoch_store,
        data_dir=tmp_path,
        config=SyncConfig(stall_timeout_s=0.01, pcp_rate_limit_per_minute=2),
    )


def test_snapshot_quorum_selection(snapshot_manifest: SnapshotManifest, tmp_path: Path) -> None:
    peers = []
    for idx in range(5):
        manifest = snapshot_manifest.model_copy()
        manifest.producer_node_id = f"peer{idx}"
        if idx >= 3:
            manifest.snapshot_hash = "cc" * 32
        peers.append(
            FakePeer(
                peer_id=f"peer{idx}",
                snapshots={manifest.snapshot_id(): manifest},
                snapshot_chunks={},
                epoch_packs={},
                epoch_chunks={},
                headers_behavior=[],
                blocks_behavior=[],
                pack_payloads={},
                pack_index={},
            )
        )
    engine = _make_engine(tmp_path, peers)
    selected = engine.select_snapshot()
    assert selected is not None
    assert selected.snapshot_hash == snapshot_manifest.snapshot_hash


def test_snapshot_corrupted_chunk_redownload(snapshot_manifest: SnapshotManifest, tmp_path: Path) -> None:
    snapshot_id = snapshot_manifest.snapshot_id()
    archive = tmp_path / "snapshot.zip"
    archive.write_bytes(b"data" * 10)
    file_size, chunks = build_chunk_manifest(archive, 5)
    snapshot_manifest = snapshot_manifest.model_copy(update={
        "file_size": file_size,
        "chunk_size": 5,
        "chunks": chunks,
        "whole_file_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "commitment_root": compute_commitment_root(chunks),
    })
    corrupt = archive.read_bytes()[:5] + b"corrupt"
    good_chunks = {c.index: archive.read_bytes()[c.index * 5 : c.index * 5 + c.size] for c in chunks}
    peer_a = FakePeer(
        peer_id="peerA",
        snapshots={snapshot_id: snapshot_manifest},
        snapshot_chunks={snapshot_id: {0: corrupt, **good_chunks}},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={},
        pack_index={},
    )
    peer_b = FakePeer(
        peer_id="peerB",
        snapshots={snapshot_id: snapshot_manifest},
        snapshot_chunks={snapshot_id: good_chunks},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={},
        pack_index={},
    )
    engine = _make_engine(tmp_path, [peer_a, peer_b])
    path = engine.download_snapshot(snapshot_manifest)
    assert path.exists()


def test_snapshot_manifest_mismatch_rejected(snapshot_manifest: SnapshotManifest, tmp_path: Path) -> None:
    manifest_a = snapshot_manifest.model_copy(update={"snapshot_hash": "11" * 32})
    manifest_b = snapshot_manifest.model_copy(update={"snapshot_hash": "22" * 32})
    peer_a = FakePeer(
        peer_id="peerA",
        snapshots={manifest_a.snapshot_id(): manifest_a},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={},
        pack_index={},
    )
    peer_b = FakePeer(
        peer_id="peerB",
        snapshots={manifest_b.snapshot_id(): manifest_b},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={},
        pack_index={},
    )
    engine = _make_engine(tmp_path, [peer_a, peer_b])
    assert engine.select_snapshot() is None


def test_snapshot_restore_atomic(snapshot_manifest: SnapshotManifest, tmp_path: Path) -> None:
    snapshot_id = snapshot_manifest.snapshot_id()
    archive = shutil.make_archive(str(tmp_path / "snap"), "zip", tmp_path)
    archive = Path(archive)
    file_size, chunks = build_chunk_manifest(archive, 256)
    manifest = snapshot_manifest.model_copy(update={
        "file_size": file_size,
        "chunk_size": 256,
        "chunks": chunks,
        "whole_file_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "commitment_root": compute_commitment_root(chunks),
    })
    store = SnapshotStore(tmp_path / "store")
    for chunk in chunks:
        data = archive.read_bytes()[chunk.index * 256 : chunk.index * 256 + chunk.size]
        store.write_chunk(snapshot_id, chunk.index, data)
    restored = store.restore_atomic(archive, tmp_path / "state")
    assert (restored / "snapshot_restored.json").exists()


def test_tail_sync_after_restore(snapshot_manifest: SnapshotManifest, tmp_path: Path) -> None:
    peer = FakePeer(
        peer_id="peerA",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[list(range(101, 106))],
        blocks_behavior=[list(range(101, 106))],
        pack_payloads={},
        pack_index={},
    )
    engine = _make_engine(tmp_path, [peer])
    final_height = engine.tail_sync(100, 105)
    assert final_height == 105


def test_epoch_pack_creation_manifest(tmp_path: Path) -> None:
    items = [b"block1", b"block2", b"block3"]
    hashes = [hash_payload(item) for item in items]
    manifest = build_epoch_pack(
        chain_id=1,
        kind="headers",
        epoch_id=0,
        start_height=0,
        end_height=2,
        start_hash="aa",
        end_hash="bb",
        total_work=10,
        items=items,
        item_hashes=hashes,
        output_path=tmp_path / "epoch.epk",
    )
    assert manifest.payload_root == merkle_root(hashes).hex()
    meta, index_bytes, payload = read_pack_sections(tmp_path / "epoch.epk")
    assert meta["epoch_id"] == 0
    entries = parse_index(index_bytes)
    assert entries[0].height == 0
    assert payload.startswith(items[0])


def test_epoch_pack_chunk_corruption_redownload(tmp_path: Path) -> None:
    items = [b"a", b"b"]
    hashes = [hash_payload(item) for item in items]
    pack_path = tmp_path / "epoch.epk"
    manifest = build_epoch_pack(
        chain_id=1,
        kind="full",
        epoch_id=1,
        start_height=0,
        end_height=1,
        start_hash="aa",
        end_hash="bb",
        total_work=10,
        items=items,
        item_hashes=hashes,
        output_path=pack_path,
        chunk_size=4,
    )
    pack_id = manifest.pack_id()
    _, chunks = build_chunk_manifest(pack_path, manifest.chunk_size)
    bad_chunk = b"bad"
    good_chunks = {c.index: pack_path.read_bytes()[c.index * 4 : c.index * 4 + c.size] for c in chunks}
    peer_bad = FakePeer(
        peer_id="peerBad",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={pack_id: manifest},
        epoch_chunks={pack_id: {0: bad_chunk, **good_chunks}},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={pack_id: read_pack_sections(pack_path)[2]},
        pack_index={pack_id: read_pack_sections(pack_path)[1]},
    )
    peer_good = FakePeer(
        peer_id="peerGood",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={pack_id: manifest},
        epoch_chunks={pack_id: good_chunks},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={pack_id: read_pack_sections(pack_path)[2]},
        pack_index={pack_id: read_pack_sections(pack_path)[1]},
    )
    engine = _make_engine(tmp_path, [peer_bad, peer_good])
    engine._download_epoch_pack(manifest)


def test_epoch_pack_anchor_rejects_mismatch(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, [])
    manifest = EpochPackManifest(
        chain_id=1,
        kind="headers",
        epoch_id=0,
        start_height=0,
        end_height=1,
        start_hash="aa",
        end_hash="bb",
        total_work=10,
        file_size=0,
        chunk_size=1,
        chunks=[],
        whole_file_sha256="",
        commitment_root="",
        payload_root="",
        pcp_root="",
        producer_node_id="peer",
        signature="",
    )
    with pytest.raises(PeerError):
        engine.anchor_epoch(manifest, "cc")


def test_backfill_resume_after_interruption(tmp_path: Path) -> None:
    items = [b"x", b"y"]
    hashes = [hash_payload(item) for item in items]
    pack_path = tmp_path / "epoch.epk"
    manifest = build_epoch_pack(
        chain_id=1,
        kind="headers",
        epoch_id=2,
        start_height=0,
        end_height=1,
        start_hash="aa",
        end_hash="bb",
        total_work=10,
        items=items,
        item_hashes=hashes,
        output_path=pack_path,
    )
    pack_id = manifest.pack_id()
    payload = read_pack_sections(pack_path)[2]
    index_bytes = read_pack_sections(pack_path)[1]
    peer = FakePeer(
        peer_id="peer",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={pack_id: manifest},
        epoch_chunks={pack_id: {0: pack_path.read_bytes()}},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={pack_id: payload},
        pack_index={pack_id: index_bytes},
    )
    engine = _make_engine(tmp_path, [peer])
    engine.epoch_store.save_progress({"headers": 2})
    engine.backfill_epoch_packs("headers", 1)
    progress = engine.epoch_store.load_progress()
    assert progress["headers"] == 3


def test_pcp_root_and_proof_validation() -> None:
    items = [b"one", b"two", b"three"]
    hashes = [hash_payload(item) for item in items]
    root = merkle_root(hashes)
    proof = build_proof(hashes, 1)
    assert proof.root == root
    assert verify_pcp_proof(proof.leaf_hash, proof.proof, root)
    bad_step = proof.proof[0]
    assert not verify_pcp_proof(proof.leaf_hash, [bad_step], b"\x00" * 32)


def test_pcp_sample_rate_limit(tmp_path: Path) -> None:
    items = [b"one", b"two"]
    hashes = [hash_payload(item) for item in items]
    pack_path = tmp_path / "epoch.epk"
    manifest = build_epoch_pack(
        chain_id=1,
        kind="headers",
        epoch_id=0,
        start_height=0,
        end_height=1,
        start_hash="aa",
        end_hash="bb",
        total_work=10,
        items=items,
        item_hashes=hashes,
        output_path=pack_path,
    )
    pack_id = manifest.pack_id()
    payload = read_pack_sections(pack_path)[2]
    index_bytes = read_pack_sections(pack_path)[1]
    peer = FakePeer(
        peer_id="peer",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={pack_id: manifest},
        epoch_chunks={pack_id: {0: pack_path.read_bytes()}},
        headers_behavior=[],
        blocks_behavior=[],
        pack_payloads={pack_id: payload},
        pack_index={pack_id: index_bytes},
    )
    engine = _make_engine(tmp_path, [peer])
    assert not engine.pcp_sample(pack_id, 1, 1).rate_limited
    assert not engine.pcp_sample(pack_id, 2, 1).rate_limited
    assert engine.pcp_sample(pack_id, 3, 1).rate_limited


def test_never_stall_headers_empty_rotates_peer(tmp_path: Path) -> None:
    peer_bad = FakePeer(
        peer_id="bad",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[PeerError("headers_empty")],
        blocks_behavior=[list(range(1, 3))],
        pack_payloads={},
        pack_index={},
    )
    peer_good = FakePeer(
        peer_id="good",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[list(range(1, 3))],
        blocks_behavior=[list(range(1, 3))],
        pack_payloads={},
        pack_index={},
    )
    random.seed(0)
    engine = _make_engine(tmp_path, [peer_bad, peer_good])
    assert engine.tail_sync(0, 2) == 2


def test_missing_parent_recovery(tmp_path: Path) -> None:
    peer = FakePeer(
        peer_id="peer",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[list(range(1, 3))],
        blocks_behavior=[PeerError("missing_parent"), list(range(1, 3))],
        pack_payloads={},
        pack_index={},
    )
    engine = _make_engine(tmp_path, [peer])
    assert engine.tail_sync(0, 2) == 2


def test_invalid_headers_ban_peer(tmp_path: Path) -> None:
    peer_bad = FakePeer(
        peer_id="bad",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[PeerError("invalid_headers")],
        blocks_behavior=[list(range(1, 2))],
        pack_payloads={},
        pack_index={},
    )
    peer_good = FakePeer(
        peer_id="good",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[list(range(1, 2))],
        blocks_behavior=[list(range(1, 2))],
        pack_payloads={},
        pack_index={},
    )
    engine = _make_engine(tmp_path, [peer_bad, peer_good])
    assert engine.tail_sync(0, 1) == 1
    assert engine.peer_scores["bad"].score < 0


def test_watchdog_recovers(tmp_path: Path) -> None:
    peer = FakePeer(
        peer_id="peer",
        snapshots={},
        snapshot_chunks={},
        epoch_packs={},
        epoch_chunks={},
        headers_behavior=[list(range(1, 2))],
        blocks_behavior=[list(range(1, 2))],
        pack_payloads={},
        pack_index={},
    )
    engine = _make_engine(tmp_path, [peer])
    engine.state.last_progress_at -= 100
    engine.watchdog_tick()
    assert engine.state.phase == engine.state.phase.RECOVERY
    assert engine.tail_sync(0, 1) == 1
