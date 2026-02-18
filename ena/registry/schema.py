"""
Schema definitions for ENA model registry.

All manifests are deterministic and CBOR-encodable for on-chain commitments.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any


class ModelType(str, Enum):
    """Model architecture type."""
    TEACHER = "teacher"  # Large model for training
    STUDENT = "student"  # Small CPU-optimized model for inference


class QuantizationType(str, Enum):
    """Quantization format."""
    FP32 = "fp32"
    FP16 = "fp16"
    INT8 = "int8"
    INT4 = "int4"
    GGUF_Q4_0 = "gguf_q4_0"
    GGUF_Q8_0 = "gguf_q8_0"


@dataclass
class ArtifactHashes:
    """Cryptographic hashes of model artifacts."""
    weights: str  # SHA256 of model weights file
    tokenizer: str  # SHA256 of tokenizer files
    config: str  # SHA256 of model config
    full_package: Optional[str] = None  # SHA256 of complete archive


@dataclass
class EvalMetrics:
    """Evaluation metrics for model quality."""
    accuracy: Optional[float] = None
    perplexity: Optional[float] = None
    bleu_score: Optional[float] = None
    toxicity_score: Optional[float] = None  # Lower is better
    refusal_rate: Optional[float] = None  # % of policy violations refused
    regression_pass_rate: Optional[float] = None  # % of regression tests passed
    
    # Custom metrics
    custom: Dict[str, float] = field(default_factory=dict)
    
    def meets_thresholds(self, thresholds: Dict[str, float]) -> bool:
        """Check if metrics meet minimum thresholds."""
        for metric_name, min_value in thresholds.items():
            if metric_name == "toxicity_score":
                # Lower is better for toxicity
                actual = getattr(self, metric_name, None)
                if actual is None or actual > min_value:
                    return False
            else:
                actual = getattr(self, metric_name, None)
                if actual is None or actual < min_value:
                    return False
        return True


@dataclass
class TrainingProvenance:
    """Training job provenance for auditability."""
    base_model: str  # Base model used for training
    dataset_hashes: List[str]  # DA commitment hashes of training datasets
    hyperparams: Dict[str, Any]  # Training hyperparameters
    eval_suite_hash: str  # Hash of evaluation suite used
    aicf_job_ids: List[str]  # AICF job IDs for this training run
    training_start: str  # ISO8601 timestamp
    training_end: str  # ISO8601 timestamp
    gpu_hours: Optional[float] = None  # Total GPU hours consumed
    cost_anm: Optional[int] = None  # Total cost in ANM base units


@dataclass
class RolloutPolicy:
    """Rollout and safety policy for model deployment."""
    canary_percent: float = 0.1  # Start with 10% traffic
    canary_duration_seconds: int = 3600  # 1 hour canary period
    min_calls_for_promotion: int = 100  # Min calls before promoting to 100%
    
    # Safety thresholds (model won't be promoted if violated)
    min_accuracy: Optional[float] = None
    max_perplexity: Optional[float] = None
    min_regression_pass_rate: float = 0.95  # 95% of regression tests must pass
    max_toxicity_score: float = 0.1  # Max 10% toxicity
    
    # Rollback thresholds (auto-rollback if violated after promotion)
    error_rate_threshold: float = 0.05  # 5% error rate triggers rollback
    latency_p99_ms: Optional[int] = None  # Max p99 latency


@dataclass
class ModelManifest:
    """
    Complete model manifest with all metadata.
    
    This is the canonical representation stored in DA and referenced on-chain.
    """
    model_id: str  # e.g., "ena"
    version: str  # Semver or epoch-based, e.g., "1.2.3" or "epoch-42"
    model_type: ModelType  # teacher or student
    quantization: QuantizationType
    
    # Artifacts
    artifact_hashes: ArtifactHashes
    artifact_urls: Dict[str, str]  # {artifact_name: DA_URL or HTTPS_URL}
    
    # Quality metrics
    eval_metrics: EvalMetrics
    
    # Provenance
    training_provenance: TrainingProvenance
    
    # Deployment policy
    rollout_policy: RolloutPolicy
    
    # Metadata
    created_at: str  # ISO8601 timestamp
    creator: str  # Address or identifier of creator
    description: str
    
    # On-chain reference
    da_commitment: Optional[str] = None  # DA commitment hash of this manifest
    onchain_tx_hash: Optional[str] = None  # Transaction that published this version
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)
    
    def compute_hash(self) -> str:
        """Compute deterministic hash of manifest (excluding da_commitment and onchain_tx_hash)."""
        d = self.to_dict()
        # Exclude fields that are set after hashing
        d.pop("da_commitment", None)
        d.pop("onchain_tx_hash", None)
        
        # Canonical JSON (sorted keys)
        canonical = json.dumps(d, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    
    @classmethod
    def from_dict(cls, data: dict) -> ModelManifest:
        """Create manifest from dictionary."""
        # Convert nested dicts to dataclasses
        data["model_type"] = ModelType(data["model_type"])
        data["quantization"] = QuantizationType(data["quantization"])
        data["artifact_hashes"] = ArtifactHashes(**data["artifact_hashes"])
        data["eval_metrics"] = EvalMetrics(**data["eval_metrics"])
        data["training_provenance"] = TrainingProvenance(**data["training_provenance"])
        data["rollout_policy"] = RolloutPolicy(**data["rollout_policy"])
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> ModelManifest:
        """Create manifest from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


def create_manifest_template(
    model_id: str,
    version: str,
    model_type: ModelType,
    creator: str,
    description: str
) -> ModelManifest:
    """Create a template manifest with minimal required fields."""
    now = datetime.utcnow().isoformat() + "Z"
    
    return ModelManifest(
        model_id=model_id,
        version=version,
        model_type=model_type,
        quantization=QuantizationType.FP32,
        artifact_hashes=ArtifactHashes(
            weights="",
            tokenizer="",
            config="",
        ),
        artifact_urls={},
        eval_metrics=EvalMetrics(),
        training_provenance=TrainingProvenance(
            base_model="",
            dataset_hashes=[],
            hyperparams={},
            eval_suite_hash="",
            aicf_job_ids=[],
            training_start=now,
            training_end=now,
        ),
        rollout_policy=RolloutPolicy(),
        created_at=now,
        creator=creator,
        description=description,
    )
