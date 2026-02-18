# ENA Upgrade System - Operator Guide

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Installation & Setup](#installation--setup)
4. [Quick Start](#quick-start)
5. [Command Reference](#command-reference)
6. [Workflow Examples](#workflow-examples)
7. [Safety Features](#safety-features)
8. [Rollback Procedures](#rollback-procedures)
9. [Troubleshooting](#troubleshooting)
10. [Production Considerations](#production-considerations)

## Overview

The ENA Upgrade System provides a complete, automated workflow for training, verifying, and deploying new ENA model versions with safety guarantees and AICF integration.

### Key Features

- **Automated Workflow**: End-to-end orchestration from training to deployment
- **State Management**: Persistent state with resume capability
- **Safety Gates**: Quality thresholds and regression testing
- **AICF Integration**: Transparent job submission and budget tracking
- **Gradual Rollout**: Canary deployments with automatic rollback
- **Telemetry**: Opt-in performance monitoring and improvement
- **Registry**: Version management with content-addressable storage

### Workflow Overview

```
┌─────────────┐
│   PLANNING  │  Generate training plan
└──────┬──────┘
       │
┌──────▼──────────────┐
│ ALLOCATING_BUDGET   │  Allocate AICF funds
└──────┬──────────────┘
       │
┌──────▼──────────────┐
│  SUBMITTING_JOBS    │  Submit to AICF queue
└──────┬──────────────┘
       │
┌──────▼──────────────┐
│    MONITORING       │  Track job progress
└──────┬──────────────┘
       │
┌──────▼──────────────┐
│    VERIFYING        │  Check safety gates
└──────┬──────────────┘
       │
┌──────▼──────────────┐
│   PUBLISHING        │  Save to registry
└──────┬──────────────┘
       │
┌──────▼──────────────┐
│     CANARY          │  Gradual rollout
└──────┬──────────────┘
       │
┌──────▼──────────────┐
│    COMPLETED        │  Upgrade complete
└─────────────────────┘
```

## Architecture

The system consists of four main components:

### 1. State Machine

Tracks upgrade progress with persistent state:

- **States**: IDLE → PLANNING → ... → COMPLETED
- **Persistence**: JSON state file with atomic writes
- **Resumability**: Resume from any state after interruption
- **Job Tracking**: Individual job status with AICF IDs

### 2. Coordinator

Orchestrates the workflow:

- Creates training plans
- Submits jobs to AICF
- Monitors progress
- Verifies results
- Publishes to registry
- Manages deployments

### 3. Verifier

Validates results and checks quality:

- **Artifact Verification**: SHA256 hash checking
- **Metrics Validation**: Schema and threshold checks
- **Safety Gates**: Configurable quality thresholds
- **Regression Testing**: Backward compatibility checks

### 4. Registry

Manages model versions:

- **Content-Addressable**: Models identified by hash
- **Version Pinning**: Active version selection
- **Manifest Schema**: Standardized metadata
- **Storage**: Hierarchical directory structure

## Installation & Setup

### Prerequisites

- Python 3.8+
- Animica CLI installed
- AICF account with budget (for production)
- Sufficient disk space for models

### Install

```bash
# Install Animica with ENA support
pip install -e ".[ena]"

# Verify installation
animica ena upgrade --help
```

### Configuration

Set up directories:

```bash
# Default locations (automatic)
~/.animica/ena/upgrade_state.json  # State file
~/.animica/ena/registry/           # Model registry
~/.animica/ena/work/               # Working directory

# Override with environment variables
export ANIMICA_ENA_DIR=/custom/path
export ANIMICA_ENA_REGISTRY_DIR=/custom/registry
export ANIMICA_ENA_WORK_DIR=/custom/work
```

Configure safety thresholds (optional):

```bash
# Create config file
cat > ~/.animica/ena/config.json <<EOF
{
  "safety_gates": {
    "min_accuracy": 0.90,
    "max_perplexity": 3.0,
    "max_toxicity_score": 0.10,
    "min_regression_pass_rate": 0.95
  },
  "canary": {
    "initial_percent": 0.10,
    "duration_seconds": 3600,
    "auto_promote": false
  }
}
EOF
```

## Quick Start

### Run Demo

```bash
# Interactive demo (MOCK mode, ~2 minutes)
./scripts/ena_upgrade_demo.sh

# Keep demo data for inspection
./scripts/ena_upgrade_demo.sh --keep-data

# Verbose output
./scripts/ena_upgrade_demo.sh --verbose
```

### Simple Upgrade

```bash
# Full automatic workflow
animica ena upgrade auto \
  --version 2.0.0 \
  --creator your_address \
  --datasets hash1,hash2,hash3 \
  --auto-promote

# Monitor progress
animica ena upgrade status

# View logs
tail -f ~/.animica/ena/upgrade.log
```

### Manual Control

```bash
# Step 1: Plan
animica ena upgrade plan \
  --version 2.0.0 \
  --creator your_address \
  --datasets hash1,hash2

# Step 2: Review and approve plan
cat ~/.animica/ena/work/training_plan.json

# Step 3: Execute
animica ena upgrade execute

# Step 4: Monitor
animica ena upgrade status

# Step 5: Promote canary (when ready)
animica ena upgrade promote
```

## Command Reference

### Upgrade Commands

#### `animica ena upgrade auto`

Run fully automated upgrade workflow.

**Options**:
- `--model-id TEXT` - Model identifier (default: "ena")
- `--version TEXT` - Target version (required)
- `--creator TEXT` - Creator address (required)
- `--datasets TEXT` - Comma-separated dataset hashes (required)
- `--base-model TEXT` - Base model to fine-tune (default: "qwen2.5-coder-1.5b")
- `--auto-promote` - Automatically promote canary after validation
- `--dry-run` - Show what would happen without executing
- `--local-dev` - Use local mock mode for development

**Example**:
```bash
animica ena upgrade auto \
  --version 2.0.0 \
  --creator anim1abc... \
  --datasets hash1,hash2,hash3 \
  --auto-promote
```

#### `animica ena upgrade status`

Show current upgrade status.

**Output**:
- Current state
- Job statuses
- Budget tracking
- Errors (if any)
- Timestamps

**Example**:
```bash
$ animica ena upgrade status

Upgrade Status
══════════════════════════════════════════════════
Upgrade ID:      upgrade_1234567890
Model:           ena
Target Version:  2.0.0
Current State:   MONITORING
Created:         2024-01-15T10:30:00Z

Jobs:
  ✓ train_sft_001     [completed]  
  ⏳ eval_001         [running]
  ⏸  distill_001     [pending]

Budget:
  Allocated:   10.0 ANM
  Used:        5.2 ANM
  Remaining:   4.8 ANM
```

#### `animica ena upgrade resume`

Resume interrupted upgrade.

**Behavior**:
- Checks current state
- Continues from last checkpoint
- Re-submits failed jobs
- Preserves completed work

**Example**:
```bash
# After crash or interruption
animica ena upgrade resume
```

#### `animica ena upgrade promote`

Promote canary to 100% traffic.

**Prerequisites**:
- Upgrade in CANARY state
- No errors in canary phase
- Metrics within thresholds

**Example**:
```bash
animica ena upgrade promote
```

#### `animica ena upgrade rollback`

Rollback to previous version.

**Behavior**:
- Reverts pinned version
- Restores previous model
- Updates state to ROLLED_BACK
- Preserves upgrade artifacts

**Example**:
```bash
animica ena upgrade rollback
```

### Registry Commands

#### `animica ena registry list`

List all model versions.

**Options**:
- `--model-id TEXT` - Filter by model ID

**Example**:
```bash
$ animica ena registry list

Available Models
══════════════════════════════════════════════════
ena:
  • 1.0.0
  • 1.5.0
  • 2.0.0  [pinned]
```

#### `animica ena registry show`

Show manifest for a version.

**Arguments**:
- `model_id` - Model identifier
- `version` - Version to show

**Example**:
```bash
$ animica ena registry show ena 2.0.0

Model Manifest: ena@2.0.0
══════════════════════════════════════════════════
Created:      2024-01-15T10:45:00Z
Creator:      anim1abc...
Base Model:   qwen2.5-coder-1.5b
Type:         causal_lm
Quantization: none

Artifacts:
  model.bin:      sha256:abc123...
  tokenizer.json: sha256:def456...
  config.json:    sha256:ghi789...

Metrics:
  Accuracy:            95.2%
  Perplexity:          2.31
  Toxicity:            2.8%
  Regression Pass:     98.1%

Training:
  Plan:        plan_001
  Datasets:    hash1, hash2, hash3
  AICF Proof:  proof_abc...
```

#### `animica ena registry pin`

Pin a version as active.

**Arguments**:
- `model_id` - Model identifier
- `version` - Version to pin

**Example**:
```bash
animica ena registry pin ena 2.0.0
```

#### `animica ena registry pinned`

Show currently pinned version.

**Example**:
```bash
$ animica ena registry pinned ena
2.0.0
```

### Telemetry Commands

#### `animica ena telemetry enable`

Enable telemetry collection (opt-in).

**Example**:
```bash
animica ena telemetry enable
```

#### `animica ena telemetry disable`

Disable telemetry collection.

**Example**:
```bash
animica ena telemetry disable
```

#### `animica ena telemetry status`

Show telemetry configuration.

**Example**:
```bash
$ animica ena telemetry status

Telemetry: ENABLED
User ID:   anonymous_abc123
Data Dir:  ~/.animica/ena/telemetry
```

#### `animica ena data curate`

Prepare telemetry data for submission.

**Options**:
- `--dry-run` - Show what would be collected
- `--output PATH` - Export path

**Example**:
```bash
# Show collected data
animica ena data curate --dry-run

# Export for manual review
animica ena data curate --output /tmp/telemetry.json
```

## Workflow Examples

### Example 1: Basic Upgrade

```bash
# Run automated upgrade
animica ena upgrade auto \
  --version 1.1.0 \
  --creator anim1myaddr... \
  --datasets dataset_hash_1,dataset_hash_2 \
  --auto-promote

# Output:
# ✓ Created training plan (3 jobs, 10 ANM budget)
# ✓ Allocated budget from AICF
# ⏳ Submitted jobs to queue...
# ⏳ Monitoring progress (est. 30 minutes)...
# ✓ All jobs completed
# ✓ Verified results (accuracy: 95%)
# ✓ Published to registry
# ✓ Canary deployed (10% traffic)
# ⏳ Monitoring canary (1 hour)...
# ✓ Canary promoted to 100%
# ✓ Upgrade complete!
```

### Example 2: Gradual Rollout

```bash
# Upgrade without auto-promote
animica ena upgrade auto \
  --version 2.0.0 \
  --creator anim1myaddr... \
  --datasets hash1,hash2,hash3

# Wait in canary state for manual validation
# ... check metrics, test endpoints ...

# If satisfied, promote
animica ena upgrade promote

# If issues found, rollback
animica ena upgrade rollback
```

### Example 3: Resume After Failure

```bash
# Start upgrade
animica ena upgrade auto --version 2.0.0 ...

# (Network issue, process killed, etc.)

# Later, check status
animica ena upgrade status
# Output: Current State: MONITORING

# Resume from checkpoint
animica ena upgrade resume
# Output: Resuming from MONITORING state...
```

### Example 4: Custom Training Plan

```python
# Create custom plan programmatically
from ena.upgrade import create_custom_training_plan, JobSpec, JobType

plan = create_custom_training_plan(
    model_id="ena",
    target_version="2.1.0",
    creator="anim1myaddr...",
    jobs=[
        JobSpec(
            job_type=JobType.TRAIN_SFT,
            job_id="custom_train_001",
            base_model="custom-base-model",
            dataset_hashes=["hash1", "hash2"],
            hyperparams={"lr": 2e-5, "epochs": 5},
            max_cost_anm=8_000_000_000,
        ),
        JobSpec(
            job_type=JobType.EVAL,
            job_id="custom_eval_001",
            base_model="output:custom_train_001",
            depends_on=["custom_train_001"],
            max_cost_anm=1_000_000_000,
        ),
    ],
)

# Save plan
plan.save("custom_plan.json")

# Execute with CLI
# animica ena upgrade execute --plan custom_plan.json
```

## Safety Features

### Quality Gates

All models must pass configurable quality thresholds:

| Metric | Default Threshold | Description |
|--------|------------------|-------------|
| Accuracy | ≥ 90% | Model accuracy on eval suite |
| Perplexity | ≤ 3.0 | Language model perplexity |
| Toxicity | ≤ 10% | Toxic output percentage |
| Regression Pass Rate | ≥ 95% | Backward compatibility tests |

**Configuration**:
```python
# In config.json
{
  "safety_gates": {
    "min_accuracy": 0.92,           # Raise to 92%
    "max_perplexity": 2.5,          # Lower to 2.5
    "max_toxicity_score": 0.05,     # Lower to 5%
    "min_regression_pass_rate": 0.98 # Raise to 98%
  }
}
```

### Artifact Verification

All training outputs are verified:

1. **Hash Checking**: SHA256 hashes compared against manifest
2. **Signature Verification**: AICF job signatures validated
3. **Schema Validation**: Manifests checked against JSON schema
4. **Completeness**: All required artifacts present

### Gradual Rollout

Canary deployment minimizes risk:

1. **Initial Traffic**: 10% (configurable)
2. **Monitoring Period**: 1 hour (configurable)
3. **Metrics Tracking**: Error rates, latency, quality
4. **Automatic Rollback**: If thresholds exceeded
5. **Manual Promotion**: Operator approval required (unless `--auto-promote`)

### State Persistence

Workflow state is persistent and resumable:

- **Atomic Writes**: State updates are atomic
- **Checkpointing**: State saved after each transition
- **Idempotent**: Operations can be retried safely
- **Audit Trail**: Complete history of state changes

## Rollback Procedures

### Automatic Rollback

Triggered automatically if:

- Safety gates fail during verification
- Canary metrics exceed thresholds
- Critical errors during deployment

### Manual Rollback

```bash
# Rollback to previous version
animica ena upgrade rollback

# Verify rollback
animica ena registry pinned ena
# Output: 1.5.0 (previous version)

# Check service health
curl http://localhost:8000/v1/health
```

### Emergency Rollback

If CLI is unavailable:

```bash
# Manually pin previous version
echo "1.5.0" > ~/.animica/ena/registry/ena/pinned.txt

# Restart ENA service
systemctl restart ena-node

# Verify
curl http://localhost:8000/v1/models
```

### Rollback Limitations

**Cannot rollback if**:
- Previous version no longer in registry
- Registry directory corrupted
- State file missing

**Recovery**:
1. Restore from backup
2. Manually reconstruct registry
3. Force pin known-good version

## Troubleshooting

### Common Issues

#### Issue: "No upgrade in progress"

**Cause**: State file missing or corrupt

**Solution**:
```bash
# Check state file
cat ~/.animica/ena/upgrade_state.json

# If missing, create new upgrade
animica ena upgrade auto --version ...

# If corrupt, restore from backup
cp ~/.animica/ena/upgrade_state.json.backup \
   ~/.animica/ena/upgrade_state.json
```

#### Issue: "Budget allocation failed"

**Cause**: Insufficient AICF funds

**Solution**:
```bash
# Check AICF balance
animica aicf balance

# Deposit more funds
animica aicf deposit --amount 100

# Resume upgrade
animica ena upgrade resume
```

#### Issue: "Job stuck in MONITORING"

**Cause**: AICF job not progressing

**Solution**:
```bash
# Check AICF job status
animica aicf jobs list

# Check specific job
animica aicf jobs get <job_id>

# If failed, rollback and retry
animica ena upgrade rollback
```

#### Issue: "Safety gates failed"

**Cause**: Model quality below thresholds

**Solution**:
```bash
# Review metrics
animica ena upgrade status

# Check specific failure
cat ~/.animica/ena/work/verification_report.json

# Options:
# 1. Adjust training parameters and retry
# 2. Lower thresholds (if justified)
# 3. Investigate data quality
```

### Debug Mode

Enable verbose logging:

```bash
# Set log level
export ANIMICA_LOG_LEVEL=DEBUG

# Run command
animica ena upgrade status

# Or use Python logging
python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from ena.upgrade import UpgradeCoordinator
# ... debug code ...
"
```

### State Inspection

```bash
# View current state
cat ~/.animica/ena/upgrade_state.json | jq .

# View specific fields
jq '.current_state' ~/.animica/ena/upgrade_state.json
jq '.job_statuses' ~/.animica/ena/upgrade_state.json
jq '.errors' ~/.animica/ena/upgrade_state.json

# Watch for changes
watch -n 2 'jq ".current_state" ~/.animica/ena/upgrade_state.json'
```

### Logs

```bash
# Follow upgrade logs
tail -f ~/.animica/ena/upgrade.log

# Search for errors
grep ERROR ~/.animica/ena/upgrade.log

# View AICF integration logs
tail -f ~/.animica/ena/aicf.log
```

## Production Considerations

### Pre-Production Checklist

- [ ] AICF account funded with sufficient budget
- [ ] Datasets uploaded and committed to DA
- [ ] Base model downloaded and verified
- [ ] Registry backed up
- [ ] Safety thresholds configured
- [ ] Monitoring and alerting set up
- [ ] Rollback procedure tested
- [ ] Emergency contacts documented

### Resource Planning

**Storage**:
- Base model: ~3-6 GB
- Training checkpoints: ~10-20 GB per job
- Registry: ~500 MB per version
- Logs: ~100 MB per upgrade

**Compute (AICF)**:
- SFT training: 5-10 ANM (~6-12 hours)
- Evaluation: 0.5-1 ANM (~1-2 hours)
- Distillation: 2-5 ANM (~3-6 hours)

**Network**:
- Dataset upload: varies by size
- Model download: 3-6 GB
- AICF communication: minimal

### Monitoring

Key metrics to monitor:

```bash
# Upgrade progress
watch -n 10 'animica ena upgrade status'

# AICF job queue
watch -n 30 'animica aicf jobs list'

# Registry health
ls -lh ~/.animica/ena/registry/

# Service health (if deployed)
watch -n 5 'curl -s http://localhost:8000/v1/health | jq .'
```

### Backup Strategy

```bash
# Before upgrade
BACKUP_DIR="/backup/ena/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup registry
cp -r ~/.animica/ena/registry "$BACKUP_DIR/"

# Backup state
cp ~/.animica/ena/upgrade_state.json "$BACKUP_DIR/"

# Backup config
cp ~/.animica/ena/config.json "$BACKUP_DIR/"

# Verify backup
ls -lh "$BACKUP_DIR"
```

### High Availability

For production deployments:

1. **Registry Replication**: Sync registry across nodes
2. **State Redundancy**: Use distributed state store
3. **Load Balancing**: Multiple ENA service instances
4. **Monitoring**: Prometheus + Grafana
5. **Alerting**: PagerDuty or equivalent

### Security

- **Credentials**: Use hardware wallets for creator keys
- **AICF Budget**: Set spending limits
- **Access Control**: Restrict upgrade CLI to authorized users
- **Audit Logging**: Enable comprehensive logging
- **Network**: Use TLS for AICF communication

### Testing

Before production upgrades:

```bash
# 1. Test with mock mode
./scripts/ena_upgrade_demo.sh --keep-data

# 2. Test on devnet
animica ena upgrade auto \
  --version 2.0.0-rc1 \
  --creator <devnet_addr> \
  --datasets <test_hashes>

# 3. Test rollback
animica ena upgrade rollback

# 4. Test resume
# (kill process mid-upgrade, then resume)
```

## See Also

- [AICF Training Guide](./AICF_TRAINING.md) - Integration with AICF
- [Architecture Document](./ENA_UPGRADE_ARCHITECTURE.md) - Technical details
- [ENA Service Guide](./ENA.md) - ENA inference service
- [AICF Documentation](./AICF.md) - AI Compute Fund

## Support

- **Documentation**: `docs/`
- **Issues**: GitHub Issues
- **Discussions**: GitHub Discussions
- **Security**: See [SECURITY.md](../SECURITY.md)
