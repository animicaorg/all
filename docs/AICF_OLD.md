# AICF (AI Compute Fund)

## Overview

AICF is Animica's on-chain AI compute marketplace that coordinates GPU workloads and distributes rewards to GPU contributors. It integrates with ENA (Animica's inference service) to fund GPU compute via a percentage of each inference call.

## Architecture

```
User → ENA Inference → AICF Contribution (25% of fee)
                          ↓
                    Epoch Accounting
                          ↓
                    Reward Distribution
                          ↓
            GPU Workers (70%) | Treasury (20%) | Dev (5%) | Burn (5%)
```

## How Funds Flow

1. **Inflow**: Users pay for ENA inference. A portion (default 25%) goes to AICF.
2. **Accounting**: Contributions are recorded per epoch (e.g., every 1000 blocks).
3. **Credits**: GPU workers earn credits for completing jobs.
4. **Distribution**: At epoch finalization:
   - 70% → GPU workers (proportional to credits)
   - 20% → Treasury
   - 5% → Dev fund
   - 5% → Burn
5. **Payout**: Workers claim their share via on-chain transactions.

## For Users

### Making an AICF Contribution

When you use ENA inference, AICF contributions are **automatic**:

```bash
# This command includes automatic AICF payment
animica ena infer "hello world"
```

The CLI will:
1. Compute the total fee (service + AICF)
2. Submit two transactions: one to ENA service, one to AICF
3. Wait for confirmation
4. Run inference
5. Display receipt with AICF contribution details

### Checking AICF Status

```bash
# View AICF information
animica ena aicf info

# Check contribution transaction
animica ena aicf verify <tx_hash>
```

### Receipts

Example receipt:

```
✓ Inference complete!

Receipt:
  ID: req_abc123
  Mode: per_call_tx

AICF Contribution:
  Amount: 0.0025 ANM
  Required: 0.0025 ANM
  Status: ✓ Verified on-chain
  Transaction: 0xabc...def
```

## For GPU Contributors

### Registration

Register as a GPU worker to start earning:

```bash
# Register with your payout address
animica ena aicf worker-register anim1your_address_here --name "MyGPU"
```

This returns a `worker_id` that you'll use for all subsequent operations.

### Running Jobs

Start the worker loop to pull and process jobs:

```bash
# Single job
animica ena aicf worker-run <worker_id>

# Continuous loop (recommended)
animica ena aicf worker-run <worker_id> --loop
```

The worker will:
1. Poll for available jobs
2. Execute the job (training, inference, etc.)
3. Submit results
4. Earn credits

### Claiming Rewards

After an epoch is finalized, claim your share:

```bash
# Claim rewards for epoch N
animica ena aicf worker-claim <worker_id> <epoch_number>
```

Example output:

```
✓ Rewards claimed!
  Amount: 1.234 ANM
  Transaction: 0x123...abc
  Status: CONFIRMED
```

### Checking Worker Status

```bash
# View your worker info
animica ena aicf worker-status <worker_id>

# List all workers
animica ena aicf list-workers
```

## For Operators

### Creating Jobs

Create training or inference jobs for workers:

```bash
animica aicf coordinator create-job \
  --type distill \
  --difficulty 3 \
  --dataset ipfs://Qm...
```

### Finalizing Epochs

Epochs can be auto-finalized or manually triggered:

```bash
# Manual epoch finalization
animica aicf epoch finalize
```

This calculates reward shares and enables workers to claim payouts.

### Monitoring

```bash
# View epoch info
animica ena aicf epoch-info <epoch_number>

# Check protocol status
animica ena aicf protocol-status
```

## Economics

### Reward Split

Default distribution per epoch:

- **70%** → GPU workers (proportional to credits earned)
- **20%** → Treasury (community fund)
- **5%** → Dev fund (protocol development)
- **5%** → Burn (deflationary)

### Epochs

- **Length**: 1000 blocks (configurable)
- **Challenge Window**: 100 blocks after epoch end
- **Finalization**: After challenge window closes

### Credits

Workers earn credits for:
- Completing training jobs
- Running inference
- Providing storage proofs
- Quantum compute proofs

Credit weight varies by job difficulty and verification.

## Troubleshooting

### Doctor Command

Run diagnostics if you encounter issues:

```bash
# Check AICF configuration and connectivity
animica ena aicf doctor
```

The doctor will check:
- RPC connectivity
- Wallet configuration
- Data directory permissions
- ENA endpoint accessibility

### Common Issues

**Issue**: "AICF contribution failed"

**Fix**:
1. Check wallet balance: `animica wallet balance`
2. Verify AICF address: `animica ena aicf info`
3. Check transaction logs: `animica tx status <hash>`

**Issue**: "No jobs available"

**Fix**:
1. Ensure worker is registered: `animica ena aicf worker-status <id>`
2. Check coordinator is running
3. Try again later (jobs are created on demand)

**Issue**: "Claim failed - epoch not finalized"

**Fix**:
Wait for epoch to finalize. Check status:
```bash
animica ena aicf epoch-info <epoch>
```

## Configuration

### Environment Variables

```bash
# ENA endpoint (default: https://pool.animica.org)
export ENA_ENDPOINT=https://pool.animica.org

# Animica RPC (default: https://mainnet.animica.org/rpc)
export ANIMICA_RPC_URL=https://mainnet.animica.org/rpc

# AICF database path (for coordinators)
export AICF_DB_PATH=~/.animica/aicf_protocol.db
```

### Protocol Parameters

Configured in `aicf/protocol/config.py`:

```python
EPOCH_LENGTH_BLOCKS = 1000
CHALLENGE_WINDOW_BLOCKS = 100
MIN_STAKE = 10_000_000_000  # 10 ANM
MAX_WORKERS = 10000
```

## Security

- All payments are on-chain and verifiable
- Jobs are verified before credits are minted
- Challenge window allows disputing invalid work
- Slashing for malicious behavior

## API Reference

See [AICF Protocol RPC Methods](../aicf/specs/RPC.md) for detailed API documentation.

## Further Reading

- [ENA Documentation](./ENA.md)
- [AICF Protocol Specification](../aicf/README.md)
- [Economics Model](../aicf/economics/README.md)
