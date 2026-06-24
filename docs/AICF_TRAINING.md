# AICF Training Integration Guide

## Table of Contents

1. [Overview](#overview)
2. [Training Job Submission](#training-job-submission)
3. [Job Types & Specifications](#job-types--specifications)
4. [Worker Registration](#worker-registration)
5. [Budget Allocation](#budget-allocation)
6. [Result Verification](#result-verification)
7. [Settlement & Payments](#settlement--payments)
8. [Monitoring & Debugging](#monitoring--debugging)

## Overview

The ENA upgrade system integrates with AICF (AI Compute Fund) to submit training jobs, manage budgets, and verify results. This guide covers the complete integration workflow.

### AICF Architecture

```
┌─────────────────┐
│  ENA Upgrade    │
│  Coordinator    │
└────────┬────────┘
         │ submit jobs
         ▼
┌─────────────────┐      ┌──────────────┐
│  AICF Queue     │◄────►│  Job Manager │
│  (on-chain)     │      └──────────────┘
└────────┬────────┘              │
         │                       │ assign
         ▼                       ▼
┌─────────────────┐      ┌──────────────┐
│  AICF Escrow    │      │   Workers    │
│  (smart         │      │  (compute    │
│   contract)     │      │   providers) │
└────────┬────────┘      └──────┬───────┘
         │                      │
         │ settle               │ submit results
         ▼                      ▼
┌─────────────────────────────────────┐
│  AICF Settlement                    │
│  (payments + proof verification)    │
└─────────────────────────────────────┘
```

### Integration Points

1. **Job Submission**: Coordinator → AICF Queue
2. **Budget Escrow**: Coordinator → AICF Escrow
3. **Job Assignment**: AICF → Registered Workers
4. **Result Submission**: Workers → AICF
5. **Verification**: Coordinator → Result Verification
6. **Settlement**: AICF → Payment Distribution

## Training Job Submission

### Job Lifecycle

```
PENDING → ASSIGNED → RUNNING → COMPLETED
                              ↓
                           FAILED
```

### Submission Flow

#### 1. Create Training Plan

```python
from ena.upgrade.training_plan import create_default_training_plan

plan = create_default_training_plan(
    model_id="ena",
    target_version="2.0.0",
    creator="anim1abc...",
    dataset_hashes=["hash1", "hash2", "hash3"],
    base_model="qwen2.5-coder-1.5b",
)

# Plan contains:
# - job_specs: List of JobSpec objects
# - dependencies: Job execution order
# - budget: Total ANM cost estimate
```

#### 2. Allocate Budget

```python
from ena.upgrade.coordinator import UpgradeCoordinator

coordinator = UpgradeCoordinator(...)

# Allocate funds from creator account to AICF escrow
tx_hash = coordinator.allocate_budget(
    amount_anm=plan.max_total_cost_anm
)

# Wait for confirmation
coordinator.wait_for_tx(tx_hash)
```

#### 3. Submit Jobs

```python
# Submit all jobs from plan
job_ids = coordinator.submit_jobs(plan)

# Returns mapping: job_spec_id → aicf_job_id
# {
#   "train_sft_001": "aicf_job_12345",
#   "eval_001": "aicf_job_12346",
#   ...
# }
```

### Job Specification Format

Jobs are submitted with full specifications:

```python
{
  "job_type": "train_sft",           # Type of training job
  "job_id": "train_001",             # Unique identifier
  "creator": "anim1abc...",          # Job submitter
  "escrow_amount": 5000000000,       # ANM locked in escrow
  
  # Input artifacts
  "base_model": "qwen2.5-coder-1.5b",
  "dataset_hashes": ["hash1", "hash2"],
  
  # Hyperparameters
  "hyperparams": {
    "learning_rate": 1e-5,
    "epochs": 3,
    "batch_size": 4,
    "warmup_steps": 100
  },
  
  # Output requirements
  "required_artifacts": [
    "model.bin",
    "tokenizer.json",
    "config.json",
    "training_log.json"
  ],
  
  # Dependencies
  "depends_on": [],  # Job IDs that must complete first
  
  # Deadlines
  "timeout_seconds": 43200,  # 12 hours
  "created_at": "2024-01-15T10:00:00Z"
}
```

## Job Types & Specifications

### 1. SFT (Supervised Fine-Tuning)

Train a model on supervised datasets.

**Specification**:
```python
from ena.upgrade.training_plan import JobSpec, JobType

sft_job = JobSpec(
    job_type=JobType.TRAIN_SFT,
    job_id="sft_001",
    base_model="qwen2.5-coder-1.5b",
    dataset_hashes=["dataset_hash_1", "dataset_hash_2"],
    hyperparams={
        "learning_rate": 1e-5,
        "epochs": 3,
        "batch_size": 4,
        "gradient_accumulation_steps": 8,
        "warmup_ratio": 0.1,
        "lr_scheduler_type": "cosine",
        "weight_decay": 0.01,
    },
    max_cost_anm=5_000_000_000,  # 5 ANM
)
```

**Worker Requirements**:
- GPU with ≥16GB VRAM (or CPU with ≥64GB RAM)
- PyTorch ≥2.0
- Transformers library
- Dataset access (via DA layer)

**Expected Outputs**:
- `model.bin` - Trained model weights
- `tokenizer.json` - Tokenizer configuration
- `config.json` - Model configuration
- `training_log.json` - Training metrics
- `checkpoints/` - Intermediate checkpoints (optional)

**Verification**:
- Output hash matches manifest
- Training log shows convergence
- Model loads successfully

### 2. Evaluation

Evaluate model quality on standardized tasks.

**Specification**:
```python
eval_job = JobSpec(
    job_type=JobType.EVAL,
    job_id="eval_001",
    base_model="output:sft_001",  # Use output from sft_001
    depends_on=["sft_001"],
    hyperparams={
        "tasks": [
            "accuracy",
            "perplexity", 
            "toxicity",
            "regression_suite"
        ],
        "num_samples": 1000,
        "batch_size": 16,
    },
    max_cost_anm=500_000_000,  # 0.5 ANM
)
```

**Worker Requirements**:
- Same as training (to load model)
- Evaluation datasets from DA
- Standard eval harness (lm-eval)

**Expected Outputs**:
- `metrics.json` - Evaluation metrics
- `results.json` - Per-sample results
- `eval_suite_hash.txt` - Hash of eval suite used

**Verification**:
- Metrics schema matches expected
- Eval suite hash matches approved list
- Results statistically valid

### 3. Distillation

Create smaller model from larger teacher.

**Specification**:
```python
distill_job = JobSpec(
    job_type=JobType.DISTILL,
    job_id="distill_001",
    base_model="output:sft_001",  # Teacher model
    depends_on=["sft_001"],
    hyperparams={
        "target_size": "0.5x",  # 50% of original
        "temperature": 2.0,
        "alpha": 0.5,  # KL divergence weight
        "epochs": 5,
        "batch_size": 8,
    },
    max_cost_anm=2_000_000_000,  # 2 ANM
)
```

**Worker Requirements**:
- GPU with ≥24GB VRAM (needs teacher + student)
- Distillation framework

**Expected Outputs**:
- `student_model.bin` - Distilled model
- `distillation_log.json` - Training metrics
- `compression_stats.json` - Size/quality tradeoff

**Verification**:
- Model size matches target
- Quality degradation within bounds
- Inference speed improvement verified

### 4. RLHF (Reinforcement Learning from Human Feedback)

Fine-tune with preference learning.

**Specification**:
```python
rlhf_job = JobSpec(
    job_type=JobType.TRAIN_RLHF,
    job_id="rlhf_001",
    base_model="output:sft_001",
    dataset_hashes=["preference_data_hash"],
    depends_on=["sft_001"],
    hyperparams={
        "reward_model": "output:reward_001",
        "ppo_epochs": 4,
        "learning_rate": 1e-6,
        "kl_penalty": 0.1,
    },
    max_cost_anm=8_000_000_000,  # 8 ANM
)
```

**Worker Requirements**:
- GPU with ≥32GB VRAM
- PPO implementation
- Reward model

**Expected Outputs**:
- `rlhf_model.bin` - Fine-tuned model
- `reward_curve.json` - Reward progression
- `kl_divergence.json` - KL tracking

## Worker Registration

### Become a Compute Provider

#### 1. Install Worker Software

```bash
# Install the Animica client (provides the `animica aicf` CLI used below)
pip install animica

# Or install with every optional extra (kitchen sink / operators)
pip install "animica[all]"

# Install ENA worker package
pip install animica-ena-worker

# Verify installation
ena-worker --version
```

**Which install do I need?**

- `pip install animica` — the complete client. Everything to mine, run a node,
  use the wallet, deploy Python contracts, run `animica up` (the unified miner:
  PoW + useful-work + GPU train/serve + Studio functions), and use the Studio
  SDK. The native CPU miner (`animica-fastpow`) is included **by default**. This
  is what most people want, and it provides the `animica aicf` commands used
  throughout this guide.
- `pip install "animica[all]"` — everything above **plus** every optional extra:
  Qt desktop-wallet QR codes, the full distributed Studio client (cloudpickle
  for closures + omni-sdk for on-chain ANM escrow), and all server/operator
  dependencies pinned. Use it if you want the kitchen sink or are running
  pool/API infrastructure. Quote the extras form (`"animica[all]"`) so
  zsh/macOS does not glob the brackets.

#### 2. Configure Worker

```bash
# Create config file
cat > ~/.animica/ena-worker/config.json <<EOF
{
  "worker_id": "worker_001",
  "worker_address": "anim1worker...",
  "capabilities": {
    "gpu": {
      "available": true,
      "vram_gb": 24,
      "cuda_version": "12.1"
    },
    "cpu": {
      "cores": 16,
      "ram_gb": 64
    },
    "storage_gb": 500
  },
  "supported_job_types": [
    "train_sft",
    "eval",
    "distill"
  ],
  "pricing": {
    "per_gpu_hour_anm": 100000000,  # 0.1 ANM/hr
    "per_cpu_hour_anm": 10000000    # 0.01 ANM/hr
  },
  "availability": {
    "max_concurrent_jobs": 2,
    "maintenance_window": null
  }
}
EOF
```

#### 3. Register On-Chain

```bash
# Register as compute provider
animica aicf worker register \
  --config ~/.animica/ena-worker/config.json

# Stake collateral (optional, for reputation)
animica aicf worker stake --amount 1000
```

#### 4. Start Worker

```bash
# Start worker daemon
ena-worker start --daemon

# Check status
ena-worker status

# View logs
tail -f ~/.animica/ena-worker/worker.log
```

### Worker Implementation

Workers must implement the job execution interface:

```python
from ena.workers import WorkerBase

class CustomWorker(WorkerBase):
    def execute_job(self, job_spec: JobSpec) -> JobResult:
        """Execute a training job."""
        
        # 1. Download inputs
        base_model = self.download_artifact(job_spec.base_model)
        datasets = [
            self.download_dataset(h) 
            for h in job_spec.dataset_hashes
        ]
        
        # 2. Execute training
        outputs = self.train(
            model=base_model,
            datasets=datasets,
            hyperparams=job_spec.hyperparams,
        )
        
        # 3. Upload outputs
        artifact_hashes = {
            name: self.upload_artifact(path)
            for name, path in outputs.items()
        }
        
        # 4. Return result
        return JobResult(
            job_id=job_spec.job_id,
            status="completed",
            artifact_hashes=artifact_hashes,
            metrics=self.extract_metrics(outputs),
            worker_signature=self.sign_result(...),
        )
```

## Budget Allocation

### Escrow Mechanism

Funds are locked in AICF smart contract:

```python
# Submit budget allocation transaction
from animica.client import AnimicaClient

client = AnimicaClient(rpc_url="http://localhost:8545")

# Create escrow
escrow_tx = client.aicf.create_escrow(
    creator="anim1abc...",
    amount=10_000_000_000,  # 10 ANM
    purpose="ena_upgrade_v2.0.0",
    jobs=["job_001", "job_002", "job_003"],
)

# Submit transaction
tx_hash = client.send_transaction(escrow_tx)

# Track escrow
escrow_id = client.aicf.get_escrow_id(tx_hash)
status = client.aicf.get_escrow_status(escrow_id)

print(f"Escrow {escrow_id}: {status.amount} locked")
```

### Escrow Lifecycle

```
CREATED → ACTIVE → SETTLING → SETTLED
              ↓
           EXPIRED (timeout)
```

**CREATED**: Funds locked, jobs not yet assigned
**ACTIVE**: Jobs assigned to workers
**SETTLING**: Jobs completed, calculating payments
**SETTLED**: Payments distributed

### Budget Tracking

```python
# Monitor budget usage
coordinator.get_budget_status()
# {
#   "allocated": 10000000000,
#   "committed": 7500000000,  # Assigned to jobs
#   "spent": 5000000000,      # Jobs completed
#   "remaining": 2500000000
# }
```

### Cost Optimization

Tips for reducing training costs:

1. **Use efficient base models**: Smaller base = less compute
2. **Optimize hyperparameters**: Fewer epochs, larger batch size
3. **Leverage caching**: Reuse evaluation results
4. **Schedule off-peak**: Lower worker pricing
5. **Use CPU where possible**: Evaluation can run on CPU

## Result Verification

### Verification Pipeline

```python
from ena.upgrade.verifier import ResultVerifier

verifier = ResultVerifier(
    approved_eval_suite_hash="expected_suite_hash"
)

# Verify job outputs
result = verifier.verify_job_output(
    job_id="train_001",
    output_dir=Path("outputs/train_001"),
    expected_artifacts={
        "model.bin": "expected_hash",
        "tokenizer.json": "expected_hash",
    },
    metrics={"accuracy": 0.95, "perplexity": 2.3},
    eval_suite_hash="suite_hash",
)

if result.passed:
    print("Verification passed!")
else:
    print(f"Failed: {result.reason}")
```

### Verification Checks

#### 1. Artifact Hash Verification

```python
# Each artifact must match expected hash
for artifact_name, expected_hash in expected_artifacts.items():
    actual_hash = compute_sha256(artifact_path)
    
    if actual_hash != expected_hash:
        raise VerificationError(
            f"Hash mismatch for {artifact_name}: "
            f"expected {expected_hash}, got {actual_hash}"
        )
```

#### 2. Metrics Validation

```python
from ena.registry.schema import EvalMetrics

# Validate schema
metrics = EvalMetrics(**raw_metrics)

# Check safety gates
if metrics.accuracy < safety_gates.min_accuracy:
    raise SafetyGateError("Accuracy below threshold")

if metrics.perplexity > safety_gates.max_perplexity:
    raise SafetyGateError("Perplexity above threshold")

if metrics.toxicity_score > safety_gates.max_toxicity_score:
    raise SafetyGateError("Toxicity above threshold")
```

#### 3. Eval Suite Verification

```python
# Ensure eval suite is approved
if eval_suite_hash not in approved_eval_suites:
    raise VerificationError(
        f"Eval suite {eval_suite_hash} not approved"
    )
```

#### 4. Worker Signature Verification

```python
# Verify worker signed the results
from animica.crypto import verify_signature

is_valid = verify_signature(
    message=result_hash,
    signature=worker_signature,
    public_key=worker_public_key,
)

if not is_valid:
    raise VerificationError("Invalid worker signature")
```

### Fraud Detection

Automated checks for malicious behavior:

- **Result Duplication**: Same result submitted for multiple jobs
- **Invalid Artifacts**: Corrupted or malformed files
- **Metric Manipulation**: Unrealistic or impossible metrics
- **Timing Anomalies**: Suspiciously fast completion
- **Signature Reuse**: Signatures used across jobs

## Settlement & Payments

### Settlement Trigger

After all jobs complete and verification passes:

```python
# Trigger settlement
settlement_tx = coordinator.settle_escrow(
    escrow_id="escrow_001",
    results=[
        {"job_id": "job_001", "worker": "anim1worker1...", "cost": 5000000000},
        {"job_id": "job_002", "worker": "anim1worker2...", "cost": 500000000},
        {"job_id": "job_003", "worker": "anim1worker1...", "cost": 2000000000},
    ],
)

# Wait for settlement
client.wait_for_tx(settlement_tx)
```

### Payment Distribution

```
Total Escrow: 10 ANM

Payments:
├─ Worker 1: 7 ANM (jobs 1 + 3)
├─ Worker 2: 0.5 ANM (job 2)
├─ AICF Fee: 0.75 ANM (10% platform fee)
└─ Refund:   1.75 ANM (unspent, back to creator)
```

### Payment Proof

Each payment includes on-chain proof:

```python
payment_proof = {
    "escrow_id": "escrow_001",
    "job_id": "job_001",
    "worker": "anim1worker1...",
    "amount": 5000000000,
    "tx_hash": "0xabc...",
    "block_height": 12345,
    "timestamp": "2024-01-15T14:30:00Z",
    "proof_hash": "proof_hash_123",
}
```

### Dispute Resolution

If verification fails:

1. **Automatic Rejection**: Invalid results rejected immediately
2. **Escrow Lock**: Disputed funds held in escrow
3. **Evidence Collection**: Logs and artifacts preserved
4. **Arbitration**: Manual review by AICF committee
5. **Resolution**: Payment or refund based on findings

## Monitoring & Debugging

### Job Monitoring

```bash
# List all jobs
animica aicf jobs list

# Get specific job
animica aicf jobs get job_001

# Watch job progress
watch -n 10 'animica aicf jobs get job_001 | jq .status'
```

### Worker Monitoring

```bash
# List active workers
animica aicf workers list

# Get worker info
animica aicf workers get anim1worker...

# Check worker reputation
animica aicf workers reputation anim1worker...
```

### Debugging Failed Jobs

```bash
# Get job logs
animica aicf jobs logs job_001

# Download artifacts for inspection
animica aicf jobs download job_001 --output /tmp/job_001

# Re-run verification locally
python3 -m ena.upgrade.verifier \
  --job-output /tmp/job_001 \
  --expected-artifacts expected.json
```

### Telemetry & Analytics

```python
# Collect training telemetry
from ena.telemetry import TelemetryCollector

collector = TelemetryCollector()

# Track job metrics
collector.record_job_started(job_id="job_001")
collector.record_job_completed(
    job_id="job_001",
    duration_seconds=3600,
    cost_anm=5000000000,
    metrics={"accuracy": 0.95},
)

# Export for analysis
telemetry = collector.export()
```

### Performance Metrics

Key metrics to track:

- **Job Completion Rate**: % of jobs that complete successfully
- **Average Duration**: Time from submission to completion
- **Cost Efficiency**: ANM spent vs. budget allocated
- **Worker Reliability**: Success rate per worker
- **Quality Metrics**: Model accuracy, perplexity trends

## Best Practices

### Job Submission

1. **Start Small**: Test with small jobs before large upgrades
2. **Set Realistic Budgets**: Add 20% buffer for variability
3. **Use Dependencies**: Chain jobs properly with `depends_on`
4. **Monitor Actively**: Check status every few minutes
5. **Plan for Failures**: Have rollback plan ready

### Budget Management

1. **Estimate Conservatively**: Overestimate costs by 20-30%
2. **Use Escrow**: Lock funds only for active jobs
3. **Track Spending**: Monitor budget vs. actual costs
4. **Release Unused**: Return unused funds promptly
5. **Audit Regularly**: Review cost efficiency monthly

### Security

1. **Verify Workers**: Check worker reputation before assignment
2. **Sign Results**: Always verify worker signatures
3. **Audit Artifacts**: Inspect outputs for tampering
4. **Use Timeouts**: Set reasonable job timeouts
5. **Monitor Anomalies**: Alert on suspicious patterns

### Efficiency

1. **Batch Jobs**: Submit multiple jobs at once when possible
2. **Reuse Models**: Cache intermediate models
3. **Parallelize**: Run independent jobs concurrently
4. **Optimize Data**: Compress datasets for faster downloads
5. **Profile Costs**: Track which job types are most expensive

## See Also

- [ENA Upgrade Guide](./ENA_UPGRADE.md) - Complete upgrade workflow
- [Architecture Document](./ENA_UPGRADE_ARCHITECTURE.md) - System design
- [AICF Documentation](./AICF.md) - AI Compute Fund overview
- [Worker Guide](../ena/workers/README.md) - Become a compute provider

## Support

For AICF-specific issues:
- Check AICF status: `animica aicf status`
- View documentation: `docs/AICF.md`
- Report issues: GitHub Issues with `aicf` label
