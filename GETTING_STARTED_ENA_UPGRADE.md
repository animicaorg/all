# Getting Started with ENA Upgrade System

## Quick Start (5 minutes)

### 1. Check that everything is installed

```bash
# Verify imports work
python3 -c "from ena.upgrade import *; print('✓ Ready')"
```

### 2. Run a test upgrade

```bash
# Run integration tests
python3 ena/upgrade/test_integration.py
```

### 3. Try the CLI

```bash
# Show help
python3 python/animica/cli/ena_upgrade.py --help

# Check status (should be empty)
python3 python/animica/cli/ena_upgrade.py status

# List registry (should be empty)
python3 python/animica/cli/ena_upgrade.py registry list
```

## First Upgrade Workflow

### Option 1: Full Automatic (One Command)

```bash
python3 python/animica/cli/ena_upgrade.py auto \
  --model-id ena \
  --version 1.0.0 \
  --creator test_creator \
  --datasets hash1,hash2 \
  --auto-promote
```

### Option 2: Manual Steps

```bash
# Step 1: Start upgrade
python3 python/animica/cli/ena_upgrade.py auto \
  --model-id ena \
  --version 1.0.0 \
  --creator test_creator \
  --datasets hash1,hash2

# Step 2: Check status anytime
python3 python/animica/cli/ena_upgrade.py status

# Step 3: If paused at canary, promote
python3 python/animica/cli/ena_upgrade.py promote

# Or rollback if needed
python3 python/animica/cli/ena_upgrade.py rollback
```

## Understanding the Output

When you run an upgrade, you'll see:

```
╭─────────────────────────────────────────╮
│         ENA Upgrade Workflow            │
│ Model: ena                              │
│ Version: 1.0.0                          │
│ Creator: test_creator                   │
╰─────────────────────────────────────────╯

⠋ Running upgrade workflow...

STUB: Budget allocation not yet implemented (AICF integration pending)
STUB: Job submission not yet implemented (AICF integration pending)
STUB: Job monitoring not yet implemented (AICF integration pending)
STUB: Canary rollout not yet implemented (traffic routing pending)
STUB: Canary promotion not yet implemented (traffic routing pending)

✓ Upgrade completed successfully!
```

**Note**: The "STUB" messages are expected - they show where AICF integration will go.

## Registry Operations

### List all models

```bash
python3 python/animica/cli/ena_upgrade.py registry list
```

Output:
```
ena
┌─────────┬────────┬──────────┬─────────────────────────┐
│ Version │ Pinned │ Type     │ Created                 │
├─────────┼────────┼──────────┼─────────────────────────┤
│ 1.0.0   │ ✓      │ student  │ 2024-12-...             │
└─────────┴────────┴──────────┴─────────────────────────┘
```

### Show version details

```bash
python3 python/animica/cli/ena_upgrade.py registry show ena 1.0.0
```

### Pin a version

```bash
python3 python/animica/cli/ena_upgrade.py registry pin ena 1.0.0
```

## Python API

### Complete Example

```python
import sys
import os
from pathlib import Path

# Add to path
sys.path.insert(0, os.getcwd())

from ena.upgrade import (
    UpgradeStateMachine,
    UpgradeCoordinator,
    ResultVerifier,
    SafetyGates,
)
from ena.registry.storage import RegistryStorage

# Setup directories
state_file = Path.home() / ".animica/ena/upgrade_state.json"
registry_dir = Path.home() / ".animica/ena/registry"
work_dir = Path.home() / ".animica/ena/work"

# Create components
state_machine = UpgradeStateMachine(state_file)
registry = RegistryStorage(registry_dir)
verifier = ResultVerifier()
safety_gates = SafetyGates(
    min_accuracy=0.9,
    max_perplexity=3.0,
    max_toxicity_score=0.1,
    min_regression_pass_rate=0.95,
)

# Create coordinator
coordinator = UpgradeCoordinator(
    state_machine=state_machine,
    registry=registry,
    verifier=verifier,
    safety_gates=safety_gates,
    work_dir=work_dir,
)

# Create upgrade
state_machine.create_upgrade(
    upgrade_id="my_upgrade_001",
    model_id="ena",
    target_version="1.0.0",
)

# Run full workflow
success = coordinator.run_full_workflow(
    model_id="ena",
    target_version="1.0.0",
    creator="my_address",
    dataset_hashes=["hash1", "hash2"],
    base_model="qwen2.5-coder-1.5b",
    auto_promote=True,
)

if success:
    print("✓ Upgrade complete!")
    
    # Check registry
    manifest = registry.load_manifest("ena", "1.0.0")
    print(f"Published: {manifest.model_id} v{manifest.version}")
else:
    print("✗ Upgrade failed")
```

## Common Tasks

### Check Current State

```python
status = state_machine.get_status()

if status:
    print(f"State: {status.current_state.value}")
    print(f"Model: {status.model_id} v{status.target_version}")
    print(f"Jobs: {len(status.job_statuses)}")
else:
    print("No upgrade in progress")
```

### Configure Safety Gates

```python
# Stricter gates
strict_gates = SafetyGates(
    min_accuracy=0.95,         # Higher accuracy required
    max_perplexity=2.0,        # Lower perplexity required
    max_toxicity_score=0.05,   # Lower toxicity allowed
    min_regression_pass_rate=0.98,  # More tests must pass
)

# Custom metrics
custom_gates = SafetyGates(
    min_accuracy=0.9,
    custom_thresholds={
        "code_quality": ("min", 0.9),
        "latency_ms": ("max", 100),
    }
)
```

### Manual Verification

```python
from ena.registry.schema import EvalMetrics

# Create metrics
metrics = EvalMetrics(
    accuracy=0.95,
    perplexity=2.5,
    toxicity_score=0.05,
    regression_pass_rate=0.98,
)

# Check safety gates
passed, failures = safety_gates.passes_all_gates(metrics)

if passed:
    print("✓ All safety gates passed")
else:
    print("✗ Failed gates:")
    for failure in failures:
        print(f"  - {failure}")
```

## File Locations

All state is stored in `~/.animica/ena/`:

```
~/.animica/ena/
├── upgrade_state.json       # Current workflow state
├── registry/
│   ├── manifests/
│   │   ├── ena_1.0.0.json
│   │   └── ...
│   └── pins.json
└── work/
    ├── plan_files/
    └── outputs/
```

## Troubleshooting

### View Current State

```bash
cat ~/.animica/ena/upgrade_state.json | python3 -m json.tool
```

### Reset State

```bash
# Backup first!
mv ~/.animica/ena/upgrade_state.json ~/.animica/ena/upgrade_state.json.bak

# Start fresh
python3 python/animica/cli/ena_upgrade.py status
```

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Next Steps

1. **Try the test workflow**: Run `python3 ena/upgrade/test_integration.py`
2. **Explore the CLI**: Try `python3 python/animica/cli/ena_upgrade.py --help`
3. **Read the docs**: Check `ENA_UPGRADE_IMPLEMENTATION.md` for details
4. **Use the API**: See examples in `ENA_UPGRADE_QUICKREF.md`

## AICF Integration (Coming Soon)

The following features are stubbed and will be implemented when AICF is integrated:

- ✅ Budget allocation (stub logs what would happen)
- ✅ Job submission (stub logs what would happen)
- ✅ Job monitoring (stub logs what would happen)
- ✅ Traffic routing (stub logs what would happen)

The rest of the system is **fully functional** and ready to use!

## Help

For more information:
- Implementation guide: `ENA_UPGRADE_IMPLEMENTATION.md`
- Quick reference: `ENA_UPGRADE_QUICKREF.md`
- Code documentation: Docstrings in source files
