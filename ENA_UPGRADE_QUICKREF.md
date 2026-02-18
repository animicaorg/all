# ENA Upgrade System - Quick Reference

## File Structure

```
ena/upgrade/
├── __init__.py              # Package exports
├── training_plan.py         # Job specs and training plans
├── state_machine.py         # Workflow state tracking (NEW)
├── verifier.py              # Result validation & safety gates (NEW)
├── coordinator.py           # Workflow orchestration (NEW)
└── test_integration.py      # Integration tests (NEW)

ena/registry/
├── __init__.py              # Package exports
├── schema.py                # Model manifest schema
├── storage.py               # Registry storage backend
└── versioning.py            # Version parsing

python/animica/cli/
├── ena.py                   # Main ENA CLI (UPDATED)
└── ena_upgrade.py           # Upgrade CLI commands (NEW)
```

## Quick Start

### Run a Full Upgrade

```bash
# Automatic upgrade with all steps
animica ena upgrade auto \
  --version 2.0.0 \
  --creator your_address \
  --datasets hash1,hash2,hash3 \
  --auto-promote

# Or without auto-promote (manual canary promotion)
animica ena upgrade auto \
  --version 2.0.0 \
  --creator your_address \
  --datasets hash1,hash2,hash3

# Then promote manually when ready
animica ena upgrade promote
```

### Monitor Progress

```bash
# Show current upgrade status
animica ena upgrade status

# Resume if interrupted
animica ena upgrade resume
```

### Registry Management

```bash
# List all model versions
animica ena upgrade registry list

# Show details for a version
animica ena upgrade registry show ena 2.0.0

# Pin a version as active
animica ena upgrade registry pin ena 2.0.0

# Check pinned version
animica ena upgrade registry pinned ena
```

## Python API

### Create Coordinator

```python
from pathlib import Path
from ena.upgrade import (
    UpgradeStateMachine,
    UpgradeCoordinator,
    ResultVerifier,
    SafetyGates,
)
from ena.registry.storage import RegistryStorage

# Setup
state_file = Path.home() / ".animica/ena/upgrade_state.json"
registry_dir = Path.home() / ".animica/ena/registry"
work_dir = Path.home() / ".animica/ena/work"

state_machine = UpgradeStateMachine(state_file)
registry = RegistryStorage(registry_dir)
verifier = ResultVerifier()
safety_gates = SafetyGates(
    min_accuracy=0.9,
    max_perplexity=3.0,
    max_toxicity_score=0.1,
    min_regression_pass_rate=0.95,
)

coordinator = UpgradeCoordinator(
    state_machine=state_machine,
    registry=registry,
    verifier=verifier,
    safety_gates=safety_gates,
    work_dir=work_dir,
)
```

### Run Full Workflow

```python
# Create upgrade
state_machine.create_upgrade(
    upgrade_id="upgrade_001",
    model_id="ena",
    target_version="2.0.0",
)

# Run full workflow
success = coordinator.run_full_workflow(
    model_id="ena",
    target_version="2.0.0",
    creator="your_address",
    dataset_hashes=["hash1", "hash2"],
    base_model="qwen2.5-coder-1.5b",
    auto_promote=True,
)
```

### Manual Workflow Steps

```python
# Step 1: Create plan
plan = coordinator.create_plan(
    model_id="ena",
    target_version="2.0.0",
    creator="your_address",
    dataset_hashes=["hash1", "hash2"],
)

# Step 2: Allocate budget
coordinator.allocate_budget(plan.max_total_cost_anm)

# Step 3: Submit jobs
job_ids = coordinator.submit_jobs(plan)

# Step 4: Monitor progress
statuses = coordinator.monitor_progress()

# Step 5: Verify results
result = coordinator.verify_results(plan, job_outputs, metrics)

# Step 6: Publish model
manifest = coordinator._create_manifest_from_plan(plan, metrics)
manifest_hash = coordinator.publish_model(manifest)

# Step 7: Rollout canary
coordinator.rollout_canary()

# Step 8: Promote canary
coordinator.promote_canary()
```

## State Machine

### States

```python
from ena.upgrade import UpgradeState

# Workflow states
UpgradeState.IDLE                # No upgrade in progress
UpgradeState.PLANNING            # Creating training plan
UpgradeState.ALLOCATING_BUDGET   # Allocating AICF funds
UpgradeState.SUBMITTING_JOBS     # Submitting to AICF queue
UpgradeState.MONITORING          # Monitoring job progress
UpgradeState.VERIFYING           # Verifying results
UpgradeState.PUBLISHING          # Publishing to registry
UpgradeState.CANARY              # Canary deployment
UpgradeState.COMPLETED           # Successfully completed
UpgradeState.FAILED              # Failed (can rollback)
UpgradeState.ROLLED_BACK         # Rolled back to previous
```

### Status Queries

```python
# Get current status
status = state_machine.get_status()

print(f"State: {status.current_state.value}")
print(f"Model: {status.model_id}")
print(f"Version: {status.target_version}")
print(f"Budget: {status.budget_allocated} / {status.budget_used}")

# Check if can resume
if state_machine.can_resume():
    print("Can resume from:", status.current_state.value)

# Get job statuses
for job_id, job_status in status.job_statuses.items():
    print(f"{job_id}: {job_status.state}")
```

## Safety Gates

### Configure Thresholds

```python
from ena.upgrade import SafetyGates

gates = SafetyGates(
    min_accuracy=0.90,              # Minimum 90% accuracy
    max_perplexity=3.0,             # Maximum perplexity of 3.0
    max_toxicity_score=0.10,        # Maximum 10% toxicity
    min_regression_pass_rate=0.95,  # 95% regression tests must pass
    custom_thresholds={
        "custom_metric": ("min", 0.8),  # Custom metric >= 0.8
    }
)
```

### Check Metrics

```python
from ena.registry.schema import EvalMetrics

metrics = EvalMetrics(
    accuracy=0.95,
    perplexity=2.5,
    toxicity_score=0.05,
    regression_pass_rate=0.98,
    custom={"custom_metric": 0.85},
)

# Check all gates
passed, failures = gates.passes_all_gates(metrics)

if not passed:
    print("Failed safety gates:")
    for failure in failures:
        print(f"  - {failure}")
```

## Registry Operations

### Load Manifest

```python
from ena.registry.storage import RegistryStorage
from pathlib import Path

registry = RegistryStorage(Path.home() / ".animica/ena/registry")

# Load by version
manifest = registry.load_manifest("ena", "2.0.0")

# Load by hash
manifest = registry.load_manifest_by_hash("22f544823d666c35")
```

### Pin Version

```python
# Pin version as active
registry.pin_version("ena", "2.0.0")

# Get pinned version
pinned = registry.get_pinned_version("ena")

# Get pinned manifest
manifest = registry.get_pinned_manifest("ena")
```

### List Versions

```python
# List all versions for a model
versions = registry.list_versions("ena")
print(versions)  # ['1.0.0', '1.1.0', '2.0.0']

# Get latest version
latest = registry.get_latest_version("ena")

# List all models
all_models = registry.list_all_models()
# {'ena': ['1.0.0', '2.0.0'], 'other': ['1.0.0']}
```

## Training Plan

### Create Plan

```python
from ena.upgrade.training_plan import create_default_training_plan

plan = create_default_training_plan(
    model_id="ena",
    target_version="2.0.0",
    creator="your_address",
    dataset_hashes=["hash1", "hash2"],
    base_model="qwen2.5-coder-1.5b",
)

print(f"Plan ID: {plan.plan_id}")
print(f"Jobs: {len(plan.jobs)}")
print(f"Max cost: {plan.max_total_cost_anm / 1e9} ANM")

# Get execution order
order = plan.get_execution_order()
print(f"Execution order: {order}")
```

### Custom Plan

```python
from ena.upgrade.training_plan import TrainingPlan, JobSpec, JobType
from datetime import datetime

# Create custom jobs
train_job = JobSpec(
    job_type=JobType.TRAIN_SFT,
    job_id="custom_train_001",
    base_model="custom-model",
    dataset_hashes=["hash1"],
    hyperparams={"lr": 1e-5, "epochs": 3},
    max_cost_anm=5_000_000_000,
)

eval_job = JobSpec(
    job_type=JobType.EVAL,
    job_id="custom_eval_001",
    base_model=f"output:{train_job.job_id}",
    hyperparams={"tasks": ["accuracy"]},
    max_cost_anm=500_000_000,
    depends_on=[train_job.job_id],
)

plan = TrainingPlan(
    plan_id="custom_plan_001",
    model_id="ena",
    target_version="2.0.0",
    jobs=[train_job, eval_job],
    max_total_cost_anm=10_000_000_000,
    dataset_commitments=["hash1"],
    created_at=datetime.utcnow().isoformat() + "Z",
    creator="your_address",
    description="Custom training pipeline",
)
```

## Verification

### Verify Artifacts

```python
from ena.upgrade import ResultVerifier
from pathlib import Path

verifier = ResultVerifier(approved_eval_suite_hash="expected_hash")

# Verify artifact hash
result = verifier.verify_artifact_hash(
    artifact_path=Path("model.bin"),
    expected_hash="abc123...",
)

if result.passed:
    print("Hash verified!")
else:
    print(f"Verification failed: {result.reason}")
```

### Verify Job Output

```python
result = verifier.verify_job_output(
    output_dir=Path("outputs/job_001"),
    expected_artifacts={
        "model.bin": "hash1",
        "tokenizer.json": "hash2",
    },
    metrics={"accuracy": 0.95},
    eval_suite_hash="expected_hash",
)
```

## Common Patterns

### Rollback on Failure

```python
try:
    success = coordinator.run_full_workflow(...)
    if not success:
        coordinator.rollback()
except Exception as e:
    logger.error(f"Workflow failed: {e}")
    coordinator.rollback()
```

### Resume After Crash

```python
# On restart, check if there's an upgrade in progress
if state_machine.can_resume():
    status = state_machine.get_status()
    
    # Resume based on state
    if status.current_state == UpgradeState.MONITORING:
        coordinator.monitor_progress()
        # ... continue workflow
```

### Dry Run

```python
# Create plan but don't execute
plan = coordinator.create_plan(...)

# Estimate cost
estimated_cost = plan.estimate_cost()
print(f"Estimated cost: {estimated_cost / 1e9} ANM")

# Show execution order
order = plan.get_execution_order()
for job_id in order:
    job = plan.get_job_by_id(job_id)
    print(f"{job_id}: {job.job_type.value}")
```

## Testing

```bash
# Run integration tests
python3 ena/upgrade/test_integration.py

# Expected output:
# Testing ENA Upgrade Workflow
# ============================================================
# ✓ All tests passed!
#
# Testing Safety Gates
# ============================================================
# ✓ Safety gate tests passed!
```

## Troubleshooting

### Check State File

```bash
cat ~/.animica/ena/upgrade_state.json | jq .
```

### Reset State

```bash
# Backup first!
mv ~/.animica/ena/upgrade_state.json ~/.animica/ena/upgrade_state.json.bak

# State machine will create new state on next upgrade
```

### View Logs

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Constants

```python
# ANM base units
ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units

# Default thresholds
DEFAULT_MIN_ACCURACY = 0.9
DEFAULT_MAX_PERPLEXITY = 3.0
DEFAULT_MAX_TOXICITY = 0.1
DEFAULT_MIN_REGRESSION_PASS_RATE = 0.95

# Canary defaults
DEFAULT_CANARY_PERCENT = 0.1  # 10%
DEFAULT_CANARY_DURATION = 3600  # 1 hour
```

## Links

- Implementation docs: `ENA_UPGRADE_IMPLEMENTATION.md`
- Training plan spec: `ena/upgrade/training_plan.py`
- Registry schema: `ena/registry/schema.py`
- Integration tests: `ena/upgrade/test_integration.py`
