# AICF CLI Usage Guide

## Overview

The AICF (AI Compute Fund) CLI provides production-ready commands for managing AICF credits, submitting jobs, and monitoring the AICF ecosystem.

## Installation

The AICF CLI is part of the Animica CLI. Install it with:

```bash
pip install -e .
```

## Quick Start

### 1. Check AICF Status

View global AICF credit statistics:

```bash
# Human-readable output
animica aicf status

# JSON output for scripting
animica aicf status --json
```

Example output:
```
AICF Credit Summary:
  Total Balance: 1,234,567.89 credits
  Total Minted: 10,000,000 credits
  Total Spent: 8,765,432.11 credits

Last Update:
  Block Height: 12345
  Block Hash: 0xabcd...

Credits are minted from block rewards and can fund AI/Quantum training jobs.
```

### 2. Check Miner Credits

View AICF credits for a specific miner address:

```bash
# Using bech32 address
animica aicf miner-credits anim1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw

# Using hex address
animica aicf miner-credits 0x1234567890abcdef...

# JSON output
animica aicf miner-credits anim1... --json

# With debug output
animica aicf miner-credits anim1... --debug-rpc
```

### 3. RPC Diagnostics

Diagnose RPC connectivity and discover available methods:

```bash
# Test default RPC endpoint
animica aicf doctor

# Test custom endpoint
animica aicf doctor --rpc-url http://testnet.animica.org:8545

# JSON output
animica aicf doctor --json
```

Example output:
```
RPC Doctor Results
  URL: http://127.0.0.1:8545/rpc
  Reachable: ✓

Available Methods (45):
  [state]
    - state.getBalance
    - state.getNonce
    - state.getAicfSummary
    - state.getAicfMinerCredits
  
  [chain]
    - chain.getChainId
    - chain.getHead
    ...
```

## Built-in Job Plans

### List Available Plans

View all built-in job plans:

```bash
# List all plans
animica aicf jobs plans

# Filter by category
animica aicf jobs plans --category testing
animica aicf jobs plans --category qa

# Show detailed parameters
animica aicf jobs plans --details

# JSON output
animica aicf jobs plans --json
```

### Available Plans

The CLI includes 8 built-in plans focused on ENA and chain quality:

1. **ena_smoke** (testing, 100 credits, ~30s)
   - Quick smoke test for ENA inference API
   - Single prompt with fast response
   - Validates basic ENA functionality

2. **ena_regression** (testing, 5000 credits, ~5-10 min)
   - Comprehensive ENA regression test suite
   - Multiple prompts with quality checks
   - Reports pass/fail rates and quality scores

3. **repo_index_refresh** (maintenance, 10000 credits, ~10-30 min)
   - Refresh repository embeddings for ENA context
   - Indexes code files for semantic search
   - Updates vector store

4. **tx_mempool_fuzz** (qa, 2000 credits, ~2-5 min)
   - Fuzz test transaction decoding
   - Tests mempool admission logic
   - Detects crashes and edge cases

5. **rpc_conformance** (qa, 3000 credits, ~5-10 min)
   - OpenRPC conformance testing
   - Negative test cases included
   - Validates spec compliance

6. **wallet_e2e** (testing, 2500 credits, ~3-7 min)
   - End-to-end wallet operations
   - Tests balance, send, receive flows
   - Verifies state consistency

7. **consensus_sanity** (testing, 1500 credits, ~2-5 min)
   - Block production health check
   - Stale template detection
   - Fork choice validation

8. **p2p_gossip_health** (testing, 2000 credits, ~3-8 min)
   - P2P network health monitoring
   - Tests peer connectivity and relay
   - Measures propagation latency

### Submit a Job

Submit a job using a built-in plan:

```bash
# Basic submission
animica aicf jobs submit \
  --plan ena_smoke \
  --budget 500

# Override plan parameters
animica aicf jobs submit \
  --plan wallet_e2e \
  --budget 3000 \
  --param network=testnet \
  --param num_wallets=10

# Custom plan for repo indexing
animica aicf jobs submit \
  --plan repo_index_refresh \
  --budget 15000 \
  --param repo_url=https://github.com/animicaorg/all \
  --param branch=main

# JSON output
animica aicf jobs submit --plan ena_smoke --budget 500 --json
```

## Monitoring

### Watch AICF Status

Monitor AICF status changes in real-time:

```bash
# Watch with default interval (10s)
animica aicf watch

# Custom polling interval
animica aicf watch --interval 5

# Limited duration (5 minutes)
animica aicf watch --max-duration 300

# Custom RPC URL
animica aicf watch --rpc-url http://testnet.animica.org:8545
```

Example output:
```
AICF Status Monitor
Polling every 10 seconds. Press Ctrl+C to stop.

2024-02-19 10:30:00
  Balance: 1,234,567.89 credits
  Minted:  10,000,000 credits
  Spent:   8,765,432.11 credits
  Height:  12345

2024-02-19 10:30:10
  Balance: 1,234,667.89 credits
  Minted:  10,000,100 credits
  Spent:   8,765,432.11 credits
  Height:  12346
  → Minted: +100 credits
```

### Watch Job Progress

Monitor a specific job with alerts:

```bash
# Basic job monitoring
animica aicf jobs watch <job_id>

# Custom polling interval
animica aicf jobs watch <job_id> --interval 15

# With webhook alerts
animica aicf jobs watch <job_id> \
  --alert discord-webhook=https://discord.com/api/webhooks/... \
  --alert-on fail,complete

# Alert on all events
animica aicf jobs watch <job_id> \
  --alert discord-webhook=https://... \
  --alert-on fail,stall,complete
```

Alert triggers:
- `fail`: Job fails or errors
- `stall`: No progress detected
- `complete`: Job completes successfully

Example output:
```
Job Monitor: job_abc123
Polling every 10 seconds. Press Ctrl+C to stop.

Alert Config:
  Triggers: fail, complete
  Webhook: https://discord.com/api/webhooks/...

2024-02-19 10:30:00
  Status: RUNNING
  Progress: 25%
  Budget: 250/1000 credits
  Workers: 3
  ETA: 180 seconds

2024-02-19 10:30:10
  Status: RUNNING
  Progress: 50%
  Budget: 500/1000 credits
  Workers: 3
  ETA: 90 seconds
```

## Configuration

### Environment Variables

- `ANIMICA_RPC_URL`: Override default RPC endpoint
  ```bash
  export ANIMICA_RPC_URL=http://testnet.animica.org:8545
  ```

- `ANIMICA_DATA_DIR`: Override data directory
  ```bash
  export ANIMICA_DATA_DIR=/custom/path/data
  ```

### RPC URL Handling

The CLI automatically normalizes RPC URLs to include the `/rpc` suffix:

```
http://127.0.0.1:8545 → http://127.0.0.1:8545/rpc
http://127.0.0.1:8545/ → http://127.0.0.1:8545/rpc
http://127.0.0.1:8545/rpc → http://127.0.0.1:8545/rpc (unchanged)
```

If you encounter a `405 Method Not Allowed` error, the CLI will provide actionable guidance:

```
❌ 405 Method Not Allowed

Your RPC URL is incorrect or missing /rpc:
  Current: http://127.0.0.1:8545

The RPC server expects POST requests to /rpc.
Fix: Set ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc
```

## Troubleshooting

### Connection Issues

If you cannot connect to the RPC server:

1. Check the node is running:
   ```bash
   animica node status
   ```

2. Verify the RPC URL:
   ```bash
   animica aicf doctor
   ```

3. Check network/firewall settings

4. Enable debug output:
   ```bash
   animica aicf status --debug-rpc
   ```

### Method Not Found

If you get a "method not found" error:

1. Check available methods:
   ```bash
   animica aicf doctor
   ```

2. Verify your node version supports AICF

3. Update your node if needed

### Parameter Validation Errors

When submitting jobs, if you get validation errors:

1. Check plan requirements:
   ```bash
   animica aicf jobs plans --details
   ```

2. Ensure all required parameters are provided

3. Use `--param key=value` format correctly

## Advanced Usage

### Scripting with JSON Output

All commands support `--json` for machine parsing:

```bash
# Get credits as JSON
credits=$(animica aicf miner-credits anim1... --json)
balance=$(echo $credits | jq -r '.balance')

# Monitor and alert
status=$(animica aicf status --json)
minted=$(echo $status | jq -r '.minted_total')
```

### Custom Alert Webhooks

The webhook alert system sends POST requests with JSON payloads:

```json
{
  "content": "Job job_abc123 completed successfully"
}
```

This format is compatible with:
- Discord webhooks
- Slack incoming webhooks
- Custom HTTP endpoints

### Batch Operations

Submit multiple jobs in a loop:

```bash
#!/bin/bash
for plan in ena_smoke rpc_conformance wallet_e2e; do
  animica aicf jobs submit --plan $plan --budget 2000
  echo "Submitted $plan"
  sleep 5
done
```

## Best Practices

1. **Use built-in plans** instead of custom JSON when possible
2. **Set appropriate budgets** - check plan minimums
3. **Monitor jobs** with alerts for production use
4. **Use --json** for automation and scripting
5. **Check doctor** before reporting RPC issues
6. **Watch status** to detect minting/spending changes

## Examples

### Complete Workflow

```bash
# 1. Check your miner credits
animica aicf miner-credits anim1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw

# 2. Review available plans
animica aicf jobs plans

# 3. Submit a smoke test
animica aicf jobs submit --plan ena_smoke --budget 500

# 4. Monitor the job (when backend implemented)
animica aicf jobs watch <job_id> --alert-on complete

# 5. Watch global AICF status
animica aicf watch --max-duration 300
```

### Testing Checklist

Run all QA plans:

```bash
# Mempool fuzzing
animica aicf jobs submit --plan tx_mempool_fuzz --budget 2000

# RPC conformance
animica aicf jobs submit --plan rpc_conformance --budget 3000

# Wallet E2E
animica aicf jobs submit --plan wallet_e2e --budget 2500
```

### Maintenance Tasks

Regular maintenance with built-in plans:

```bash
# Daily: Refresh code index
animica aicf jobs submit \
  --plan repo_index_refresh \
  --budget 10000 \
  --param repo_url=https://github.com/animicaorg/all

# Weekly: Comprehensive testing
animica aicf jobs submit --plan ena_regression --budget 5000
animica aicf jobs submit --plan consensus_sanity --budget 1500
animica aicf jobs submit --plan p2p_gossip_health --budget 2000
```
