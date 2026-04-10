from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .merkle import ProofStep
from .pcp import build_proof, hash_payload, verify_pcp_proof
from .schemas import EpochPackManifest, PCPSample, PCPSampleItem, PCPProof, SnapshotManifest
from .storage import EpochPackStore, SnapshotStore

log = logging.getLogger("animica.sync.fastbootstrap")


class SyncPhase(str, Enum):
    INIT = "init"
    DISCOVERY = "discovery"
    SNAPSHOT_DOWNLOAD = "snapshot_download"
    SNAPSHOT_RESTORE = "snapshot_restore"
    TAIL_HEADERS = "tail_headers"
    TAIL_BLOCKS = "tail_blocks"
    VERIFYING = "verifying"
    SYNCED = "synced"
    BACKFILL_HEADERS = "backfill_headers"
    BACKFILL_FULL = "backfill_full"
    DEGRADED = "degraded"
    RECOVERY = "recovery"


@dataclass
class SyncConfig:
    chunk_parallelism: int = 4
    snapshot_quorum_min_peers: int = 3
    snapshot_quorum_ratio: float = 0.6
    stall_timeout_s: float = 20.0
    max_headers_per_request: int = 256
    max_blocks_per_request: int = 64
    pcp_sample_limit: int = 64
    pcp_rate_limit_per_minute: int = 120


@dataclass
class PeerScore:
    peer_id: str
    score: float = 0.0
    failures: int = 0
    last_seen: float = field(default_factory=time.time)

    def reward(self) -> None:
        self.score = min(self.score + 1.0, 100.0)
        self.failures = 0

    def penalize(self) -> None:
        self.score = max(self.score - 1.5, -50.0)
        self.failures += 1
        self.last_seen = time.time()


class PeerError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class PeerInterface:
    peer_id: str

    def list_snapshots(self) -> Sequence[SnapshotManifest]:
        raise NotImplementedError

    def get_snapshot_manifest(self, snapshot_id: str) -> SnapshotManifest:
        raise NotImplementedError

    def get_snapshot_chunk(self, snapshot_id: str, chunk_index: int) -> bytes:
        raise NotImplementedError

    def list_epoch_packs(self, kind: str, min_epoch_id: int) -> Sequence[EpochPackManifest]:
        raise NotImplementedError

    def get_epoch_manifest(self, pack_id: str) -> EpochPackManifest:
        raise NotImplementedError

    def get_epoch_chunk(self, pack_id: str, chunk_index: int) -> bytes:
        raise NotImplementedError

    def get_epoch_index(self, pack_id: str) -> bytes:
        raise NotImplementedError

    def get_pcp_proof(self, pack_id: str, height: int) -> Tuple[bytes, List[ProofStep]]:
        raise NotImplementedError

    def get_pcp_sample(self, pack_id: str, seed: int, k: int) -> PCPSample:
        raise NotImplementedError

    def get_headers(self, start_height: int, limit: int) -> Sequence[int]:
        raise NotImplementedError

    def get_blocks(self, heights: Sequence[int]) -> Sequence[int]:
        raise NotImplementedError


@dataclass
class SyncState:
    phase: SyncPhase = SyncPhase.INIT
    last_progress_at: float = field(default_factory=time.time)
    last_anchor_height: Optional[int] = None
    last_snapshot_id: Optional[str] = None
    penalties: Dict[str, int] = field(default_factory=dict)


class FastBootstrapEngine:
    def __init__(
        self,
        *,
        peers: Sequence[PeerInterface],
        snapshot_store: SnapshotStore,
        epoch_store: EpochPackStore,
        data_dir: Path,
        config: Optional[SyncConfig] = None,
    ) -> None:
        self.peers = list(peers)
        self.snapshot_store = snapshot_store
        self.epoch_store = epoch_store
        self.data_dir = data_dir
        self.config = config or SyncConfig()
        self.state = SyncState()
        self.peer_scores: Dict[str, PeerScore] = {
            peer.peer_id: PeerScore(peer.peer_id) for peer in self.peers
        }
        self._pcp_requests: Dict[str, List[float]] = {}

    def select_snapshot(self) -> Optional[SnapshotManifest]:
        manifests: List[SnapshotManifest] = []
        for peer in self.peers:
            manifests.extend(peer.list_snapshots())
        if not manifests:
            return None
        buckets: Dict[Tuple[int, str, str], List[SnapshotManifest]] = {}
        for manifest in manifests:
            key = (
                manifest.snapshot_height,
                manifest.snapshot_hash,
                manifest.whole_file_sha256,
            )
            buckets.setdefault(key, []).append(manifest)
        total_peers = len({m.producer_node_id for m in manifests})
        min_quorum = max(
            self.config.snapshot_quorum_min_peers,
            int((total_peers * self.config.snapshot_quorum_ratio) + 0.5),
        )
        best: Optional[SnapshotManifest] = None
        for items in buckets.values():
            if len(items) >= min_quorum:
                candidate = max(items, key=lambda m: m.snapshot_height)
                if not best or candidate.snapshot_height > best.snapshot_height:
                    best = candidate
        return best

    def download_snapshot(self, manifest: SnapshotManifest) -> Path:
        snapshot_id = manifest.snapshot_id()
        self.snapshot_store.verify_manifest(manifest)
        for chunk in manifest.chunks:
            chunk_ok = False
            random.shuffle(self.peers)
            for peer in self.peers:
                data = peer.get_snapshot_chunk(snapshot_id, chunk.index)
                if hashlib.sha256(data).hexdigest() != chunk.sha256:
                    self._penalize(peer.peer_id)
                    continue
                self.snapshot_store.write_chunk(snapshot_id, chunk.index, data)
                chunk_ok = True
                self._reward(peer.peer_id)
                break
            if not chunk_ok:
                raise PeerError("snapshot_chunk_unavailable")
        path = self.snapshot_store.assemble(snapshot_id, manifest)
        self.snapshot_store.verify_file(path, manifest)
        self.state.last_snapshot_id = snapshot_id
        self._mark_progress()
        return path

    def restore_snapshot(self, snapshot_file: Path) -> None:
        self.state.phase = SyncPhase.SNAPSHOT_RESTORE
        restore_dir = self.data_dir / "state"
        self.snapshot_store.restore_atomic(snapshot_file, restore_dir)
        self._mark_progress()

    def tail_sync(self, current_height: int, target_height: int) -> int:
        self.state.phase = SyncPhase.TAIL_HEADERS
        height = current_height
        while height < target_height:
            peer = self._select_peer()
            try:
                headers = peer.get_headers(height + 1, self.config.max_headers_per_request)
            except PeerError as exc:
                self._handle_peer_error(peer, exc)
                continue
            if not headers:
                self._handle_peer_error(peer, PeerError("headers_empty"))
                continue
            height = max(headers)
            self._mark_progress()
        self.state.phase = SyncPhase.TAIL_BLOCKS
        next_height = current_height + 1
        while next_height <= target_height:
            peer = self._select_peer()
            batch = list(range(next_height, min(next_height + self.config.max_blocks_per_request, target_height + 1)))
            try:
                blocks = peer.get_blocks(batch)
            except PeerError as exc:
                self._handle_peer_error(peer, exc)
                continue
            if not blocks:
                self._handle_peer_error(peer, PeerError("missing_parent"))
                continue
            next_height = max(blocks) + 1
            self._mark_progress()
        self.state.phase = SyncPhase.SYNCED
        return target_height

    def backfill_epoch_packs(self, kind: str, min_epoch_id: int) -> None:
        self.state.phase = SyncPhase.BACKFILL_FULL if kind == "full" else SyncPhase.BACKFILL_HEADERS
        progress = self.epoch_store.load_progress()
        start_epoch = max(min_epoch_id, progress.get(kind, min_epoch_id))
        for peer in self.peers:
            packs = peer.list_epoch_packs(kind, start_epoch)
            for manifest in packs:
                if manifest.epoch_id < start_epoch:
                    continue
                self._download_epoch_pack(manifest)
                progress[kind] = manifest.epoch_id + 1
                self.epoch_store.save_progress(progress)
                self._mark_progress()

    def anchor_epoch(self, manifest: EpochPackManifest, expected_end_hash: str) -> None:
        if manifest.end_hash != expected_end_hash:
            raise PeerError("not_anchored", "epoch end hash mismatch")

    def _download_epoch_pack(self, manifest: EpochPackManifest) -> Path:
        pack_id = manifest.pack_id()
        self.epoch_store.verify_manifest(manifest)
        for chunk in manifest.chunks:
            chunk_ok = False
            random.shuffle(self.peers)
            for peer in self.peers:
                data = peer.get_epoch_chunk(pack_id, chunk.index)
                if hashlib.sha256(data).hexdigest() != chunk.sha256:
                    self._penalize(peer.peer_id)
                    continue
                self.epoch_store.write_chunk(pack_id, chunk.index, data)
                self._reward(peer.peer_id)
                chunk_ok = True
                break
            if not chunk_ok:
                raise PeerError("epoch_chunk_corrupt")
        pack_path = self.epoch_store.assemble(pack_id, manifest)
        self.epoch_store.verify_file(pack_path, manifest)
        return pack_path

    def pcp_sample(self, pack_id: str, seed: int, k: int) -> PCPSample:
        k = min(k, self.config.pcp_sample_limit)
        now = time.time()
        history = self._pcp_requests.setdefault(pack_id, [])
        history[:] = [t for t in history if now - t < 60]
        if len(history) >= self.config.pcp_rate_limit_per_minute:
            return PCPSample(pack_id=pack_id, seed=seed, items=[], k=k, rate_limited=True)
        history.append(now)
        peer = self._select_peer()
        return peer.get_pcp_sample(pack_id, seed, k)

    def verify_pcp_sample(self, sample: PCPSample, root_hex: str) -> bool:
        root = bytes.fromhex(root_hex)
        for item in sample.items:
            leaf_hash = hash_payload(item.payload)
            proof_steps = [
                ProofStep(bytes.fromhex(step["hash"]), step["direction"])
                for step in item.proof.steps
            ]
            if not verify_pcp_proof(leaf_hash, proof_steps, root):
                return False
        return True

    def watchdog_tick(self) -> None:
        if time.time() - self.state.last_progress_at > self.config.stall_timeout_s:
            self.state.phase = SyncPhase.RECOVERY
            for score in self.peer_scores.values():
                score.penalize()
            self._mark_progress()

    def _select_peer(self) -> PeerInterface:
        sorted_peers = sorted(
            self.peers,
            key=lambda peer: (self.peer_scores[peer.peer_id].score, random.random()),
            reverse=True,
        )
        return sorted_peers[0]

    def _handle_peer_error(self, peer: PeerInterface, exc: PeerError) -> None:
        code = exc.code
        if code in {"headers_empty", "missing_parent", "invalid_headers", "not_anchored"}:
            self._penalize(peer.peer_id)
        elif code == "peer_behind":
            self._penalize(peer.peer_id)
        elif code == "stale_network_best":
            self._penalize(peer.peer_id)
        self.state.phase = SyncPhase.RECOVERY

    def _reward(self, peer_id: str) -> None:
        self.peer_scores[peer_id].reward()

    def _penalize(self, peer_id: str) -> None:
        self.peer_scores[peer_id].penalize()

    def _mark_progress(self) -> None:
        self.state.last_progress_at = time.time()
