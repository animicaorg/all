from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from animica.sync.epoch_pack import parse_index, read_pack_sections
from animica.sync.pcp import build_proof, hash_payload
from animica.sync.schemas import EpochPackManifest, SnapshotManifest
from animica.sync.storage import EpochPackStore, SnapshotStore
from p2p.wire.encoding import decode_payload, encode_payload
from p2p.wire.message_ids import MsgID
from p2p.wire.messages import (
    EpochChunkReq,
    EpochChunkResp,
    EpochIndexReq,
    EpochIndexResp,
    EpochList,
    EpochListResp,
    EpochManifestReq,
    EpochManifestResp,
    PCPProofReq,
    PCPProofResp,
    PCPSampleReq,
    PCPSampleResp,
    SnapshotChunkRespV2,
    SnapshotChunkV2,
    SnapshotList,
    SnapshotListResp,
    SnapshotManifestReq,
    SnapshotManifestResp,
)

log = logging.getLogger("animica.p2p.protocol.fastbootstrap")


@dataclass
class FastBootstrapHandler:
    snapshot_store: SnapshotStore
    epoch_store: EpochPackStore
    data_dir: Path
    pcp_rate_limit_per_minute: int = 120
    _pcp_requests: Dict[str, List[float]] = field(default_factory=dict)

    def msg_ids(self) -> Iterable[int]:
        return [
            MsgID.SNAPSHOT_LIST,
            MsgID.SNAPSHOT_MANIFEST,
            MsgID.SNAPSHOT_CHUNK_V2,
            MsgID.EPOCH_LIST,
            MsgID.EPOCH_MANIFEST,
            MsgID.EPOCH_CHUNK,
            MsgID.EPOCH_INDEX,
            MsgID.PCP_PROOF,
            MsgID.PCP_SAMPLE,
        ]

    async def handle(self, conn: Any, frame: Any) -> None:
        if frame.msg_id == MsgID.SNAPSHOT_LIST:
            await self._handle_snapshot_list(conn, frame)
            return
        if frame.msg_id == MsgID.SNAPSHOT_MANIFEST:
            await self._handle_snapshot_manifest(conn, frame)
            return
        if frame.msg_id == MsgID.SNAPSHOT_CHUNK_V2:
            await self._handle_snapshot_chunk(conn, frame)
            return
        if frame.msg_id == MsgID.EPOCH_LIST:
            await self._handle_epoch_list(conn, frame)
            return
        if frame.msg_id == MsgID.EPOCH_MANIFEST:
            await self._handle_epoch_manifest(conn, frame)
            return
        if frame.msg_id == MsgID.EPOCH_CHUNK:
            await self._handle_epoch_chunk(conn, frame)
            return
        if frame.msg_id == MsgID.EPOCH_INDEX:
            await self._handle_epoch_index(conn, frame)
            return
        if frame.msg_id == MsgID.PCP_PROOF:
            await self._handle_pcp_proof(conn, frame)
            return
        if frame.msg_id == MsgID.PCP_SAMPLE:
            await self._handle_pcp_sample(conn, frame)
            return

    async def _handle_snapshot_list(self, conn: Any, frame: Any) -> None:
        req = SnapshotList(**decode_payload(frame.payload))
        manifests = []
        for path in sorted(self.snapshot_store.manifests_dir.glob("snapshot_*.json")):
            manifest = SnapshotManifest.model_validate_json(path.read_text())
            if manifest.snapshot_height < req.min_height:
                continue
            manifests.append(manifest.model_dump())
            if len(manifests) >= req.limit:
                break
        response = SnapshotListResp(manifests=manifests)
        await conn.send_frame(MsgID.SNAPSHOT_LIST_RESP, encode_payload(response))

    async def _handle_snapshot_manifest(self, conn: Any, frame: Any) -> None:
        req = SnapshotManifestReq(**decode_payload(frame.payload))
        path = self.snapshot_store.manifests_dir / f"snapshot_{req.snapshot_id}.json"
        if not path.exists():
            response = SnapshotManifestResp(manifest=None, found=False)
        else:
            manifest = SnapshotManifest.model_validate_json(path.read_text())
            response = SnapshotManifestResp(manifest=manifest.model_dump(), found=True)
        await conn.send_frame(MsgID.SNAPSHOT_MANIFEST_RESP, encode_payload(response))

    async def _handle_snapshot_chunk(self, conn: Any, frame: Any) -> None:
        req = SnapshotChunkV2(**decode_payload(frame.payload))
        chunk_path = self.snapshot_store.chunks_dir / req.snapshot_id / f"{req.chunk_index}.chunk"
        if not chunk_path.exists():
            response = SnapshotChunkRespV2(
                snapshot_id=req.snapshot_id,
                chunk_index=req.chunk_index,
                data=b"",
                found=False,
            )
        else:
            response = SnapshotChunkRespV2(
                snapshot_id=req.snapshot_id,
                chunk_index=req.chunk_index,
                data=chunk_path.read_bytes(),
                found=True,
            )
        await conn.send_frame(MsgID.SNAPSHOT_CHUNK_RESP_V2, encode_payload(response))

    async def _handle_epoch_list(self, conn: Any, frame: Any) -> None:
        req = EpochList(**decode_payload(frame.payload))
        manifests = []
        for path in sorted(self.epoch_store.manifests_dir.glob("epoch_*.json")):
            manifest = EpochPackManifest.model_validate_json(path.read_text())
            if manifest.kind != req.kind:
                continue
            if manifest.epoch_id < req.min_epoch_id:
                continue
            manifests.append(manifest.model_dump())
            if len(manifests) >= req.limit:
                break
        response = EpochListResp(manifests=manifests)
        await conn.send_frame(MsgID.EPOCH_LIST_RESP, encode_payload(response))

    async def _handle_epoch_manifest(self, conn: Any, frame: Any) -> None:
        req = EpochManifestReq(**decode_payload(frame.payload))
        path = self.epoch_store.manifests_dir / f"epoch_{req.pack_id}.json"
        if not path.exists():
            response = EpochManifestResp(manifest=None, found=False)
        else:
            manifest = EpochPackManifest.model_validate_json(path.read_text())
            response = EpochManifestResp(manifest=manifest.model_dump(), found=True)
        await conn.send_frame(MsgID.EPOCH_MANIFEST_RESP, encode_payload(response))

    async def _handle_epoch_chunk(self, conn: Any, frame: Any) -> None:
        req = EpochChunkReq(**decode_payload(frame.payload))
        chunk_path = self.epoch_store.chunks_dir / req.pack_id / f"{req.chunk_index}.chunk"
        if not chunk_path.exists():
            response = EpochChunkResp(
                pack_id=req.pack_id,
                chunk_index=req.chunk_index,
                data=b"",
                found=False,
            )
        else:
            response = EpochChunkResp(
                pack_id=req.pack_id,
                chunk_index=req.chunk_index,
                data=chunk_path.read_bytes(),
                found=True,
            )
        await conn.send_frame(MsgID.EPOCH_CHUNK_RESP, encode_payload(response))

    async def _handle_epoch_index(self, conn: Any, frame: Any) -> None:
        req = EpochIndexReq(**decode_payload(frame.payload))
        pack_path = self.epoch_store.base_dir / f"epoch_{req.pack_id}.epk"
        if not pack_path.exists():
            response = EpochIndexResp(pack_id=req.pack_id, index=b"", found=False)
        else:
            _, index_bytes, _ = read_pack_sections(pack_path)
            response = EpochIndexResp(pack_id=req.pack_id, index=index_bytes, found=True)
        await conn.send_frame(MsgID.EPOCH_INDEX_RESP, encode_payload(response))

    async def _handle_pcp_proof(self, conn: Any, frame: Any) -> None:
        req = PCPProofReq(**decode_payload(frame.payload))
        pack_path = self.epoch_store.base_dir / f"epoch_{req.pack_id}.epk"
        if not pack_path.exists():
            response = PCPProofResp(pack_id=req.pack_id, height=req.height, found=False)
            await conn.send_frame(MsgID.PCP_PROOF_RESP, encode_payload(response))
            return
        _, index_bytes, payload = read_pack_sections(pack_path)
        entries = parse_index(index_bytes)
        for idx, entry in enumerate(entries):
            if entry.height == req.height:
                item = payload[entry.offset : entry.offset + entry.length]
                item_hashes = [hash_payload(payload[e.offset : e.offset + e.length]) for e in entries]
                proof = build_proof(item_hashes, idx)
                response = PCPProofResp(
                    pack_id=req.pack_id,
                    height=req.height,
                    leaf_hash=proof.leaf_hash.hex(),
                    root=proof.root.hex(),
                    steps=[
                        {"hash": step.sibling.hex(), "direction": step.direction}
                        for step in proof.proof
                    ],
                    found=True,
                )
                await conn.send_frame(MsgID.PCP_PROOF_RESP, encode_payload(response))
                return
        response = PCPProofResp(pack_id=req.pack_id, height=req.height, found=False)
        await conn.send_frame(MsgID.PCP_PROOF_RESP, encode_payload(response))

    async def _handle_pcp_sample(self, conn: Any, frame: Any) -> None:
        req = PCPSampleReq(**decode_payload(frame.payload))
        now = time.time()
        history = self._pcp_requests.setdefault(conn.remote_addr, [])
        history[:] = [t for t in history if now - t < 60]
        if len(history) >= self.pcp_rate_limit_per_minute:
            response = PCPSampleResp(
                pack_id=req.pack_id,
                seed=req.seed,
                k=req.k,
                items=[],
                rate_limited=True,
            )
            await conn.send_frame(MsgID.PCP_SAMPLE_RESP, encode_payload(response))
            return
        history.append(now)
        pack_path = self.epoch_store.base_dir / f"epoch_{req.pack_id}.epk"
        if not pack_path.exists():
            response = PCPSampleResp(pack_id=req.pack_id, seed=req.seed, k=req.k, items=[], rate_limited=False)
            await conn.send_frame(MsgID.PCP_SAMPLE_RESP, encode_payload(response))
            return
        _, index_bytes, payload = read_pack_sections(pack_path)
        entries = parse_index(index_bytes)
        rng = random.Random(req.seed)
        sample_entries = rng.sample(entries, min(req.k, len(entries)))
        item_hashes = [hash_payload(payload[e.offset : e.offset + e.length]) for e in entries]
        items: List[dict] = []
        for entry in sample_entries:
            idx = entries.index(entry)
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
        response = PCPSampleResp(
            pack_id=req.pack_id,
            seed=req.seed,
            k=req.k,
            items=items,
            rate_limited=False,
        )
        await conn.send_frame(MsgID.PCP_SAMPLE_RESP, encode_payload(response))
