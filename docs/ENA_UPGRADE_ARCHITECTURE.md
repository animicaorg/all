# ENA Upgrade System - Technical Architecture

## Table of Contents

1. [System Design](#system-design)
2. [Component Architecture](#component-architecture)
3. [Data Flow](#data-flow)
4. [State Machine](#state-machine)
5. [Registry Design](#registry-design)
6. [Worker Architecture](#worker-architecture)
7. [Telemetry System](#telemetry-system)
8. [Security Considerations](#security-considerations)

## System Design

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ENA Upgrade System                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │   CLI        │───▶│  Coordinator │───▶│   AICF      │  │
│  │  (User)      │    │  (Workflow)  │    │  (Jobs)     │  │
│  └──────────────┘    └──────┬───────┘    └─────────────┘  │
│                              │                              │
│  ┌──────────────┐    ┌──────▼───────┐    ┌─────────────┐  │
│  │  Telemetry   │◄───│    State     │───▶│  Registry   │  │
│  │  (Metrics)   │    │   Machine    │    │  (Models)   │  │
│  └──────────────┘    └──────────────┘    └─────────────┘  │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │   Verifier   │    │ Safety Gates │    │   Workers   │  │
│  │  (Checks)    │    │ (Thresholds) │    │  (Compute)  │  │
│  └──────────────┘    └──────────────┘    └─────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
         │                      │                      │
         ▼                      ▼                      ▼
    On-Chain State       Filesystem Storage      AICF Network
```

### Design Principles

1. **Idempotency**: All operations can be safely retried
2. **Atomicity**: State transitions are atomic
3. **Resumability**: Workflow can resume from any state
4. **Auditability**: Complete audit trail of all actions
5. **Safety**: Multiple layers of verification and gates
6. **Transparency**: All costs and decisions are visible

### Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| CLI | Typer + Rich | User interface |
| Workflow | Python async | Orchestration |
| State | JSON + File locks | Persistence |
| Registry | Filesystem | Model storage |
| AICF | REST + JSON-RPC | Job submission |
| Crypto | liboqs (PQ) | Signatures |
| Telemetry | SQLite | Metrics collection |

## Component Architecture

### 1. Coordinator

**Responsibility**: Orchestrate the complete upgrade workflow

**Interface**:
```python
class UpgradeCoordinator:
    def __init__(
        self,
        state_machine: UpgradeStateMachine,
        registry: RegistryStorage,
        verifier: ResultVerifier,
        safety_gates: SafetyGates,
        work_dir: Path,
    ): ...
    
    # Workflow methods
    async def run_full_workflow(...) -> bool
    async def create_plan(...) -> TrainingPlan
    async def allocate_budget(...) -> str
    async def submit_jobs(...) -> Dict[str, str]
    async def monitor_progress(...) -> Dict[str, JobStatus]
    async def verify_results(...) -> VerificationResult
    async def publish_model(...) -> str
    async def rollout_canary(...) -> bool
    async def promote_canary(...) -> bool
    async def rollback(...) -> bool
```

**State Transitions**:
```python
# Managed via state machine
coordinator.state_machine.transition(
    from_state=UpgradeState.PLANNING,
    to_state=UpgradeState.ALLOCATING_BUDGET,
)
```

**Error Handling**:
```python
try:
    success = await coordinator.submit_jobs(plan)
except AICFConnectionError as e:
    # Retry with backoff
    coordinator.state_machine.record_error(str(e))
    await asyncio.sleep(delay)
    success = await coordinator.submit_jobs(plan)
```

### 2. State Machine

**Responsibility**: Track and persist workflow state

**Data Model**:
```python
@dataclass
class UpgradeStatus:
    # Identifiers
    upgrade_id: str
    model_id: str
    target_version: str
    
    # State
    current_state: UpgradeState
    plan_id: Optional[str]
    plan_hash: Optional[str]
    
    # Jobs
    job_statuses: Dict[str, JobStatus]
    
    # Budget
    budget_allocated: int
    budget_used: int
    
    # Errors
    errors: List[str]
    
    # Timestamps
    created_at: str
    updated_at: str
    
    # Artifacts
    published_manifest_hash: Optional[str]
    canary_started_at: Optional[str]
    previous_version: Optional[str]
```

**Persistence**:
```python
class UpgradeStateMachine:
    def _save(self) -> None:
        """Atomic save with file locking."""
        # Write to temp file
        temp_file = self.state_file.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(self.status.to_dict(), f, indent=2)
        
        # Atomic rename
        temp_file.replace(self.state_file)
        
        # Sync to disk
        os.sync()
```

**Concurrency Control**:
```python
from filelock import FileLock

class UpgradeStateMachine:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.lock_file = state_file.with_suffix('.lock')
        self.lock = FileLock(self.lock_file)
    
    def transition(self, from_state: UpgradeState, to_state: UpgradeState):
        with self.lock:
            if self.status.current_state != from_state:
                raise StateTransitionError(...)
            
            self.status.current_state = to_state
            self.status.updated_at = datetime.utcnow().isoformat() + "Z"
            self._save()
```

### 3. Verifier

**Responsibility**: Validate job outputs and check quality

**Architecture**:
```python
class ResultVerifier:
    """
    Three-stage verification:
    1. Artifact verification (hashes)
    2. Metrics validation (schema + values)
    3. Eval suite verification (approved list)
    """
    
    def verify_job_output(
        self,
        job_id: str,
        output_dir: Path,
        expected_artifacts: Dict[str, str],
        metrics: Dict[str, float],
        eval_suite_hash: str,
    ) -> VerificationResult:
        
        # Stage 1: Artifacts
        artifact_result = self.verify_artifacts(
            output_dir, expected_artifacts
        )
        if not artifact_result.passed:
            return artifact_result
        
        # Stage 2: Metrics
        metrics_result = self.verify_metrics(metrics)
        if not metrics_result.passed:
            return metrics_result
        
        # Stage 3: Eval suite
        eval_result = self.verify_eval_suite(eval_suite_hash)
        if not eval_result.passed:
            return eval_result
        
        return VerificationResult(passed=True)
```

**Hash Verification**:
```python
import hashlib

def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of file."""
    hasher = hashlib.sha256()
    
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    
    return hasher.hexdigest()

def verify_artifact_hash(
    artifact_path: Path,
    expected_hash: str,
) -> VerificationResult:
    actual_hash = compute_sha256(artifact_path)
    
    if actual_hash != expected_hash:
        return VerificationResult(
            passed=False,
            reason=f"Hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    
    return VerificationResult(passed=True)
```

### 4. Safety Gates

**Responsibility**: Enforce quality thresholds

**Configuration**:
```python
@dataclass
class SafetyGates:
    min_accuracy: float = 0.9
    max_perplexity: float = 3.0
    max_toxicity_score: float = 0.1
    min_regression_pass_rate: float = 0.95
    custom_thresholds: Dict[str, Tuple[str, float]] = field(default_factory=dict)
```

**Checking Logic**:
```python
def passes_all_gates(
    self,
    metrics: EvalMetrics,
) -> Tuple[bool, List[str]]:
    """Check all safety gates."""
    failures = []
    
    # Accuracy
    if metrics.accuracy < self.min_accuracy:
        failures.append(
            f"Accuracy {metrics.accuracy} < {self.min_accuracy}"
        )
    
    # Perplexity
    if metrics.perplexity > self.max_perplexity:
        failures.append(
            f"Perplexity {metrics.perplexity} > {self.max_perplexity}"
        )
    
    # Toxicity
    if metrics.toxicity_score > self.max_toxicity_score:
        failures.append(
            f"Toxicity {metrics.toxicity_score} > {self.max_toxicity_score}"
        )
    
    # Regression
    if metrics.regression_pass_rate < self.min_regression_pass_rate:
        failures.append(
            f"Regression {metrics.regression_pass_rate} < {self.min_regression_pass_rate}"
        )
    
    # Custom thresholds
    for metric_name, (comparison, threshold) in self.custom_thresholds.items():
        value = metrics.custom.get(metric_name)
        if value is None:
            failures.append(f"Missing custom metric: {metric_name}")
            continue
        
        if comparison == "min" and value < threshold:
            failures.append(f"{metric_name} {value} < {threshold}")
        elif comparison == "max" and value > threshold:
            failures.append(f"{metric_name} {value} > {threshold}")
    
    passed = len(failures) == 0
    return passed, failures
```

### 5. Registry

**Responsibility**: Store and manage model versions

**Directory Structure**:
```
registry/
├── ena/
│   ├── manifests/
│   │   ├── 1.0.0.json
│   │   ├── 1.5.0.json
│   │   └── 2.0.0.json
│   ├── artifacts/
│   │   ├── abc123.../         # Hash-based storage
│   │   │   ├── model.bin
│   │   │   ├── tokenizer.json
│   │   │   └── config.json
│   │   └── def456.../
│   ├── pinned.txt             # Current active version
│   └── versions.json          # Version list
└── other_model/
    └── ...
```

**Manifest Schema**:
```python
@dataclass
class ModelManifest:
    # Identity
    model_id: str
    version: str
    created_at: str
    creator: str
    
    # Metadata
    description: str
    base_model: str
    model_type: ModelType
    quantization: QuantizationType
    
    # Artifacts (content-addressable)
    artifact_hashes: ArtifactHashes
    
    # Quality metrics
    eval_metrics: EvalMetrics
    
    # Provenance
    training_provenance: TrainingProvenance
    
    # Deployment
    rollout_policy: Optional[RolloutPolicy]
```

**Versioning**:
```python
from packaging import version

def compare_versions(v1: str, v2: str) -> int:
    """Compare semantic versions."""
    version1 = version.parse(v1)
    version2 = version.parse(v2)
    
    if version1 < version2:
        return -1
    elif version1 > version2:
        return 1
    else:
        return 0

def get_latest_version(versions: List[str]) -> str:
    """Get latest version from list."""
    return max(versions, key=lambda v: version.parse(v))
```

## Data Flow

### Upgrade Workflow Data Flow

```
┌─────────────┐
│  CLI Input  │
│ (datasets,  │
│  version)   │
└──────┬──────┘
       │
       ▼
┌────────────────┐
│ Training Plan  │  ← Hyperparameters, job specs
│  (JSON spec)   │
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│ AICF Job Queue │  ← Job submission
│  (on-chain)    │
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│  Worker Pool   │  ← Job assignment
│  (compute)     │
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│ Job Outputs    │  ← Model weights, metrics
│ (artifacts)    │
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│  Verification  │  ← Hash checks, safety gates
│   Pipeline     │
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│    Registry    │  ← Manifest + artifacts
│   Storage      │
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│ Canary Deploy  │  ← Traffic routing
│  (gradual)     │
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│  Production    │  ← Full rollout
│   Serving      │
└────────────────┘
```

### State Transitions

```
┌──────────┐
│   IDLE   │
└────┬─────┘
     │ create_upgrade()
     ▼
┌──────────┐
│ PLANNING │
└────┬─────┘
     │ create_plan()
     ▼
┌───────────────────┐
│ ALLOCATING_BUDGET │
└────┬──────────────┘
     │ allocate_budget()
     ▼
┌──────────────────┐
│ SUBMITTING_JOBS  │
└────┬─────────────┘
     │ submit_jobs()
     ▼
┌──────────────┐
│  MONITORING  │◄──────┐
└────┬─────────┘       │
     │                 │ poll
     ├─────────────────┘
     │ all_jobs_done()
     ▼
┌──────────────┐
│  VERIFYING   │
└────┬─────────┘
     │ verify_results()
     ▼
┌──────────────┐
│  PUBLISHING  │
└────┬─────────┘
     │ publish_model()
     ▼
┌──────────────┐
│    CANARY    │
└────┬─────────┘
     │ promote_canary()
     ▼
┌──────────────┐
│  COMPLETED   │
└──────────────┘

Any state ──error──▶ FAILED ──rollback──▶ ROLLED_BACK
```

## State Machine

### State Definition

```python
class UpgradeState(str, Enum):
    """All possible workflow states."""
    
    # Normal flow
    IDLE = "idle"                       # No upgrade active
    PLANNING = "planning"               # Creating training plan
    ALLOCATING_BUDGET = "allocating_budget"  # Locking AICF funds
    SUBMITTING_JOBS = "submitting_jobs"     # Submitting to queue
    MONITORING = "monitoring"           # Tracking job progress
    VERIFYING = "verifying"            # Checking outputs
    PUBLISHING = "publishing"          # Saving to registry
    CANARY = "canary"                  # Gradual rollout
    COMPLETED = "completed"            # Success!
    
    # Error states
    FAILED = "failed"                  # Workflow failed
    ROLLED_BACK = "rolled_back"        # Reverted to previous
```

### Valid Transitions

```python
VALID_TRANSITIONS = {
    UpgradeState.IDLE: [UpgradeState.PLANNING],
    UpgradeState.PLANNING: [UpgradeState.ALLOCATING_BUDGET, UpgradeState.FAILED],
    UpgradeState.ALLOCATING_BUDGET: [UpgradeState.SUBMITTING_JOBS, UpgradeState.FAILED],
    UpgradeState.SUBMITTING_JOBS: [UpgradeState.MONITORING, UpgradeState.FAILED],
    UpgradeState.MONITORING: [UpgradeState.VERIFYING, UpgradeState.FAILED],
    UpgradeState.VERIFYING: [UpgradeState.PUBLISHING, UpgradeState.FAILED],
    UpgradeState.PUBLISHING: [UpgradeState.CANARY, UpgradeState.FAILED],
    UpgradeState.CANARY: [UpgradeState.COMPLETED, UpgradeState.FAILED],
    UpgradeState.COMPLETED: [UpgradeState.IDLE],
    UpgradeState.FAILED: [UpgradeState.ROLLED_BACK, UpgradeState.IDLE],
    UpgradeState.ROLLED_BACK: [UpgradeState.IDLE],
}
```

### Resumability

Each state has a resume handler:

```python
class UpgradeStateMachine:
    def can_resume(self) -> bool:
        """Check if upgrade can resume from current state."""
        if not self.status:
            return False
        
        # Cannot resume from terminal states
        terminal_states = {
            UpgradeState.IDLE,
            UpgradeState.COMPLETED,
            UpgradeState.ROLLED_BACK,
        }
        
        return self.status.current_state not in terminal_states
    
    async def resume(self, coordinator: UpgradeCoordinator):
        """Resume upgrade from current state."""
        state = self.status.current_state
        
        if state == UpgradeState.PLANNING:
            # Re-create plan
            plan = await coordinator.create_plan(...)
        
        elif state == UpgradeState.ALLOCATING_BUDGET:
            # Check if budget was allocated
            if not self.status.budget_allocated:
                await coordinator.allocate_budget(...)
        
        elif state == UpgradeState.MONITORING:
            # Continue monitoring jobs
            await coordinator.monitor_progress()
        
        # ... etc for each state
```

## Registry Design

### Content-Addressable Storage

Artifacts are stored by hash:

```python
def store_artifact(
    registry_dir: Path,
    model_id: str,
    artifact_name: str,
    artifact_path: Path,
) -> str:
    """Store artifact and return hash."""
    
    # Compute hash
    artifact_hash = compute_sha256(artifact_path)
    
    # Create hash-based directory
    artifact_dir = registry_dir / model_id / "artifacts" / artifact_hash
    artifact_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy artifact
    dest = artifact_dir / artifact_name
    shutil.copy(artifact_path, dest)
    
    return artifact_hash
```

### Manifest Management

```python
class RegistryStorage:
    def save_manifest(
        self,
        manifest: ModelManifest,
    ) -> str:
        """Save manifest and return hash."""
        
        # Serialize to canonical JSON
        manifest_json = json.dumps(
            manifest.to_dict(),
            sort_keys=True,
            indent=2,
        )
        
        # Compute manifest hash
        manifest_hash = hashlib.sha256(
            manifest_json.encode('utf-8')
        ).hexdigest()[:16]
        
        # Save manifest file
        manifest_file = (
            self.registry_dir /
            manifest.model_id /
            "manifests" /
            f"{manifest.version}.json"
        )
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(manifest_file, 'w') as f:
            f.write(manifest_json)
        
        # Update versions list
        self._update_versions_list(manifest.model_id, manifest.version)
        
        return manifest_hash
```

### Version Pinning

```python
def pin_version(
    self,
    model_id: str,
    version: str,
) -> None:
    """Pin version as active."""
    
    # Verify version exists
    if not self.manifest_exists(model_id, version):
        raise VersionNotFoundError(...)
    
    # Write pinned version
    pinned_file = self.registry_dir / model_id / "pinned.txt"
    with open(pinned_file, 'w') as f:
        f.write(version)
    
    # Log pinning event
    logger.info(f"Pinned {model_id}@{version}")
```

## Worker Architecture

### Worker Interface

```python
class WorkerBase(ABC):
    """Base class for all workers."""
    
    @abstractmethod
    async def execute_job(
        self,
        job_spec: JobSpec,
    ) -> JobResult:
        """Execute a training job."""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check worker health."""
        pass
    
    @abstractmethod
    async def estimate_cost(
        self,
        job_spec: JobSpec,
    ) -> int:
        """Estimate job cost in ANM base units."""
        pass
```

### Training Worker

```python
class TrainWorker(WorkerBase):
    async def execute_job(
        self,
        job_spec: JobSpec,
    ) -> JobResult:
        """Execute SFT training job."""
        
        # 1. Download inputs
        base_model = await self.download_model(job_spec.base_model)
        datasets = await self.download_datasets(job_spec.dataset_hashes)
        
        # 2. Set up training
        trainer = self.create_trainer(
            model=base_model,
            datasets=datasets,
            hyperparams=job_spec.hyperparams,
        )
        
        # 3. Train with checkpointing
        for epoch in range(job_spec.hyperparams["epochs"]):
            trainer.train_epoch()
            
            # Save checkpoint
            if epoch % CHECKPOINT_INTERVAL == 0:
                self.save_checkpoint(trainer, epoch)
        
        # 4. Save final model
        output_dir = self.work_dir / job_spec.job_id / "outputs"
        trainer.save_model(output_dir)
        
        # 5. Upload artifacts
        artifact_hashes = await self.upload_artifacts(output_dir)
        
        # 6. Extract metrics
        metrics = self.extract_training_metrics(trainer)
        
        # 7. Sign results
        result_hash = self.compute_result_hash(artifact_hashes, metrics)
        signature = self.sign(result_hash)
        
        return JobResult(
            job_id=job_spec.job_id,
            status="completed",
            artifact_hashes=artifact_hashes,
            metrics=metrics,
            worker_signature=signature,
        )
```

### Eval Worker

```python
class EvalWorker(WorkerBase):
    async def execute_job(
        self,
        job_spec: JobSpec,
    ) -> JobResult:
        """Execute evaluation job."""
        
        # 1. Download model
        model = await self.download_model(job_spec.base_model)
        
        # 2. Load eval suite
        eval_suite = await self.load_eval_suite()
        
        # 3. Run evaluations
        results = {}
        for task in job_spec.hyperparams["tasks"]:
            results[task] = await self.evaluate_task(
                model=model,
                task=task,
                eval_suite=eval_suite,
            )
        
        # 4. Aggregate metrics
        metrics = self.aggregate_metrics(results)
        
        # 5. Save detailed results
        output_dir = self.work_dir / job_spec.job_id / "outputs"
        self.save_eval_results(results, output_dir)
        
        # 6. Upload and sign
        artifact_hashes = await self.upload_artifacts(output_dir)
        signature = self.sign(self.compute_result_hash(...))
        
        return JobResult(
            job_id=job_spec.job_id,
            status="completed",
            artifact_hashes=artifact_hashes,
            metrics=metrics,
            worker_signature=signature,
            eval_suite_hash=eval_suite.hash,
        )
```

## Telemetry System

### Data Collection

```python
class TelemetryCollector:
    """Collect telemetry data (opt-in only)."""
    
    def record_job_started(
        self,
        job_id: str,
        job_type: str,
    ):
        """Record job start event."""
        if not self.is_enabled():
            return
        
        event = {
            "event_type": "job_started",
            "job_id": self._anonymize(job_id),
            "job_type": job_type,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        self._store_event(event)
    
    def record_job_completed(
        self,
        job_id: str,
        duration_seconds: int,
        cost_anm: int,
        metrics: Dict[str, float],
    ):
        """Record job completion."""
        if not self.is_enabled():
            return
        
        event = {
            "event_type": "job_completed",
            "job_id": self._anonymize(job_id),
            "duration_seconds": duration_seconds,
            "cost_anm": cost_anm,
            "metrics": self._anonymize_metrics(metrics),
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        self._store_event(event)
```

### Privacy Preservation

```python
def _anonymize(self, identifier: str) -> str:
    """Anonymize identifier using HMAC."""
    return hmac.new(
        key=self.user_secret.encode(),
        msg=identifier.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()[:16]

def _anonymize_metrics(
    self,
    metrics: Dict[str, float],
) -> Dict[str, float]:
    """Anonymize metrics (keep values, remove identifiers)."""
    return {
        "accuracy": metrics.get("accuracy"),
        "perplexity": metrics.get("perplexity"),
        "duration": metrics.get("duration"),
        # NO user data, addresses, or private keys
    }
```

### Curation

```python
class TelemetryCurator:
    """Curate telemetry for submission."""
    
    def prepare_dataset(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Prepare telemetry dataset for export."""
        
        # Load events
        events = self.collector.load_events(start_date, end_date)
        
        # Aggregate statistics
        stats = self._compute_statistics(events)
        
        # Remove any remaining PII
        stats = self._strip_pii(stats)
        
        # Create dataset
        dataset = {
            "version": "1.0",
            "user_id": self.collector.user_id,  # Anonymous ID
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "event_count": len(events),
            "statistics": stats,
            "schema_version": "1.0",
        }
        
        return dataset
```

## Security Considerations

### Threat Model

1. **Malicious Workers**: Submit fake or corrupted results
2. **Replay Attacks**: Reuse old results for new jobs
3. **MITM Attacks**: Intercept or modify job specifications
4. **Unauthorized Access**: Access registry without permission
5. **Data Leakage**: Extract training data from models

### Mitigations

#### 1. Result Verification

```python
def verify_worker_signature(
    result: JobResult,
    worker_pubkey: bytes,
) -> bool:
    """Verify worker signed the result."""
    
    # Reconstruct message
    message = self._construct_result_message(
        job_id=result.job_id,
        artifact_hashes=result.artifact_hashes,
        metrics=result.metrics,
    )
    
    # Verify signature
    return verify_dilithium3_signature(
        message=message,
        signature=result.worker_signature,
        public_key=worker_pubkey,
    )
```

#### 2. Replay Prevention

```python
class ReplayProtection:
    """Prevent result replay attacks."""
    
    def __init__(self):
        self.used_results = set()  # In production: use database
    
    def check_and_mark(
        self,
        result_hash: str,
    ) -> bool:
        """Check if result was used before."""
        if result_hash in self.used_results:
            return False  # Already used
        
        self.used_results.add(result_hash)
        return True  # First use
```

#### 3. Access Control

```python
class RegistryAccess:
    """Control registry access."""
    
    def check_permission(
        self,
        user: str,
        action: str,
        resource: str,
    ) -> bool:
        """Check if user can perform action."""
        
        # Read: anyone
        if action == "read":
            return True
        
        # Write: only model creator
        if action == "write":
            manifest = self.load_manifest(resource)
            return user == manifest.creator
        
        # Pin: only admins
        if action == "pin":
            return user in self.admin_list
        
        return False
```

#### 4. Data Sanitization

```python
def sanitize_telemetry(
    telemetry: Dict[str, Any],
) -> Dict[str, Any]:
    """Remove sensitive data from telemetry."""
    
    # Allowed fields only
    allowed_fields = {
        "event_type",
        "job_type",
        "duration_seconds",
        "metrics",
        "timestamp",
    }
    
    sanitized = {
        k: v for k, v in telemetry.items()
        if k in allowed_fields
    }
    
    # Remove any nested sensitive data
    if "metrics" in sanitized:
        sanitized["metrics"] = {
            k: v for k, v in sanitized["metrics"].items()
            if k in ALLOWED_METRICS
        }
    
    return sanitized
```

### Best Practices

1. **Key Management**:
   - Use hardware wallets for creator keys
   - Rotate worker keys regularly
   - Separate signing and operational keys

2. **Network Security**:
   - Use TLS for all AICF communication
   - Validate SSL certificates
   - Use authenticated encryption

3. **Data Protection**:
   - Encrypt sensitive data at rest
   - Use secure deletion for artifacts
   - Implement access logging

4. **Incident Response**:
   - Monitor for anomalies
   - Have rollback procedures ready
   - Maintain audit trail

## Performance Optimization

### Parallelization

```python
async def submit_jobs_parallel(
    self,
    jobs: List[JobSpec],
) -> Dict[str, str]:
    """Submit multiple jobs in parallel."""
    
    # Create tasks
    tasks = [
        self.aicf_client.submit_job(job)
        for job in jobs
    ]
    
    # Execute concurrently
    results = await asyncio.gather(*tasks)
    
    # Map job IDs to AICF IDs
    return {
        job.job_id: aicf_id
        for job, aicf_id in zip(jobs, results)
    }
```

### Caching

```python
class ArtifactCache:
    """Cache frequently accessed artifacts."""
    
    def __init__(self, max_size_gb: int = 10):
        self.cache_dir = Path.home() / ".cache" / "ena"
        self.max_size = max_size_gb * 1024**3
    
    def get(self, artifact_hash: str) -> Optional[Path]:
        """Get artifact from cache."""
        cached_path = self.cache_dir / artifact_hash
        
        if cached_path.exists():
            # Update access time
            cached_path.touch()
            return cached_path
        
        return None
    
    def put(self, artifact_hash: str, artifact_path: Path):
        """Add artifact to cache."""
        # Evict if needed
        self._evict_if_needed(artifact_path.stat().st_size)
        
        # Copy to cache
        dest = self.cache_dir / artifact_hash
        shutil.copy(artifact_path, dest)
```

## See Also

- [Operator Guide](./ENA_UPGRADE.md) - Usage and commands
- [AICF Integration](./AICF_TRAINING.md) - Training job details
- [Implementation Summary](../ENA_UPGRADE_IMPLEMENTATION.md) - Development notes

## Appendix: Schemas

### TrainingPlan Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["plan_id", "model_id", "target_version", "jobs"],
  "properties": {
    "plan_id": {"type": "string"},
    "model_id": {"type": "string"},
    "target_version": {"type": "string"},
    "jobs": {
      "type": "array",
      "items": {"$ref": "#/definitions/JobSpec"}
    },
    "max_total_cost_anm": {"type": "integer"},
    "dataset_commitments": {
      "type": "array",
      "items": {"type": "string"}
    }
  }
}
```

### ModelManifest Schema

See `ena/registry/schema.py` for complete schema definition.

### State File Schema

See `ena/upgrade/state_machine.py` for complete schema definition.
