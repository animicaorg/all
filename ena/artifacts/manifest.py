"""
Artifact manifest definitions for ENA training artifacts.

All artifacts must be content-addressed with deterministic hashing
and include provenance tracking for verifiability.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

__all__ = [
    "ArtifactType",
    "ArtifactManifest",
    "DatasetManifest",
    "EvalReportManifest",
    "ModelCheckpointManifest",
    "RewardDataManifest",
    "IndexShardManifest",
    "hash_artifact",
    "verify_artifact",
]


class ArtifactType(str, Enum):
    """Types of artifacts in the ENA training system."""
    DATASET_SHARD = "dataset_shard"
    EVAL_REPORT = "eval_report"
    MODEL_CHECKPOINT = "model_checkpoint"
    REWARD_DATA = "reward_data"
    INDEX_SHARD = "index_shard"


@dataclass
class ArtifactManifest:
    """
    Base manifest for all ENA artifacts.
    
    All artifacts must be content-addressed and include:
    - artifact_id: deterministic hash of contents
    - type: artifact type
    - created_by: worker identity/address
    - inputs: list of input artifact hashes
    - created_at: ISO 8601 timestamp
    - version: manifest version
    - license_tos_flags: data usage compliance flags
    """
    artifact_id: str
    type: ArtifactType
    created_by: str  # Worker address or identity
    inputs: List[str] = field(default_factory=list)  # Input artifact hashes
    metrics: Dict[str, Any] = field(default_factory=dict)
    signatures: List[str] = field(default_factory=list)  # Optional PQ signatures
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: str = "1.0"
    license_tos_flags: Dict[str, bool] = field(default_factory=lambda: {"safe_allowlist": True})
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        if isinstance(self.type, ArtifactType):
            d["type"] = self.type.value
        return d
    
    def to_json(self) -> str:
        """Convert to canonical JSON for hashing."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'))


@dataclass
class DatasetManifest(ArtifactManifest):
    """
    Manifest for dataset shards.
    
    Includes:
    - source: where data came from (repo, chain, issues, etc.)
    - shard_index: position in full dataset
    - total_shards: total number of shards
    - num_samples: number of samples in this shard
    - dedup_method: deduplication algorithm used
    - safety_filtered: whether safety filtering was applied
    """
    source: str = ""  # e.g., "repo:/path", "chain:1-1000", "issues:org/repo"
    shard_index: int = 0
    total_shards: int = 1
    num_samples: int = 0
    dedup_method: str = "minhash"  # or "simhash", "exact"
    safety_filtered: bool = True
    data_hash: str = ""  # Hash of actual data content
    
    def __post_init__(self):
        if not self.type:
            object.__setattr__(self, 'type', ArtifactType.DATASET_SHARD)


@dataclass
class EvalReportManifest(ArtifactManifest):
    """
    Manifest for evaluation reports.
    
    Includes:
    - model_hash: hash of model being evaluated
    - eval_suite: name of evaluation suite
    - suite_version: version of eval suite
    - total_score: overall score
    - category_scores: breakdown by category
    - num_tasks: number of tasks evaluated
    - pass_rate: percentage of tasks passed
    """
    model_hash: str = ""
    eval_suite: str = "ena_v1"
    suite_version: str = "1.0"
    total_score: float = 0.0
    category_scores: Dict[str, float] = field(default_factory=dict)
    num_tasks: int = 0
    pass_rate: float = 0.0
    report_hash: str = ""  # Hash of full report content
    
    def __post_init__(self):
        if not self.type:
            object.__setattr__(self, 'type', ArtifactType.EVAL_REPORT)


@dataclass
class ModelCheckpointManifest(ArtifactManifest):
    """
    Manifest for model checkpoints.
    
    Includes:
    - model_name: name/identifier of model
    - base_model: base model this was derived from
    - training_method: SFT, DPO, PPO, etc.
    - checkpoint_hash: hash of checkpoint weights
    - num_parameters: number of model parameters
    - is_delta: whether this is a delta/LoRA adapter
    """
    model_name: str = ""
    base_model: str = ""
    training_method: str = ""  # SFT, DPO, PPO, DISTILL
    checkpoint_hash: str = ""
    num_parameters: int = 0
    is_delta: bool = False  # True for LoRA/adapters
    architecture: str = ""
    
    def __post_init__(self):
        if not self.type:
            object.__setattr__(self, 'type', ArtifactType.MODEL_CHECKPOINT)


@dataclass
class RewardDataManifest(ArtifactManifest):
    """
    Manifest for reward model training data.
    
    Includes:
    - num_pairs: number of preference pairs
    - labeling_method: how rewards were assigned
    - source_prompts_hash: hash of source prompts
    - quality_score: quality metric for the data
    """
    num_pairs: int = 0
    labeling_method: str = "programmatic"  # or "human", "ai_judge"
    source_prompts_hash: str = ""
    quality_score: float = 0.0
    data_hash: str = ""
    
    def __post_init__(self):
        if not self.type:
            object.__setattr__(self, 'type', ArtifactType.REWARD_DATA)


@dataclass
class IndexShardManifest(ArtifactManifest):
    """
    Manifest for RAG index shards.
    
    Includes:
    - index_type: type of index (ANN, BM25, etc.)
    - num_vectors: number of vectors indexed
    - dimension: vector dimension
    - source_data_hash: hash of source data
    - index_hash: hash of index file
    """
    index_type: str = "ann"  # or "bm25", "hybrid"
    num_vectors: int = 0
    dimension: int = 0
    source_data_hash: str = ""
    index_hash: str = ""
    metadata_hash: str = ""
    
    def __post_init__(self):
        if not self.type:
            object.__setattr__(self, 'type', ArtifactType.INDEX_SHARD)


def hash_artifact(manifest: ArtifactManifest) -> str:
    """
    Compute deterministic hash of an artifact manifest.
    
    Uses SHA3-256 of canonical JSON representation (excluding artifact_id).
    Returns hex-encoded hash string.
    """
    # Convert to dict and remove artifact_id for hashing
    d = manifest.to_dict()
    d.pop('artifact_id', None)  # Remove artifact_id field
    
    canonical_json = json.dumps(d, sort_keys=True, separators=(',', ':'))
    hash_bytes = hashlib.sha3_256(canonical_json.encode('utf-8')).digest()
    return hash_bytes.hex()


def verify_artifact(manifest: ArtifactManifest, expected_hash: Optional[str] = None) -> bool:
    """
    Verify artifact manifest integrity.
    
    Checks:
    - Manifest hash matches expected_hash (if provided)
    - Required fields are present and valid
    - Timestamps are valid
    - Hashes are properly formatted
    
    Returns True if valid, False otherwise.
    """
    try:
        # Check artifact_id matches content hash
        computed_hash = hash_artifact(manifest)
        if manifest.artifact_id != computed_hash:
            return False
        
        # Check against expected hash if provided
        if expected_hash and computed_hash != expected_hash:
            return False
        
        # Validate required fields
        if not manifest.created_by:
            return False
        
        if not manifest.type:
            return False
        
        # Validate timestamp format
        try:
            datetime.fromisoformat(manifest.created_at)
        except (ValueError, TypeError):
            return False
        
        # Type-specific validation
        if isinstance(manifest, DatasetManifest):
            if manifest.num_samples < 0:
                return False
            if manifest.shard_index < 0 or manifest.total_shards < 1:
                return False
            if manifest.shard_index >= manifest.total_shards:
                return False
        
        elif isinstance(manifest, EvalReportManifest):
            if manifest.total_score < 0 or manifest.total_score > 100:
                return False
            if manifest.pass_rate < 0 or manifest.pass_rate > 1:
                return False
            if manifest.num_tasks < 0:
                return False
        
        elif isinstance(manifest, ModelCheckpointManifest):
            if manifest.num_parameters < 0:
                return False
        
        elif isinstance(manifest, RewardDataManifest):
            if manifest.num_pairs < 0:
                return False
            if manifest.quality_score < 0 or manifest.quality_score > 1:
                return False
        
        elif isinstance(manifest, IndexShardManifest):
            if manifest.num_vectors < 0:
                return False
            if manifest.dimension < 0:
                return False
        
        return True
        
    except Exception:
        return False
