# ENA Training Scripts Library

A library of training scripts for quantum/GPU/CPU contributors to earn AICF credits.

## Quick Start

### 1. Run a quantum stub workload (CPU-compatible)

```bash
python tools/ena_training/quantum_stub.py \
  --plan tools/ena_training/plans/quantum_stub_vdf.json \
  --rpc-url http://127.0.0.1:8545/rpc
```

### 2. Run CPU LoRA training

```bash
python tools/ena_training/cpu_lora.py \
  --plan tools/ena_training/plans/cpu_lora_tiny.json \
  --rpc-url http://127.0.0.1:8545/rpc
```

### 3. Run GPU fine-tuning (requires GPU)

```bash
python tools/ena_training/gpu_finetune.py \
  --plan tools/ena_training/plans/gpu_finetune_qwen_small.json \
  --rpc-url http://127.0.0.1:8545/rpc
```

## Available Scripts

### Quantum
- `quantum_stub.py` - VDF-like sequential work (placeholder for real quantum)

### GPU Scripts
- `gpu_finetune.py` - Fine-tune language models on GPU
- `gpu_eval.py` - Evaluate models on GPU
- `gpu_distill.py` - Knowledge distillation

### CPU Scripts
- `cpu_lora.py` - LoRA training (CPU-friendly)
- `cpu_eval.py` - Model evaluation on CPU
- `cpu_data_prep.py` - Dataset preprocessing and tokenization

## Built-in Plans

Located in `plans/`:

- **cpu_lora_tiny.json** - CPU LoRA on tiny model (~30 min)
- **cpu_eval_mmlu_subset.json** - Fast MMLU evaluation (~10 min)
- **gpu_finetune_qwen_small.json** - GPU fine-tune (~1 hour)
- **gpu_distill_teacher_student.json** - Distillation (~2 hours)
- **quantum_stub_vdf.json** - Sequential work (~1 min)
- **data_prep_tokenize.json** - Dataset tokenization (~20 min)

## How It Works

All scripts follow a common pattern:

1. **Load Plan** - Read configuration from JSON plan file
2. **Execute Workload** - Run training/eval/quantum computation
3. **Generate ProofEnvelope** - Create verifiable CBOR-encoded proof
4. **Submit to RPC** - Send proof to AICF for credit attribution

### ProofEnvelope Format

Each work submission generates a `ProofEnvelope` containing:

```python
{
  "version": 1,
  "job_id": "...",
  "worker_id": "...",
  "kind": "quantum|gpu_train|cpu_train|eval|data_prep",
  "inputs_commitment": "sha3-256 hash (hex)",
  "outputs_commitment": "sha3-256 hash (hex)",
  "metrics": {...},  # Kind-specific metrics
  "attestation": "sha3-256 hash of run manifest (hex)",
  "signature": "wallet signature (hex)",
  "timestamp": 1234567890
}
```

### Credit Accounting

Credits are computed deterministically:

```
credits = unit_count * reward_rate * quality_multiplier
```

- **unit_count**: From metrics (steps, epochs, samples, etc.)
- **reward_rate**: From plan configuration
- **quality_multiplier**: Optional (1.0 by default, increased for high-quality work)

## Custom Plans

Create your own plan JSON:

```json
{
  "type": "cpu_lora",
  "job_id": "my_custom_job_001",
  "description": "My custom training job",
  "dataset": "local://my_dataset",
  "model": "gpt2",
  "epochs": 5,
  "unit_definition": "per_epoch",
  "reward_rate_hint": 15,
  "required_capabilities": ["cpu", "8gb_ram"]
}
```

Then run:

```bash
python tools/ena_training/cpu_lora.py --plan my_custom_plan.json
```

## Configuration

### Environment Variables

- `ANIMICA_DATA_DIR` - Data directory (default: `~/.animica`)
- `ANIMICA_RPC_URL` - RPC endpoint (default: `http://127.0.0.1:8545/rpc`)
- `WORKER_ID` - Worker identifier (default: hostname)

### Working Directory

All outputs are written to `~/.animica/workdir` (or `$ANIMICA_DATA_DIR/workdir`).

Contents:
- `last_envelope.cbor.hex` - Last generated proof envelope
- `run_manifest.json` - Run metadata
- Model checkpoints, logs, etc.

## Requirements

### Minimum (CPU-only)

```bash
pip install cbor2 requests
```

### GPU Training

```bash
pip install torch transformers accelerate
pip install cbor2 requests
```

### Full (all features)

```bash
pip install torch transformers accelerate datasets
pip install cbor2 requests
```

## Troubleshooting

### "RPC error: method not found"

The RPC endpoint doesn't support `aicf.submitWork` yet. Check with:

```bash
animica aicf doctor
```

### "Data directory not writable"

Set a writable data directory:

```bash
export ANIMICA_DATA_DIR=/path/to/writable/dir
```

### "No GPU available"

GPU scripts will prompt before continuing on CPU. Skip the check with:

```bash
python tools/ena_training/gpu_finetune.py --skip-gpu-check --plan ...
```

### Proof submission failed

The envelope is saved to `~/.animica/workdir/last_envelope.cbor.hex`. You can submit it manually later:

```bash
# TODO: Add manual submission command
```

## Development

### Testing a Script

Run without submission:

```bash
python tools/ena_training/runner.py \
  --plan tools/ena_training/plans/quantum_stub_vdf.json \
  --no-submit
```

### Custom Runner

Import and extend the `TrainingRunner` class:

```python
from tools.ena_training.runner import TrainingRunner

class MyCustomRunner(TrainingRunner):
    def _run_my_custom_type(self):
        # Your custom logic here
        pass
```

## Security Notes

- All envelopes are signed with your wallet
- Proofs are verified on-chain before credit attribution
- Malicious proofs will be rejected and may result in penalties
- Always use official plan files or verify custom plans carefully

## License

Same as Animica repository (see LICENSE.txt in root).
