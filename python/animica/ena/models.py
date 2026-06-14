"""
animica.ena.models
==================

Typed schemas plus the deterministic hashing/ID helpers shared across the ENA
layer. We deliberately avoid a hard pydantic dependency here: dataclasses keep
``import animica.ena`` working with only the standard library, while heavy
deps (transformers/datasets/embedding providers) stay lazy in their own
modules.

Determinism contract (must stay stable — receipts/ids are anchored downstream):

* canonical JSON = ``json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)`` — mirrors ``capabilities/jobs/id.py`` fallback and
  ``aicf`` canonical encoding.
* all hashes are SHA3-256 (matches ``aicf.aitypes.training_receipt`` and the
  "SHA3 hash" wording in docs/ena).
* ``aicf_task_id`` is domain-separated SHA3-256 over the canonical payload so
  ENA-local ids never collide with other project hashes but remain a plain
  32-byte digest the AICF queue can consume.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# Schema versions
# ---------------------------------------------------------------------------

ENA_CONFIG_VERSION = "0.2"
RECEIPT_VERSION = "1"

# Domain tags for hash separation (keep stable).
_TASK_ID_TAG = b"animica/ena.task_id/v1"


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding used for every ENA hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha3_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha3_256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    """SHA3-256 hex of the canonical JSON encoding of ``obj``."""
    return sha3_hex(canonical_bytes(obj))


def derive_aicf_task_id(payload: Mapping[str, Any]) -> str:
    """AICF-compatible 32-byte task id (hex) derived from a job spec payload.

    ENA jobs are created off-chain, so the on-chain inputs (chain_id/height/
    tx_hash) used by ``capabilities/jobs/id.py`` aren't available. We instead
    domain-separate over the canonical payload — still a single SHA3-256
    digest the AICF queue/settlement path can key on 1:1.
    """
    return hashlib.sha3_256(_TASK_ID_TAG + canonical_bytes(payload)).hexdigest()


def new_uuid() -> str:
    return uuid.uuid4().hex


def now_ts() -> int:
    return int(time.time())


def _to_jsonable(value: Any) -> Any:
    """Recursively make dataclasses/sets jsonable for canonical hashing."""
    if isinstance(value, Schema):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, set):
        return sorted(_to_jsonable(v) for v in value)
    return value


# ---------------------------------------------------------------------------
# Base schema
# ---------------------------------------------------------------------------

@dataclass
class Schema:
    """Light dataclass base with stable dict/json conversion."""

    def to_dict(self) -> dict[str, Any]:
        return {k: _to_jsonable(v) for k, v in asdict(self).items()}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in fields})


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------

@dataclass
class ModelProviderConfig(Schema):
    name: str = "deterministic"
    provider: str = "deterministic"
    transport: str = "fallback"
    model: str = "deterministic"
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_key_env_vars: list[str] = field(default_factory=list)
    max_tokens: int = 1024
    temperature: float = 0.2
    timeout_seconds: float = 30.0
    retry_attempts: int = 2


@dataclass
class EmbeddingProviderConfig(Schema):
    name: str = "hashing"
    provider: str = "hashing"
    transport: str = "fallback"
    model: str = "legacy-hash"
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_key_env_vars: list[str] = field(default_factory=list)
    dimensions: Optional[int] = None
    batch_size: int = 16
    timeout_seconds: float = 30.0
    retry_attempts: int = 2


@dataclass
class NetworkConfig(Schema):
    allow_domains: list[str] = field(default_factory=list)
    deny_domains: list[str] = field(default_factory=list)
    max_requests: int = 100
    max_depth: int = 2
    size_limit_bytes: int = 2_000_000
    request_timeout_seconds: float = 20.0
    retries: int = 2
    backoff_seconds: float = 0.5
    rate_limit_per_domain_per_minute: int = 30
    user_agent: str = "Animica-ENA/0.2 (+https://animica.org)"
    respect_robots: bool = True
    allow_browser_automation: bool = False
    allow_login: bool = False


# ---------------------------------------------------------------------------
# Retrieval schemas
# ---------------------------------------------------------------------------

@dataclass
class Chunk(Schema):
    chunk_id: str
    index_name: str
    source: str
    ordinal: int
    text: str
    text_hash: str = ""
    embedding_dim: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IndexMeta(Schema):
    name: str
    embedding_provider: str
    embedding_model: str
    dimensions: int
    chunk_count: int = 0
    created_at: int = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult(Schema):
    chunk_id: str
    source: str
    text: str
    score: float
    mode: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dataset / training schemas
# ---------------------------------------------------------------------------

@dataclass
class SplitRecord(Schema):
    split: str
    path: str
    row_count: int
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrainingManifest(Schema):
    run_name: str
    backend: str
    base_model: str
    output_dir: str
    train: Optional[SplitRecord] = None
    eval: Optional[SplitRecord] = None
    test: Optional[SplitRecord] = None
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    launcher: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Back-compat fields for older consumers.
    train_dataset: Optional[str] = None
    train_sha256: Optional[str] = None


@dataclass
class TrainingRun(Schema):
    run_id: str
    status: str
    backend: str
    manifest_path: str
    base_model: str
    output_dir: str
    command: Optional[str] = None
    checkpoint_paths: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    eval_report: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    created_at: int = field(default_factory=now_ts)
    updated_at: int = field(default_factory=now_ts)
