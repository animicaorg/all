# AICF Jobs Marketplace

## Overview

The AICF Jobs Marketplace enables miners to spend their AICF credits on AI/Quantum compute jobs that are executed by off-chain GPU workers. This document outlines the architecture and workflows for Phase 2 implementation.

## Status

**Phase 1 (Completed):** Credit minting infrastructure
- ✅ Credits automatically minted from block rewards
- ✅ State tracking and ledger
- ✅ RPC query methods
- ✅ CLI commands for credit management

**Phase 2 (Future):** Job marketplace implementation
- ⏳ Job submission and specification
- ⏳ Worker registration and claiming
- ⏳ Result verification and finalization
- ⏳ Escrow and payment routing

## Architecture (Planned)

### Job Lifecycle

```
1. Submit Job
   ↓
2. Job Queued (OPEN status)
   ↓
3. Worker Claims (ASSIGNED status)
   ↓
4. Worker Executes Off-Chain
   ↓
5. Worker Submits Results + Proofs
   ↓
6. Verification (Challenge Window)
   ↓
7. Finalize (COMPLETED status)
   ↓
8. Pay Workers from Escrow
```

### Job Types

#### Training Jobs
- **Input:** Dataset commit, hyperparameters, base model
- **Output:** Model delta (LoRA/adapter), training metrics
- **Verification:** Loss curves, validation metrics, artifact hashes
- **Duration:** Hours to days
- **Credits:** 1000-100000 (varies by model size)

#### Eval Jobs
- **Input:** Model commit, eval dataset, metrics config
- **Output:** Benchmark scores, confusion matrices
- **Verification:** Deterministic eval results (same inputs = same outputs)
- **Duration:** Minutes to hours
- **Credits:** 100-1000

#### Distillation Jobs
- **Input:** Teacher model, student model, distillation config
- **Output:** Distilled model, compression metrics
- **Verification:** Model size reduction, accuracy preservation
- **Duration:** Hours
- **Credits:** 500-5000

### Components

#### 1. Job Spec Management

```python
@dataclass
class JobSpec:
    spec_hash: str          # DA commitment to full spec
    job_type: str           # TRAINING, EVAL, DISTILL
    dataset_commit: str     # DA hash of dataset
    model_base: str         # Base model identifier
    hyperparams: Dict       # Training hyperparameters
    budget_credits: int     # Max credits to spend
    timeout_blocks: int     # Expiry deadline
    creator: str            # Submitter address
```

#### 2. Worker Registration

```python
@dataclass
class GPUWorker:
    worker_id: str
    address: str            # Payout address
    capabilities: Dict      # GPU specs, frameworks
    stake_amount: int       # Optional stake for SLA
    status: str             # ACTIVE, INACTIVE, JAILED
    region: str             # Geographic region
```

#### 3. Job Assignment

**Queue Priority:**
1. Highest credit budget
2. Earliest submission time
3. Job difficulty

**Worker Selection:**
1. Capability matching (GPU RAM, framework support)
2. Geographic proximity
3. Historical success rate
4. Available capacity

#### 4. Result Verification

**Challenge Window:**
- 100 blocks (configurable) after submission
- Anyone can challenge with counter-evidence
- Worker must respond within deadline
- Failed challenges slash challenger stake
- Valid challenges reject work and forfeit payment

**Verification Criteria:**
- Artifact hashes match commitments
- Metrics meet minimum thresholds
- Plan hash matches original job spec
- No evidence of tampering

#### 5. Payment Routing

**Escrow Flow:**
```
Job Submission
   ↓
Lock Credits (spent_total += budget)
   ↓
Job Execution
   ↓
Finalization
   ↓
Pay Worker(s) (from escrow)
   ↓
Release Unused (back to balance_total)
```

**Split Rules:**
- 85% to worker
- 10% to verifier pool
- 5% to protocol treasury

## RPC Methods (To Be Implemented)

### Job Submission

#### `aicf.submitJob`

Submit a new training/eval/distill job.

**Params:**
```json
{
  "job_spec": {
    "job_type": "TRAINING",
    "dataset_commit": "0x...",
    "model_base": "llama-7b",
    "hyperparams": {
      "learning_rate": 0.0001,
      "batch_size": 32,
      "epochs": 10
    },
    "budget_credits": 10000,
    "timeout_blocks": 1000
  },
  "creator_address": "0x..."
}
```

**Returns:**
```json
{
  "job_id": "job_abc123",
  "spec_hash": "0x...",
  "status": "OPEN",
  "escrow_locked": 10000
}
```

### Worker Operations

#### `aicf.claimJob`

Claim an available job.

**Params:**
```json
{
  "job_id": "job_abc123",
  "worker_id": "worker_xyz"
}
```

**Returns:**
```json
{
  "job_id": "job_abc123",
  "status": "ASSIGNED",
  "worker_id": "worker_xyz",
  "lease_expires_at": 5000
}
```

#### `aicf.submitResult`

Submit job results with proofs.

**Params:**
```json
{
  "job_id": "job_abc123",
  "worker_id": "worker_xyz",
  "artifact_commit": "0x...",
  "metrics": {
    "final_loss": 0.123,
    "validation_accuracy": 0.95
  },
  "proof_commit": "0x..."
}
```

**Returns:**
```json
{
  "submission_id": "sub_def456",
  "status": "PENDING",
  "challenge_deadline": 5100
}
```

### Finalization

#### `aicf.finalizeJob`

Finalize a job after challenge window.

**Params:**
```json
{
  "job_id": "job_abc123",
  "submission_id": "sub_def456"
}
```

**Returns:**
```json
{
  "job_id": "job_abc123",
  "status": "COMPLETED",
  "credits_paid": 10000,
  "payouts": [
    {"address": "0x...", "amount": 8500},
    {"address": "0x...", "amount": 1000},
    {"address": "0x...", "amount": 500}
  ]
}
```

## CLI Commands (To Be Implemented)

### Job Management

```bash
# List available jobs
animica aicf jobs list --status OPEN --type TRAINING

# Submit a training job
animica aicf jobs submit \
  --plan training_plan.json \
  --budget 10000 \
  --type train

# Watch job progress
animica aicf jobs watch job_abc123

# Get job details
animica aicf jobs get job_abc123
```

### Worker Operations

```bash
# Register as a worker
animica aicf worker register \
  --address anim1... \
  --stake 1000 \
  --capabilities gpu_specs.json

# Claim a job
animica aicf worker claim job_abc123

# Submit results
animica aicf worker submit-result \
  --job job_abc123 \
  --artifacts artifacts.tar.gz \
  --metrics metrics.json
```

## Security Considerations

### Attack Vectors

1. **Sybil Workers:** Multiple fake workers claim same job
   - **Mitigation:** Stake requirement, lease exclusivity

2. **Lazy Workers:** Claim jobs but don't execute
   - **Mitigation:** Lease timeouts, reputation tracking

3. **Fake Results:** Submit invalid artifacts
   - **Mitigation:** Challenge window, verifier incentives

4. **Credit Inflation:** Exploit minting logic
   - **Mitigation:** Deterministic splitting, replay safety

5. **Griefing:** Submit garbage jobs to waste credits
   - **Mitigation:** Minimum job quality checks, spam detection

### Safety Mechanisms

- **Escrow:** Credits locked before job execution
- **Challenge Window:** Time for verification
- **Slashing:** Penalties for dishonest behavior
- **Reputation:** Track worker success rates
- **Rate Limits:** Prevent spam submissions

## Data Availability Integration

### Job Specifications

- Full job spec stored on DA layer
- Only spec_hash stored on-chain
- Workers download spec from DA
- Deterministic spec reconstruction

### Artifacts

- Model deltas uploaded to DA
- Artifact hashes committed on-chain
- Verifiers can download and check
- Permanent archival of results

## Economics

### Credit Pricing

**Estimated Costs (in AICF credits):**
- Training tiny model (7B): 1000-5000
- Training medium model (13B): 10000-50000
- Training large model (70B): 100000-500000
- Eval benchmark: 100-1000
- Distillation: 500-5000

### Market Dynamics

- **Supply:** Credits minted from mining (10% of rewards)
- **Demand:** Jobs submitted by miners/developers
- **Price Discovery:** Job budgets compete for worker attention
- **Efficiency:** Workers optimize for credits-per-GPU-hour

## Future Enhancements

### Phase 3 (Advanced Features)

- **Federated Learning:** Multi-worker collaborative training
- **Privacy:** Zero-knowledge proofs for sensitive data
- **Cross-Chain:** Bridge AICF credits to other ecosystems
- **Governance:** Token-weighted job prioritization
- **Oracles:** External data feeds for verification

### Phase 4 (Ecosystem)

- **Marketplace UI:** Web interface for job browsing
- **Worker Dashboards:** Monitor earnings and reputation
- **Model Registry:** Catalog of trained models
- **Dataset Marketplace:** Buy/sell training data with credits
- **Automated Pipelines:** CI/CD for model development

## References

- **Phase 1 Documentation:** [AICF_MINING_FLOW.md](./AICF_MINING_FLOW.md)
- **State Schema:** `aicf/db/schema_protocol.sql`
- **Protocol State:** `aicf/protocol/state.py`
- **Economics:** `aicf/economics/split.py`
