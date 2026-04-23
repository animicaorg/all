from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseSchema(BaseModel):
    # Allow domain fields like `model_provider` while keeping protection for
    # core BaseModel API names.
    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,
        protected_namespaces=("model_validate", "model_dump"),
    )

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        )


class AutonomyLevel(str, Enum):
    READONLY = "readonly"
    WORKSPACE = "workspace"
    OPERATOR = "operator"


class JobType(str, Enum):
    SCRAPE = "scrape"
    EXTRACT = "extract"
    CLEAN = "clean"
    DEDUPE = "dedupe"
    CLASSIFY = "classify"
    CHUNK = "chunk"
    LABEL = "label"
    EMBED = "embed"
    EVAL = "eval"
    VERIFY = "verify"
    SUMMARIZE = "summarize"
    INDEX = "index"
    DATASET_BUILD = "dataset_build"
    DATASET_CLEAN = "dataset_clean"
    TRAINING_RECORDS = "training_records"
    TRAIN_PREPARE = "train_prepare"


class JobStatus(str, Enum):
    PROPOSED = "proposed"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    VERIFIED = "verified"
    FAILED = "failed"
    REJECTED = "rejected"


class StorageConfig(BaseSchema):
    home: Path = Field(default_factory=lambda: Path("~/.animica/ena").expanduser())
    db_path: Optional[Path] = None
    artifacts_dir: Optional[Path] = None
    datasets_dir: Optional[Path] = None
    indexes_dir: Optional[Path] = None
    sessions_dir: Optional[Path] = None
    logs_dir: Optional[Path] = None
    manifests_dir: Optional[Path] = None


class NetworkPolicy(BaseSchema):
    allow_domains: List[str] = Field(default_factory=list)
    deny_domains: List[str] = Field(default_factory=list)
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


class ShellPolicy(BaseSchema):
    allow_shell: bool = False
    allow_destructive: bool = False
    allow_write_outside_workspace: bool = False
    approval_required: bool = True
    approved_prefixes: List[List[str]] = Field(default_factory=list)
    blocked_tokens: List[str] = Field(
        default_factory=lambda: [
            "rm ",
            "rm\t",
            "mkfs",
            "shutdown",
            "reboot",
            "poweroff",
            "dd ",
            "sudo ",
            ":(){",
        ]
    )


class RetryPolicy(BaseSchema):
    attempts: int = 2
    backoff_seconds: float = 0.5
    max_backoff_seconds: float = 4.0


class ModelProviderConfig(BaseSchema):
    provider: Literal["deterministic", "openai_compatible", "ollama", "stub"] = "deterministic"
    transport: Literal["fallback", "remote_api", "local_runtime"] = "fallback"
    model: str = "deterministic"
    endpoint: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env_vars: List[str] = Field(default_factory=list)
    max_tokens: int = 1024
    temperature: float = 0.2
    timeout_seconds: float = 30.0
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    enabled: bool = True
    headers: Dict[str, str] = Field(default_factory=dict)
    extra_body: Dict[str, Any] = Field(default_factory=dict)


class EmbeddingProviderConfig(BaseSchema):
    provider: Literal["disabled", "hashing", "openai_compatible", "ollama", "stub"] = "disabled"
    transport: Literal["fallback", "remote_api", "local_runtime"] = "fallback"
    model: str = ""
    endpoint: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env_vars: List[str] = Field(default_factory=list)
    dimensions: Optional[int] = None
    batch_size: int = 16
    timeout_seconds: float = 30.0
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    enabled: bool = True
    headers: Dict[str, str] = Field(default_factory=dict)
    extra_body: Dict[str, Any] = Field(default_factory=dict)


class EnaConfigModel(BaseSchema):
    version: str = "0.2"
    storage: StorageConfig = Field(default_factory=StorageConfig)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    shell: ShellPolicy = Field(default_factory=ShellPolicy)
    workspace: Path = Field(default_factory=Path.cwd)
    default_output_dir: Optional[Path] = None
    model_endpoint: Optional[str] = None
    mining_job_endpoint: Optional[str] = None
    semantic_search_backend: Literal["none", "hashing", "external"] = "hashing"
    log_level: str = "INFO"
    retention_days: int = 30
    json_output: bool = False
    studio_api_enabled: bool = True
    default_model_provider: str = "deterministic"
    default_embedding_provider: str = "disabled"
    default_worker_id: str = "local-worker"
    default_miner_address: Optional[str] = None
    aicf_db_path: Optional[Path] = None
    default_index_chunk_lines: int = 80
    default_index_overlap: int = 10
    model_providers: Dict[str, ModelProviderConfig] = Field(default_factory=dict)
    embedding_providers: Dict[str, EmbeddingProviderConfig] = Field(default_factory=dict)
    agent_retry_limit: int = 2
    agent_summary_model_provider: Optional[str] = None


class Citation(BaseSchema):
    source: str
    title: Optional[str] = None
    chunk_id: Optional[str] = None
    line_hint: Optional[str] = None
    score: float = 0.0


class ArtifactRecord(BaseSchema):
    artifact_id: str
    kind: str
    path: str
    sha256: str
    sha3_256: Optional[str] = None
    size_bytes: int
    created_at: str = Field(default_factory=utc_now_iso)
    source_uri: Optional[str] = None
    parent_artifact_id: Optional[str] = None
    manifest_path: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DatasetRecord(BaseSchema):
    dataset_id: str
    kind: str
    path: str
    row_count: int
    sha256: str
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionRecord(BaseSchema):
    session_id: str
    task: str
    status: str
    autonomy: AutonomyLevel = AutonomyLevel.WORKSPACE
    working_dir: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseSchema):
    chunk_id: str
    source: str
    title: Optional[str] = None
    excerpt: str
    score: float
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    retrieval_mode: str = "hybrid"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WebPageRecord(BaseSchema):
    record_type: str = "web_page"
    url: str
    canonical_url: str
    title: Optional[str] = None
    description: Optional[str] = None
    content_text: str = ""
    links: List[str] = Field(default_factory=list)
    headings: List[str] = Field(default_factory=list)
    status_code: int = 200
    mime_type: str = "text/plain"
    content_sha256: str
    fetched_at: str = Field(default_factory=utc_now_iso)
    depth: int = 0
    source_hash: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)


class ArticleRecord(WebPageRecord):
    record_type: str = "article"
    byline: Optional[str] = None
    published_at: Optional[str] = None


class DocPageRecord(WebPageRecord):
    record_type: str = "doc_page"
    section_count: int = 0


class FAQRecord(BaseSchema):
    record_type: str = "faq"
    source: str
    question: str
    answer: str
    content_sha256: str
    provenance: Dict[str, Any] = Field(default_factory=dict)


class CodeFileSummary(BaseSchema):
    record_type: str = "code_file_summary"
    path: str
    language: str
    line_count: int
    symbol_hints: List[str] = Field(default_factory=list)
    summary: str
    content_sha256: str
    provenance: Dict[str, Any] = Field(default_factory=dict)


class RepoChunk(BaseSchema):
    record_type: str = "repo_chunk"
    chunk_id: str
    path: str
    content: str
    start_line: int
    end_line: int
    content_sha256: str
    provenance: Dict[str, Any] = Field(default_factory=dict)


class EvaluationSample(BaseSchema):
    record_type: str = "evaluation_sample"
    sample_id: str
    prompt: str
    expected: Optional[str] = None
    category: str = "general"
    citations: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TrainingSample(BaseSchema):
    record_type: str = "training_sample"
    sample_id: str
    task_type: str
    input_text: str
    output_text: str
    quality_score: float = 1.0
    source_refs: List[str] = Field(default_factory=list)
    rejected: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentTrace(BaseSchema):
    session_id: str
    step_index: int
    action: str
    status: str
    tool_name: Optional[str] = None
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class IndexRecord(BaseSchema):
    index_name: str
    root: str
    index_schema_version: str = "1.0"
    chunk_count: int = 0
    source_count: int = 0
    embedding_provider: str = "disabled"
    embedding_model: Optional[str] = None
    retrieval_mode: str = "keyword"
    manifest_artifact_id: Optional[str] = None
    chunk_manifest_artifact_id: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerificationCheck(BaseSchema):
    name: str
    passed: bool
    detail: Optional[str] = None
    score: Optional[float] = None


class VerificationRecord(BaseSchema):
    verification_id: str
    target_id: str
    target_type: str
    passed: bool
    score: float = 0.0
    checks: List[VerificationCheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class JobSpec(BaseSchema):
    manifest_version: str = "1.0"
    job_id: str = ""
    job_hash: str = ""
    job_type: JobType
    input_payload: Dict[str, Any]
    sources: List[str] = Field(default_factory=list)
    allowed_actions: List[str] = Field(default_factory=list)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    verification_rules: Dict[str, Any] = Field(default_factory=dict)
    scoring_rules: Dict[str, Any] = Field(default_factory=dict)
    reward_routing: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 50
    created_by: str = "local"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseSchema):
    job_id: str
    job_hash: str = ""
    job_type: JobType
    status: JobStatus = JobStatus.PROPOSED
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    claimed_by: Optional[str] = None
    aicf_task_id: Optional[str] = None
    spec: JobSpec
    result: Dict[str, Any] = Field(default_factory=dict)
    verification: Optional[VerificationRecord] = None
    reward: Dict[str, Any] = Field(default_factory=dict)


class ReceiptScoreComponent(BaseSchema):
    name: str
    score: float
    weight: float = 1.0
    detail: Optional[str] = None


class JobReceipt(BaseSchema):
    receipt_id: str
    receipt_version: str = "1.0"
    receipt_hash: str = ""
    job_id: str
    job_hash: str
    manifest_hash: str = ""
    job_type: JobType
    job_status: JobStatus
    aicf_task_id: Optional[str] = None
    aicf_job_kind: Optional[str] = None
    requester: str = "local"
    worker_id: Optional[str] = None
    provider_id: Optional[str] = None
    miner_address: Optional[str] = None
    verification_id: Optional[str] = None
    verification_hash: Optional[str] = None
    verification_passed: bool = False
    result_hash: str
    input_refs: List[str] = Field(default_factory=list)
    output_refs: List[str] = Field(default_factory=list)
    event_timestamps: Dict[str, str] = Field(default_factory=dict)
    source_hashes: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    artifact_hashes: Dict[str, str] = Field(default_factory=dict)
    score: float = 0.0
    score_components: List[ReceiptScoreComponent] = Field(default_factory=list)
    reward: Dict[str, Any] = Field(default_factory=dict)
    onchain_payload: Dict[str, Any] = Field(default_factory=dict)
    export_payload_hash: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DatasetSplitRecord(BaseSchema):
    split: Literal["train", "eval", "test"]
    path: str
    row_count: int
    sha256: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TrainingManifest(BaseSchema):
    manifest_version: str = "1.0"
    run_name: str
    backend: str
    base_model: str
    model_provider: Optional[str] = None
    output_dir: Optional[str] = None
    train: DatasetSplitRecord
    eval: Optional[DatasetSplitRecord] = None
    test: Optional[DatasetSplitRecord] = None
    hyperparameters: Dict[str, Any] = Field(default_factory=dict)
    launcher: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class TrainingRunRecord(BaseSchema):
    run_id: str
    status: Literal["prepared", "running", "completed", "failed", "exported"] = "prepared"
    backend: str
    manifest_path: str
    base_model: str
    output_dir: str
    resumed_from_run_id: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    command: List[str] = Field(default_factory=list)
    checkpoint_paths: List[str] = Field(default_factory=list)
    checkpoint_manifest: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    eval_report: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class TaskSpec(BaseSchema):
    task: str
    context_paths: List[str] = Field(default_factory=list)
    urls: List[str] = Field(default_factory=list)
    autonomy: AutonomyLevel = AutonomyLevel.WORKSPACE
    save_as: Optional[str] = None
    output_format: Literal["text", "json"] = "text"
    max_steps: int = 8
    model_provider: Optional[str] = None
    model: Optional[str] = None
    response_schema: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
