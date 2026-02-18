# ENA Workers and Telemetry - Quick Reference

This document provides a quick reference for the ENA Workers and Telemetry implementation.

## Phase 6: Worker Support ✓

### Workers Implemented

| Worker | Purpose | Input | Output | Status |
|--------|---------|-------|--------|--------|
| **TrainingWorker** | Supervised fine-tuning | Base model + datasets | Fine-tuned model + metrics | ✓ MOCK |
| **EvaluationWorker** | Model evaluation | Model + eval suite | Evaluation metrics | ✓ MOCK |
| **DistillationWorker** | Knowledge distillation | Teacher model | Student model + GGUF | ✓ MOCK |

### Quick Start

```bash
# Training
python -m ena.workers.train_worker --job-spec job.json --mock

# Evaluation
python -m ena.workers.eval_worker --job-spec job.json --mock

# Distillation
python -m ena.workers.distill_worker --job-spec job.json --mock
```

### Docker

```bash
# Build
docker build -f ena/workers/Dockerfile.worker -t ena-worker:gpu .

# Run
docker run --gpus all -v $(pwd)/jobs:/jobs -v $(pwd)/output:/output \
  ena-worker:gpu python -m ena.workers.train_worker \
  --job-spec /jobs/train.json --output-dir /output --mock
```

### Example Job Specs

**Training:**
```json
{
  "job_id": "train_001",
  "job_type": "ena.train.sft",
  "base_model": "da://abc123...",
  "dataset_hashes": ["da://def456..."],
  "hyperparams": {
    "learning_rate": 2e-5,
    "batch_size": 4,
    "epochs": 3,
    "lora_r": 8
  }
}
```

**Evaluation:**
```json
{
  "job_id": "eval_001",
  "job_type": "ena.eval",
  "model_hash": "da://abc123...",
  "eval_suite_hash": "da://def456...",
  "eval_tasks": ["accuracy", "perplexity", "toxicity", "regression"]
}
```

**Distillation:**
```json
{
  "job_id": "distill_001",
  "job_type": "ena.distill.cpu",
  "teacher_model_hash": "da://abc123...",
  "student_config": {
    "hidden_size": 384,
    "num_layers": 6
  },
  "quantization": {
    "format": "gguf",
    "bits": 4
  }
}
```

---

## Phase 7: Telemetry System ✓

### Privacy-First Design

- **Opt-in by default** - Disabled unless user enables
- **Aggressive redaction** - Emails, phone numbers, API keys removed
- **Local control** - Data stays local until approved
- **Full transparency** - Inspect/delete anytime

### Quick Start

```bash
# Enable
animica config set telemetry.opt_in true

# Use ENA (data collected)
animica ena chat "Hello"

# Inspect
animica data inspect

# Curate
animica data curate --auto --threshold 0.5 --mock

# Disable
animica config set telemetry.opt_in false

# Clear
animica data clear --force
```

### CLI Commands

**Data:**
```bash
animica data curate [--auto] [--threshold 0.5] [--mock]
animica data inspect [--limit 10] [--id SAMPLE_ID]
animica data clear [--id SAMPLE_ID] [--force]
```

**Config:**
```bash
animica config set telemetry.opt_in true|false
animica config get telemetry
animica config show
```

### Redaction Examples

| Before | After |
|--------|-------|
| `test@example.com` | `[EMAIL_REDACTED]` |
| `12345678901` | `[NUMBER_REDACTED]` |
| `sk_abc123...xyz` | `[KEY_REDACTED]` |

### Quality Scoring

Samples scored 0.0 to 1.0 based on:
- ✓ Feedback score (primary)
- ✗ User edits (negative)
- ✗ Flagged samples
- ✗ Too many redactions
- ✓ Reasonable length

---

## File Structure

```
ena/
├── workers/
│   ├── __init__.py
│   ├── worker_base.py       # Base class
│   ├── train_worker.py      # Training worker
│   ├── eval_worker.py       # Evaluation worker
│   ├── distill_worker.py    # Distillation worker
│   ├── Dockerfile.worker    # Container image
│   └── README.md
└── telemetry/
    ├── __init__.py
    ├── config.py            # Configuration
    ├── collector.py         # Data collection
    ├── curator.py           # Review & upload
    └── README.md

python/animica/cli/
└── ena_upgrade.py           # CLI (updated with data/config commands)

test_ena_workers_telemetry.py  # Test suite
ENA_WORKERS_TELEMETRY_IMPLEMENTATION.md  # Full docs
```

---

## Testing

```bash
# Run all tests
python test_ena_workers_telemetry.py

# Test individual worker
python -m ena.workers.train_worker --job-spec job.json --mock
python -m ena.workers.eval_worker --job-spec job.json --mock
python -m ena.workers.distill_worker --job-spec job.json --mock

# Test telemetry
python -c "
from ena.telemetry import TelemetryCollector, TelemetryCurator
collector = TelemetryCollector()
# ...test code...
"
```

---

## Key Features

### Workers

✓ Worker base class with common utilities
✓ DA upload/download (stubbed, MOCK mode works)
✓ Artifact hashing (SHA256)
✓ Checkpoint support
✓ Structured result reporting
✓ Error handling
✓ Docker support
✓ MOCK mode for testing

### Telemetry

✓ Opt-in by default (privacy-first)
✓ Aggressive redaction (emails, phones, keys)
✓ Local buffer with user control
✓ Quality scoring (0.0 to 1.0)
✓ Auto and manual curation modes
✓ Full inspect/delete capabilities
✓ CLI integration
✓ User ID hashing (never raw)

---

## MOCK Mode

All workers and telemetry support MOCK mode:

- **No real compute** - Simulates execution
- **No GPU required** - CPU-only
- **Fast execution** - Seconds, not hours
- **Realistic outputs** - Plausible metrics
- **No dependencies** - Works without DA, models, etc.

Use `--mock` flag or `mock_mode=True` parameter.

---

## Real Implementation (TODO)

### Workers

- [ ] Integrate real DA client
- [ ] Implement HuggingFace Trainer integration
- [ ] Implement lm-evaluation-harness integration
- [ ] Implement knowledge distillation libraries
- [ ] Add GPU monitoring
- [ ] Add full checkpoint/resume
- [ ] Add multi-GPU support

### Telemetry

- [ ] Integrate real DA upload
- [ ] Add advanced PII detection
- [ ] Add differential privacy
- [ ] Add ML-based quality scoring
- [ ] Add active learning

---

## Security

### Workers

- **Content-addressed artifacts** - SHA256 hashing
- **Isolated execution** - Docker containers
- **Resource limits** - GPU hours, cost caps
- **Error isolation** - Jobs don't affect each other

### Telemetry

- **Opt-in by default** - No data collected unless enabled
- **User ID hashing** - SHA256, never raw IDs
- **Aggressive redaction** - Better safe than sorry
- **Local control** - User approves all uploads
- **Transparent** - Full inspect/delete access
- **Revokable** - Disable anytime

---

## Summary

**Phase 6** provides a complete worker system with MOCK mode for testing and clear paths to real implementation.

**Phase 7** provides a privacy-first telemetry system with aggressive redaction and full user control.

Both phases are:
- ✓ Implemented
- ✓ Tested
- ✓ Documented
- ✓ Ready for MOCK mode use
- ⚠️ Real mode needs DA integration and ML libraries

Next steps: Integrate real DA layer and ML frameworks for production use.
