from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChunkInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    sha256: str
    size: int


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    snapshot_height: int
    snapshot_hash: str
    state_root: str
    total_work: int
    created_at: str
    format_version: int
    file_size: int
    chunk_size: int
    chunks: List[ChunkInfo]
    whole_file_sha256: str
    commitment_root: str
    producer_node_id: str
    signature: str
    base_epoch_id: int

    def snapshot_id(self) -> str:
        return f"{self.chain_id}:{self.snapshot_height}:{self.whole_file_sha256}"


class EpochPackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    kind: str
    epoch_id: int
    start_height: int
    end_height: int
    start_hash: str
    end_hash: str
    total_work: int
    file_size: int
    chunk_size: int
    chunks: List[ChunkInfo]
    whole_file_sha256: str
    commitment_root: str
    payload_root: str
    pcp_root: str
    producer_node_id: str
    signature: str

    def pack_id(self) -> str:
        return f"{self.chain_id}:{self.kind}:{self.epoch_id}:{self.end_hash}"


class PCPProof(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leaf_hash: str
    root: str
    steps: List[dict]


class PCPSampleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    height: int
    payload: bytes
    proof: PCPProof


class PCPSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    seed: int
    items: List[PCPSampleItem]
    k: int
    rate_limited: bool = False
