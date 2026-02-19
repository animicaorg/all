# ENA + AICF Operator Quick Reference

## Quick Start Commands

### Check System Status
```bash
# Check ENA service availability
animica ena status

# Check AICF credit pool status
animica aicf status

# Check quantum service status
animica quantum status
```

### Mining with AICF Integration
```bash
# Start mining with AICF proof (contributes to ENA training)
animica miner mine-blocks --payout-address <your-address>

# Check your mining AICF credits
animica aicf miner-credits <your-address>

# Claim your AICF credits as ANM
animica aicf claim <your-address> --all
```

### ENA Training
```bash
# List available training plans
animica aicf plans list --category training

# Submit training job
animica ena train submit --plan ena-cpu-micro --budget 1000

# Watch training job progress
animica ena train watch <job-id>

# List all training jobs
animica ena train list
```

### ENA Inference
```bash
# Local inference (free, CPU-based)
animica ena infer --local --prompt "Explain quantum computing"

# Network inference (paid, uses AICF)
animica ena infer --network --prompt "Explain quantum computing" --max-fee 0.001

# Check available models
animica ena models list

# Pull a specific model for local use
animica ena models pull ena-local-cpu
```

### Checkpoints
```bash
# List available ENA checkpoints
animica ena checkpoints list

# Fetch specific checkpoint
animica ena checkpoints fetch ena-v0.9.0-h10000

# List DA checkpoints
animica da checkpoints list --namespace ena

# Verify checkpoint integrity
animica da checkpoints verify <commitment>
```

### DA Storage Contribution
```bash
# Register as storage contributor (100GB)
animica da storage register \
  --bytes 107374182400 \
  --endpoint /mnt/animica-storage

# List storage contributors
animica da storage list

# Send heartbeat
animica da storage heartbeat
```

### Quantum Contribution
```bash
# Start quantum worker
animica quantum contribute start

# Stop quantum worker
animica quantum contribute stop

# Check quantum credits
animica quantum credits <your-address>

# List quantum jobs
animica quantum jobs list
```

## Economic Flow

### Block Mining Rewards
```
Total Block Reward (e.g., 300 ANM)
├─→ 90% (270 ANM) → Your wallet
└─→ 10% (30 ANM)  → AICF credits (for training)
```

### Transaction Fees
```
Transaction Fee (e.g., 0.001 ANM)
├─→ 70% → Operator/Miner
├─→ 20% → AICF pool (for training)
└─→ 10% → Burned
```

### ENA Call Fees
```
ENA Inference Fee (e.g., 0.0001 ANM)
├─→ 70% → AICF pool (funds training)
├─→ 20% → Service operator
└─→ 10% → Reserve/Burn
```

## AICF Plans Reference

### Miner Plans
- `miner-baseline` - Basic mining with AICF contribution
- `miner-plus-ena` - Mining optimized for ENA training rewards

### Training Plans
- `ena-cpu-micro` - Small CPU training job (low cost)
- `ena-cpu-continuous` - Continuous CPU training
- `ena-gpu-finetune` - GPU fine-tuning (higher cost, faster)
- `ena-gpu-finetune-max` - Maximum GPU allocation
- `ena-eval-regression` - Run evaluation suite
- `ena-data-curation` - Data curation job
- `ena-distill-student` - Model distillation

### Storage Plans
- `storage-da-100gb` - Contribute 100GB storage
- `storage-da-1tb` - Contribute 1TB storage

### Quantum Plans
- `quantum-sim-eval` - Simulated quantum evaluation (if hardware unavailable)

## Recommended Plan by Role

```bash
# Get plan recommendations for your role
animica aicf plans recommend --role miner
animica aicf plans recommend --role gpu
animica aicf plans recommend --role cpu
animica aicf plans recommend --role quantum
animica aicf plans recommend --role storage
```

## Fee Status

```bash
# View current fee routing configuration
animica aicf fees status
```

Expected output:
```
Fee Routing Configuration:

Block Rewards:
  AICF Share: 10.00% (1000 bps)
  Miner Share: 90.00% (9000 bps)

Transaction Fees:
  AICF Share: 20.00% (2000 bps)
  Operator Share: 70.00% (7000 bps)
  Burn: 10.00% (1000 bps)

ENA Call Fees:
  AICF Share: 70.00% (7000 bps)
  Operator Share: 20.00% (2000 bps)
  Reserve/Burn: 10.00% (1000 bps)
```

## Checkpoint Schedule

Checkpoints are published automatically every **10,000 blocks**:
- Height 10,000 → `ena-v0.9.0-h10000`
- Height 20,000 → `ena-v0.9.1-h20000`
- Height 30,000 → `ena-v0.9.2-h30000`
- etc.

Each checkpoint includes:
- Training jobs completed in the period
- Datasets used
- Eval results
- Model weights metadata
- AICF budget summary
- Top contributors

## Troubleshooting

### RPC 405 Error
```
Error: 405 Method Not Allowed
```

**Fix**: Ensure RPC URL ends with `/rpc`
```bash
export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc
```

### Read-Only Filesystem Error
```
Error: Read-only filesystem
```

**Fix**: Set writable data directory
```bash
export ANIMICA_DATA_DIR=/mnt/writable-volume
```

### BigInt Display Issue
**Fixed in latest version**. Balances > 2^53 now display correctly.

### Insufficient AICF Credits
```bash
# Check your credits
animica aicf miner-credits <your-address>

# Mine more blocks to earn credits
animica miner mine-blocks --payout-address <your-address>

# Or contribute GPU/storage/quantum
animica quantum contribute start
animica da storage register --bytes 107374182400
```

## Configuration Files

### Economic Parameters
Edit `spec/params.yaml`:
```yaml
aicf:
  block_reward_slice_bps: 1000  # 10% to AICF (default)
  fee_slice_bps: 2000           # 20% of tx fees (default)

ena:
  call_fee_aicf_bps: 7000       # 70% of ENA fees to AICF (default)
```

### Data Directory
```bash
# Default: ~/.animica
export ANIMICA_DATA_DIR=~/my-custom-dir
```

### Network Selection
```bash
# Use mainnet (default)
export ANIMICA_NETWORK=mainnet

# Use testnet
export ANIMICA_NETWORK=testnet

# Use devnet
export ANIMICA_NETWORK=devnet
```

## Alerts & Monitoring

Planned alerts (CLI will notify):
- Low AICF budget
- Failed heartbeats
- Checkpoint publish due soon
- Eval regression detected
- Model drift warning
- Wallet fee insufficient

## Support

For issues or questions:
1. Check logs: `animica node logs`
2. Run diagnostics: `animica ena doctor`
3. Check RPC: `animica aicf doctor`
4. GitHub: https://github.com/animicaorg/all/issues

## Advanced: Manual Checkpoint Publish

```bash
# Trigger checkpoint publish manually (requires operator permissions)
animica ena checkpoints publish --height <height>
```

This is normally automatic at heights divisible by 10,000.

---

**Quick Reference Version**: 1.0  
**Last Updated**: Implementation Phase 4 Complete
