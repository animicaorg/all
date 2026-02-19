# Quantum AICF Contribute Guide

**Start contributing useful work in 60 seconds.**

This guide shows you how to register as a quantum/GPU/CPU contributor and earn AICF credits by running training, evaluation, and quantum workloads.

## Table of Contents

- [Quick Start](#quick-start)
- [Contributor Types](#contributor-types)
- [Registration](#registration)
- [Running Jobs](#running-jobs)
- [Rewards & Credits](#rewards--credits)
- [Claiming Payouts](#claiming-payouts)
- [CLI Reference](#cli-reference)
- [Troubleshooting](#troubleshooting)

## Quick Start

### 1. Register as a Contributor (CPU-only example)

```bash
# Create capabilities file
cat > caps.json <<EOF
{
  "worker_type": "cpu",
  "resources": {
    "cpu_cores": 4,
    "ram_gb": 8,
    "disk_gb": 50,
    "bandwidth_mbps": 100
  }
}
EOF

# Register
animica quantum contribute register \
  --type cpu \
  --caps caps.json
```

### 2. Run a Quantum Stub Workload

```bash
# Run a simple quantum stub (VDF-like useful work)
animica quantum contribute run \
  --plan quantum_stub_vdf

# Or use the direct script
python tools/ena_training/quantum_stub.py \
  --plan tools/ena_training/plans/quantum_stub_vdf.json
```

### 3. Check Your Credits

```bash
# Check credits earned
animica aicf miner-credits <YOUR_ADDRESS>

# Monitor in real-time
animica quantum contribute status
```

That's it! You're now contributing to the network and earning credits.

## Contributor Types

### CPU Contributors

**Requirements:**
- Any modern CPU (4+ cores recommended)
- 8+ GB RAM
- Python 3.8+

**Best for:**
- LoRA training on small models
- Dataset preprocessing
- Model evaluation
- Quantum stubs (VDF-like work)

**Example:**
```bash
# CPU LoRA training (~30 minutes)
python tools/ena_training/cpu_lora.py \
  --plan tools/ena_training/plans/cpu_lora_tiny.json
```

### GPU Contributors

**Requirements:**
- NVIDIA GPU with CUDA support
- 16+ GB VRAM (for large models)
- PyTorch + CUDA installed

**Best for:**
- Large model fine-tuning
- Distillation
- Fast evaluation
- High-throughput workloads

**Example:**
```bash
# GPU fine-tuning (~1 hour)
python tools/ena_training/gpu_finetune.py \
  --plan tools/ena_training/plans/gpu_finetune_qwen_small.json
```

### Quantum Contributors

**Requirements:**
- CPU (for quantum stubs)
- Future: Real quantum hardware (QPU)

**Best for:**
- Quantum circuit simulation
- Verifiable delay functions
- Trap-circuit verification

**Example:**
```bash
# Quantum stub (~1 minute)
python tools/ena_training/quantum_stub.py \
  --plan tools/ena_training/plans/quantum_stub_vdf.json
```

## Registration

### Create Capabilities File

Define your hardware capabilities:

**CPU Example:**
```json
{
  "worker_type": "cpu",
  "resources": {
    "cpu_cores": 8,
    "cpu_model": "Intel i7-10700K",
    "ram_gb": 16,
    "disk_gb": 500,
    "bandwidth_mbps": 1000
  },
  "supported_proof_types": ["cpu_train", "eval", "data_prep"]
}
```

**GPU Example:**
```json
{
  "worker_type": "gpu",
  "resources": {
    "cpu_cores": 16,
    "ram_gb": 64,
    "gpu_model": "NVIDIA RTX 4090",
    "gpu_vram_gb": 24,
    "cuda_version": "12.1",
    "disk_gb": 1000,
    "bandwidth_mbps": 10000
  },
  "supported_proof_types": ["gpu_train", "gpu_eval", "distill"]
}
```

**Quantum Example:**
```json
{
  "worker_type": "quantum",
  "resources": {
    "cpu_cores": 4,
    "ram_gb": 8,
    "qpu_type": "stub_v1",
    "max_qubits": 0,
    "shots_per_sec": 1000
  },
  "supported_proof_types": ["stub_quantum_v1"]
}
```

### Register

```bash
animica quantum contribute register \
  --type gpu \
  --caps caps.json \
  --address <YOUR_WALLET_ADDRESS>
```

**Options:**
- `--type` - Worker type: `cpu`, `gpu`, or `quantum`
- `--caps` - Path to capabilities JSON or inline JSON
- `--address` - Your wallet address (defaults to default wallet)
- `--dry-run` - Show what would be registered without actually registering
- `--json` - Output as JSON

### Verify Registration

```bash
animica quantum contribute status
```

## Running Jobs

### Available Job Types

| Plan | Type | Duration | Credits/Run | Requirements |
|------|------|----------|-------------|--------------|
| cpu_lora_tiny | CPU LoRA | 30 min | ~30 | 4 cores, 8GB RAM |
| cpu_eval_mmlu_subset | CPU Eval | 10 min | ~5 | 2 cores, 4GB RAM |
| gpu_finetune_qwen_small | GPU Fine-tune | 1 hour | ~100 | 16GB VRAM |
| gpu_distill_teacher_student | GPU Distill | 2 hours | ~300 | 32GB VRAM |
| quantum_stub_vdf | Quantum Stub | 1 min | ~10 | CPU only |
| data_prep_tokenize | Data Prep | 20 min | ~20 | 8 cores, 8GB RAM |

### Run via CLI

```bash
# Method 1: Using animica CLI
animica quantum contribute run --plan cpu_lora_tiny

# Method 2: Direct script execution
python tools/ena_training/cpu_lora.py \
  --plan tools/ena_training/plans/cpu_lora_tiny.json \
  --rpc-url http://127.0.0.1:8545/rpc
```

### Custom Plans

Create your own plan:

```json
{
  "type": "cpu_lora",
  "job_id": "my_training_job_001",
  "description": "Custom training job",
  "dataset": "local://my_dataset",
  "model": "gpt2",
  "epochs": 5,
  "batch_size": 1,
  "gradient_accumulation_steps": 8,
  "learning_rate": 5e-5,
  "lora_r": 8,
  "lora_alpha": 16,
  "expected_runtime_sec": 3600,
  "unit_definition": "per_epoch",
  "reward_rate_hint": 20,
  "required_capabilities": ["cpu", "8gb_ram"]
}
```

Then run:

```bash
python tools/ena_training/cpu_lora.py --plan my_plan.json
```

### Monitor Progress

```bash
# Watch a specific job
animica quantum contribute watch <JOB_ID>

# Check overall worker status
animica quantum contribute status
```

## Rewards & Credits

### How Credits Are Computed

```
credits_earned = unit_count × reward_rate × quality_multiplier
```

**Components:**
- **unit_count**: From work metrics (epochs, steps, samples)
- **reward_rate**: From plan configuration
- **quality_multiplier**: 1.0 by default, higher for exceptional work

### Credit Rates (Examples)

| Work Type | Unit | Rate | Example Earnings |
|-----------|------|------|------------------|
| CPU LoRA (3 epochs) | per_epoch | 10 | 30 credits |
| CPU Eval (100 samples) | per_sample | 0.5 | 50 credits |
| GPU Fine-tune (1 epoch) | per_epoch | 100 | 100 credits |
| Quantum Stub (10k steps) | per_1000_steps | 5 | 50 credits |
| Data Prep (10k samples) | per_10k_samples | 20 | 20 credits |

### Verification

Work is verified before credits are awarded:

1. **Schema Validation** - ProofEnvelope must be well-formed
2. **Signature Check** - Proof must be signed by registered worker
3. **Job Constraints** - Work must match job requirements
4. **Attestation** - Run manifest must be reproducible

### Check Balance

```bash
# Check your credits
animica aicf miner-credits <YOUR_ADDRESS>

# Watch credits accumulate
animica aicf watch
```

## Claiming Payouts

### Check Claimable Credits

```bash
# For mining credits (from block rewards)
animica aicf miner-credits <ADDRESS>

# For worker contribution credits (coming soon)
animica quantum contribute status
```

### Claim Credits

```bash
# Claim all available credits
animica aicf claim --type mining --address <ADDRESS>

# Partial claim
animica aicf claim --type mining --address <ADDRESS> --amount 1000
```

**Note:** Claims are processed deterministically on-chain. Expect a few blocks for settlement.

## CLI Reference

### `animica quantum contribute register`

Register as a quantum/GPU/CPU contributor.

```bash
animica quantum contribute register \
  --type <cpu|gpu|quantum> \
  --caps <path-to-json|inline-json> \
  [--address <wallet-address>] \
  [--dry-run] \
  [--json]
```

### `animica quantum contribute run`

Run a workload and submit proofs.

```bash
animica quantum contribute run \
  --plan <plan-name|path-to-json> \
  [--budget <max-anm>] \
  [--dry-run] \
  [--json]
```

### `animica quantum contribute status`

Show worker status and earnings.

```bash
animica quantum contribute status [<address>] \
  [--json]
```

### `animica quantum contribute watch`

Stream job progress and attribution.

```bash
animica quantum contribute watch <job-id> \
  [--interval <seconds>]
```

### `animica aicf miner-credits`

Check AICF credit balance.

```bash
animica aicf miner-credits <address> \
  [--json]
```

### `animica aicf claim`

Claim credits for withdrawal.

```bash
animica aicf claim \
  --type <mining|storage> \
  --address <address> \
  [--amount <credits>]
```

## Troubleshooting

### Data Directory Not Writable

**Error:**
```
PermissionError: Data directory /home/user/.animica is not writable
```

**Solution:**
```bash
# Set writable data directory
export ANIMICA_DATA_DIR=/path/to/writable/dir

# Or fix permissions
chmod 755 ~/.animica
```

### RPC Method Not Found

**Error:**
```
RPC error: method 'aicf.submitWork' not found
```

**Solution:**
```bash
# Check available methods
animica aicf doctor

# Ensure node is running with AICF enabled
animica node status
```

### No GPU Available

**Warning:**
```
No GPU available - will use CPU (slower)
```

**Solutions:**

1. **Skip GPU check:**
   ```bash
   python tools/ena_training/gpu_finetune.py --skip-gpu-check --plan ...
   ```

2. **Use CPU plan instead:**
   ```bash
   python tools/ena_training/cpu_lora.py --plan tools/ena_training/plans/cpu_lora_tiny.json
   ```

### Proof Submission Failed

**Error:**
```
Failed to submit: Connection refused
```

**Recovery:**

Your proof envelope is saved to `~/.animica/workdir/last_envelope.cbor.hex`.

Resubmit manually when RPC is available:

```bash
# TODO: Add manual submission command
# For now, re-run the script when node is back online
```

### Invalid Capabilities

**Error:**
```
Error: Missing required field 'worker_type' in capabilities
```

**Solution:**

Ensure your capabilities JSON has all required fields:

```json
{
  "worker_type": "cpu",  # Required
  "resources": {         # Required
    "cpu_cores": 4,
    "ram_gb": 8
  }
}
```

## Environment Variables

Configure via environment:

```bash
# Data directory
export ANIMICA_DATA_DIR=~/.animica

# RPC endpoint
export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc

# Network
export ANIMICA_NETWORK=mainnet

# Worker ID (defaults to hostname)
export WORKER_ID=my-worker-001
```

## Security Best Practices

1. **Protect Your Keys** - Never share private keys or wallet seeds
2. **Verify Plans** - Only run trusted job plans
3. **Monitor Credits** - Track earnings regularly
4. **Update Software** - Keep animica CLI up to date
5. **Report Issues** - Submit bugs to GitHub

## Examples

### Example 1: CPU-Only Contributor

```bash
# 1. Register
cat > caps.json <<EOF
{
  "worker_type": "cpu",
  "resources": {
    "cpu_cores": 8,
    "ram_gb": 16,
    "disk_gb": 100
  }
}
EOF

animica quantum contribute register --type cpu --caps caps.json

# 2. Run quantum stub
python tools/ena_training/quantum_stub.py \
  --plan tools/ena_training/plans/quantum_stub_vdf.json

# 3. Check credits
animica aicf miner-credits $(animica key list | grep address | head -1 | awk '{print $2}')
```

### Example 2: GPU Contributor

```bash
# 1. Register with GPU
cat > caps_gpu.json <<EOF
{
  "worker_type": "gpu",
  "resources": {
    "cpu_cores": 16,
    "ram_gb": 64,
    "gpu_model": "RTX 4090",
    "gpu_vram_gb": 24,
    "cuda_version": "12.1"
  }
}
EOF

animica quantum contribute register --type gpu --caps caps_gpu.json

# 2. Run GPU fine-tuning
python tools/ena_training/gpu_finetune.py \
  --plan tools/ena_training/plans/gpu_finetune_qwen_small.json

# 3. Check status
animica quantum contribute status
```

### Example 3: Batch Processing

```bash
# Run multiple plans in sequence
for plan in quantum_stub_vdf cpu_lora_tiny cpu_eval_mmlu_subset; do
  echo "Running plan: $plan"
  animica quantum contribute run --plan $plan
  sleep 10
done

# Check total credits
animica aicf miner-credits <YOUR_ADDRESS>
```

## Next Steps

- **Join the Community** - Discord, Telegram for contributor support
- **Advanced Training** - Custom models, datasets, fine-tuning
- **Provider Node** - Run a full AICF provider node
- **Governance** - Participate in protocol upgrades

## Resources

- [Main README](../README.md) - Repository overview
- [Training Scripts](../tools/ena_training/README.md) - Detailed script documentation
- [AICF Specification](../aicf/README.md) - Technical details
- [CLI Guide](../ANIMICA_CLI_SUMMARY.md) - Full CLI reference

---

**Questions?** Open an issue on GitHub or join our Discord.
