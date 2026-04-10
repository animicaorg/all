# AICF Protocol Specification

**Version:** 1.0-MVP  
**Status:** Initial Implementation  
**Last Updated:** 2026-02-18

## Executive Summary

The AICF (AI Compute Fund) Protocol transforms AICF from a simple treasury address into a complete on-chain system for GPU contributor redistribution. The protocol enables:

1. **Verifiable GPU Work**: Contributors submit training/eval job results with artifact commitments
2. **Fair Redistribution**: Epoch-based reward distribution proportional to verified work credits
3. **Challenge System**: MVP verification using challenge windows with extension points for zkML/TEE
4. **ENA Integration**: All ENA payments flow into the protocol and fund GPU infrastructure
5. **Model Releases**: Approved models are linked to contributing epochs and workers

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AICF Protocol Layer                              │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐          │
│  │  GPU Workers │  │ Training Jobs │  │ Work Submissions│          │
│  │  Registry    │  │  Specs        │  │  + Proofs      │          │
│  └──────────────┘  └───────────────┘  └────────────────┘          │
│                                                                      │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐          │
│  │ Challenges   │  │ Epochs        │  │ Credits        │          │
│  │ + Resolution │  │ Accounting    │  │ + Claims       │          │
│  └──────────────┘  └───────────────┘  └────────────────┘          │
│                                                                      │
│  ┌──────────────────────────────────────────────────────┐          │
│  │  AICF Inflows (ENA payments + other sources)         │          │
│  └──────────────────────────────────────────────────────┘          │
├─────────────────────────────────────────────────────────────────────┤
│                        RPC Interface                                 │
│  15 JSON-RPC methods: register, submit, challenge, claim, etc.     │
└─────────────────────────────────────────────────────────────────────┘
         ▲                         ▲                        ▲
         │                         │                        │
   ┌─────┴─────┐          ┌────────┴────────┐      ┌──────┴───────┐
   │ GPU       │          │ Coordinator     │      │ ENA Service  │
   │ Workers   │          │ (optional)      │      │ Integration  │
   └───────────┘          └─────────────────┘      └──────────────┘
```

## Core Concepts

### 1. GPU Workers

**Definition:** Entities that contribute GPU compute for training/evaluation tasks.

**Registration:**
```python
aicf.protocol.registerWorker(
    address="anim1...",        # Payout address
    displayName="My GPU Farm", # Optional
    stakeAmount=1000000000,    # Optional stake in base units
    stakeTxHash="0x...",       # Optional stake transaction
    region="us-east-1",        # Optional region
)
```

**States:**
- `INACTIVE`: Registered but not active
- `ACTIVE`: Accepting jobs and earning credits
- `JAILED`: Temporarily suspended for SLA violations
- `BANNED`: Permanently banned for fraud

### 2. Training Jobs

**Definition:** Deterministic job specifications for training/eval tasks.

**Structure:**
```python
{
    "jobId": "job_abc123",
    "specHash": "0x...",           # Commitment to full spec
    "datasetCommit": "0x...",      # Dataset commitment
    "jobType": "TRAINING",         # TRAINING | EVAL | FINETUNE
    "difficulty": 5,               # Difficulty multiplier
    "rewardWeight": 100,           # Credits per unit completion
    "expiresAt": 1234567890,       # Expiry timestamp
    "creator": "anim1..."          # Admin/governance address
}
```

**Job Spec Details (off-chain, referenced by specHash):**
```json
{
    "model": "ena.tiny.v1",
    "task": "finetune",
    "dataset_url": "da://dataset_abc",
    "hyperparameters": {
        "learning_rate": 0.0001,
        "batch_size": 32,
        "epochs": 3,
        "seed": 42
    },
    "evaluation": {
        "test_set": "da://testset_xyz",
        "metrics": ["loss", "perplexity"]
    },
    "verification_tasks": {
        "checkpoints": [0.25, 0.5, 0.75, 1.0],
        "sample_size": 100
    }
}
```

### 3. Work Submissions

**Definition:** Worker attestations of completed work with artifact/proof commitments.

**Submission:**
```python
aicf.protocol.submitWork(
    jobId="job_abc123",
    workerId="worker_xyz",
    artifactCommit="0x...",     # Commitment to outputs
    metrics={                    # Evaluation results
        "loss": 0.234,
        "perplexity": 12.3,
        "checkpoints": [...]
    },
    proofCommit="0x...",        # Commitment to verification data
    currentHeight=10000,         # For challenge deadline calc
)
```

**Artifact Contents (off-chain, DA-stored):**
- Model delta (LoRA weights)
- Training logs
- Evaluation outputs
- Checkpoint samples

### 4. MVP Verification Model

**Approach:** Challenge window + probabilistic verification

**Process:**
1. **Submit:** Worker uploads artifacts to DA and posts commitments on-chain
2. **Challenge Window:** Anyone can download and verify artifacts (default: 100 blocks)
3. **Challenge:** Post challenge with evidence if fraud detected
4. **Resolution:** Governance/verifier set resolves challenge
5. **Accept/Reject:** Credits awarded only if no successful challenges

**Future Enhancements:**
- **zkML Proofs:** Zero-knowledge proofs of correct training execution
- **TEE Attestations:** SGX/SEV attestations of secure execution
- **VDF Proofs:** Verifiable Delay Functions for time-bound compute

### 5. Credits

**Definition:** Non-transferable accounting units for verified work.

**Minting:**
```python
# After successful verification
credits = job.rewardWeight * job.difficulty
aicf.protocol.awardCredits(epochId, workerId, credits)
```

**Properties:**
- Per-worker per-epoch accounting
- Cannot be transferred
- Proportional to difficulty and quality of work
- Reset after epoch finalization

### 6. Epochs

**Definition:** Time periods for batching rewards (default: 1000 blocks).

**Lifecycle:**
1. **Open:** Epoch is active, accepting inflows and awarding credits
2. **Finalize:** Close epoch, compute worker shares
3. **Claim:** Workers claim their share of epoch inflows

**Accounting:**
```python
epoch = {
    "epochId": 42,
    "startHeight": 42000,
    "endHeight": 43000,
    "inflowTotal": "10000000000",      # Total inflows (u256 string)
    "inflowEna": "9000000000",         # From ENA
    "inflowOther": "1000000000",       # From other sources
    "inflowForWorkers": "7000000000",  # 70% for workers
    "totalCredits": "500",             # Total credits issued
    "finalized": true,
    "merkleRoot": "0x..."              # Optional for large sets
}
```

### 7. Reward Split

**Configuration (basis points, total = 10000):**
```python
rewardSplit = {
    "gpuWorkersBp": 7000,  # 70% to GPU workers
    "treasuryBp": 2000,    # 20% to treasury
    "devBp": 500,          # 5% to dev fund
    "burnBp": 500,         # 5% burned
}
```

**Calculation (deterministic, round down):**
```python
inflowForWorkers = (inflowTotal * gpuWorkersBp) // 10000
workerShare = (inflowForWorkers * workerCredits) // totalCredits
```

### 8. Claims

**Process:**
```python
# 1. Finalize epoch
aicf.protocol.finalizeEpoch(epochId=42, endHeight=43000)

# 2. Create claim
result = aicf.protocol.claim(epochId=42, workerId="worker_xyz")

# 3. Process payout (off-chain treasury operation)
# Treasury sends workerShare to worker address
```

**Double-Claim Prevention:**
- Unique constraint on `(epochId, workerId)` in claims table
- Status tracking: PENDING → PAID

## RPC Methods

### Worker Management

#### `aicf.protocol.registerWorker`
Register as a GPU worker.

**Parameters:**
- `address` (required): Payout address
- `displayName` (optional): Display name
- `stakeAmount` (optional): Stake amount in base units
- `stakeTxHash` (optional): Stake transaction hash
- `region` (optional): Region/locale

**Returns:**
```json
{
    "workerId": "worker_abc123",
    "address": "anim1...",
    "status": "ACTIVE"
}
```

#### `aicf.protocol.listWorkers`
List registered workers.

**Parameters:**
- `status` (optional): Filter by status
- `offset` (optional, default: 0): Pagination offset
- `limit` (optional, default: 100): Page size

**Returns:**
```json
{
    "items": [...],
    "nextOffset": 100
}
```

### Job Management

#### `aicf.protocol.createJob`
Create a training/eval job (admin only).

**Parameters:**
- `specHash` (required): Job specification commitment
- `jobType` (optional, default: "TRAINING"): Job type
- `difficulty` (optional, default: 1): Difficulty multiplier
- `rewardWeight` (optional, default: 100): Credits per completion
- `datasetCommit` (optional): Dataset commitment
- `expiresAt` (optional): Expiry timestamp

**Returns:**
```json
{
    "jobId": "job_xyz",
    "specHash": "0x...",
    "status": "OPEN"
}
```

#### `aicf.protocol.listJobs`
List available jobs.

**Parameters:**
- `status` (optional): Filter by status
- `jobType` (optional): Filter by type
- `offset`, `limit`: Pagination

### Work Submission

#### `aicf.protocol.submitWork`
Submit completed work.

**Parameters:**
- `jobId` (required): Job ID
- `workerId` (required): Worker ID
- `artifactCommit` (required): Artifact commitment
- `metrics` (optional): Evaluation metrics JSON
- `proofCommit` (optional): Proof commitment
- `currentHeight` (required): Current block height

**Returns:**
```json
{
    "submissionId": "sub_abc",
    "jobId": "job_xyz",
    "status": "PENDING",
    "challengeDeadline": 10100
}
```

### Challenge System

#### `aicf.protocol.challengeSubmission`
Challenge a submission as invalid.

**Parameters:**
- `submissionId` (required): Submission to challenge
- `challengerAddress` (required): Challenger address
- `challengeDataCommit` (required): Evidence commitment

**Returns:**
```json
{
    "challengeId": "chal_abc",
    "submissionId": "sub_xyz",
    "status": "OPEN"
}
```

#### `aicf.protocol.resolveChallenge`
Resolve a challenge (admin/verifier only).

**Parameters:**
- `challengeId` (required): Challenge ID
- `status` (required): Resolution status (RESOLVED_VALID | RESOLVED_INVALID | EXPIRED)
- `resolutionCommit` (optional): Resolution evidence

### Epoch Management

#### `aicf.protocol.finalizeEpoch`
Close an epoch and compute rewards.

**Parameters:**
- `epochId` (required): Epoch to finalize
- `endHeight` (required): Ending block height

**Returns:**
```json
{
    "epochId": 42,
    "endHeight": 43000,
    "inflowTotal": "10000000000",
    "inflowForWorkers": "7000000000",
    "totalCredits": "500",
    "workerCount": 10,
    "finalized": true,
    "workerRewards": {
        "worker_1": "3500000000",
        "worker_2": "2100000000",
        ...
    }
}
```

#### `aicf.protocol.getEpoch`
Get epoch statistics.

**Parameters:**
- `epochId` (required): Epoch ID

### Claims

#### `aicf.protocol.claim`
Claim rewards for a finalized epoch.

**Parameters:**
- `epochId` (required): Epoch ID
- `workerId` (required): Worker ID
- `merkleProof` (optional): Merkle proof if using tree

**Returns:**
```json
{
    "claimId": "claim_abc",
    "epochId": 42,
    "workerId": "worker_xyz",
    "amount": "3500000000",
    "claimed": true,
    "status": "PENDING"
}
```

### Status

#### `aicf.protocol.getStatus`
Get protocol configuration and status.

**Returns:**
```json
{
    "params": {
        "epochLengthBlocks": 1000,
        "challengeWindowBlocks": 100,
        "minStake": "1000000000",
        "maxWorkers": 1000,
        "rewardSplit": {...}
    },
    "currentEpoch": 42,
    "totalWorkers": 15
}
```

## ENA Integration

### Payment Flow

1. **User calls ENA:** Pays service fee + AICF contribution
2. **ENA verifies:** Checks both transactions on-chain
3. **ENA records:** Deposits AICF amount into protocol
4. **Protocol tracks:** Attributes inflow to current epoch

### Integration Module

**Location:** `aicf/protocol/integration.py`

**Key Class:** `AICFProtocolRecorder`

**Usage:**
```python
from aicf.protocol.integration import create_protocol_recorder

recorder = create_protocol_recorder(
    db_path="./aicf_protocol.db",
    epoch_length_blocks=1000,
)

# After AICF payment verification
inflow_id = recorder.record_ena_deposit(
    amount=2500000,          # AICF portion
    tx_hash="0x...",
    block_height=10050,
    payer="anim1...",
    request_id="req_xyz",
)
```

## CLI Usage

### Protocol Status
```bash
# View protocol configuration
animica ena aicf protocol-status

# JSON output
animica ena aicf protocol-status --json
```

### Worker Registration
```bash
# Register as worker
animica ena aicf register-worker anim1... \
    --name "My GPU Farm" \
    --stake 100 \
    --region us-east-1

# List workers
animica ena aicf list-workers --status ACTIVE
```

### Claiming Rewards
```bash
# View epoch info
animica ena aicf epoch-info 42

# Claim rewards
animica ena aicf claim-rewards worker_abc123 42
```

## Security Considerations

### Determinism

**Critical:** All reward calculations must be deterministic across nodes.

**Implementation:**
- Integer arithmetic only (no floating point)
- Consistent rounding (always round down for payouts)
- Canonical serialization (CBOR or sorted JSON)
- Fixed-order operations

### Sybil Resistance

**Mechanisms:**
- Optional stake requirement
- Rate limiting per worker
- Reputation tracking
- Challenge system for fraud detection

### Replay Protection

**Submissions:**
- Unique `submissionId` per job/worker pair
- Nullifier tracking in database

**Claims:**
- Unique constraint on `(epochId, workerId)`
- Status tracking prevents double-payout

## Future Enhancements

### Phase 2: zkML Proofs
- Zero-knowledge proofs of training execution
- On-chain verification
- Remove challenge window (instant finality)

### Phase 3: TEE Integration
- Intel SGX / AMD SEV attestations
- Remote attestation verification
- Confidential computing support

### Phase 4: PoIES Integration
- GPU work proofs become part of block acceptance
- Useful work replaces hash grinding
- Aligned incentives for infrastructure

### Phase 5: Model Marketplace
- Published model releases
- Per-model usage tracking
- Revenue sharing with contributors

## References

- **Schema:** `aicf/db/schema_protocol.sql`
- **State Management:** `aicf/protocol/state.py`
- **Economics:** `aicf/protocol/economics.py`
- **RPC Methods:** `aicf/protocol/rpc.py`
- **Integration:** `aicf/protocol/integration.py`
- **Tests:** `aicf/protocol/tests/test_protocol.py`

---

**Last Updated:** 2026-02-18  
**Protocol Version:** 1.0-MVP  
**Chain Compatibility:** Animica v1.0+
