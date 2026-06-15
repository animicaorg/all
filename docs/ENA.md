# ENA (Embedded Neural Agent)

## Overview

ENA is Animica's LLM inference service that provides low-latency, cost-effective AI completions integrated with the Animica blockchain. Every ENA call automatically contributes to AICF (AI Compute Fund), funding GPU workers.

## Quick Start

### Installation

```bash
# Install dependencies
pip install httpx

# Or install Animica with extras
pip install -e ".[ena]"
```

### First Inference Call

```bash
# Simple inference with automatic AICF payment
animica ena infer "What is Animica?"
```

This command will:
1. Load your default wallet
2. Compute the fee (base + AICF contribution)
3. Submit payment transactions
4. Run inference
5. Display the result

## Commands

### Models

List available models:

```bash
# List all models
animica ena models

# JSON output
animica ena models --json
```

Example output:

```
Available Models
┌────────────────┬─────────┬────────────┬──────────────────────┐
│ Name           │ Version │ Max Tokens │ Description          │
├────────────────┼─────────┼────────────┼──────────────────────┤
│ ena.small      │ 1.0     │ 2048       │ Fast, compact model  │
│ ena.medium     │ 1.0     │ 4096       │ Balanced performance │
│ ena.large      │ 1.0     │ 8192       │ High quality         │
└────────────────┴─────────┴────────────┴──────────────────────┘

Aliases:
  latest → ena.medium
  default → ena.medium

Default: ena.medium
```

### Pricing

View pricing and AICF contribution details:

```bash
# View pricing
animica ena pricing

# JSON output
animica ena pricing --json
```

Example output:

```
ENA Pricing:
  Base fee per call: 0.01 ANM
  Fee per output token: 0.0001 ANM

AICF (AI Compute Fund):
  Address: anim1aicf0000000000000000000000000000000000
  Contribution: 2500 basis points (25%)

Example Payment (100 token response):
  Total cost: 0.02 ANM
  → Service fee: 0.015 ANM
  → AICF contribution: 0.005 ANM
```

### Inference

Run inference with payment:

```bash
# Basic inference
animica ena infer "Explain blockchain"

# Specify model
animica ena infer "Hello" --model ena.large

# Limit output tokens
animica ena infer "List 5 facts" --max-tokens 200

# Use specific wallet
animica ena infer "Test" --from anim1your_address_here

# Custom endpoints
animica ena infer "Test" \
  --endpoint https://ena.custom.org \
  --rpc-url https://rpc.custom.org

# JSON output
animica ena infer "Test" --json
```

### Payment Modes

ENA supports two payment modes:

#### 1. Per-Call Transaction (Default)

Sends two separate transactions for each call:
- One to ENA service
- One to AICF

```bash
animica ena infer "Hello" --fee-mode per_call_tx
```

**Pros**: Explicit AICF tracking, full transparency
**Cons**: Two transactions per call, slight delay

#### 2. Credit System (Future)

Pre-deposit credits for multiple calls:

```bash
# Deposit credits
animica ena deposit 10.0

# Inference deducts from credits
animica ena infer "Hello" --fee-mode credit
```

**Pros**: Faster (no tx per call), lower fees
**Cons**: Requires upfront deposit, credit management

### Status

Check your AICF contribution status:

```bash
# View contributions
animica ena aicf status

# Check specific transaction
animica ena aicf verify <tx_hash>
```

### Deposit (Credit Mode)

Deposit funds for credit-based payments:

```bash
# Deposit 10 ANM
animica ena deposit 10.0

# From specific wallet
animica ena deposit 5.0 --from wallet_label

# Custom RPC
animica ena deposit 10.0 --rpc-url https://rpc.custom.org
```

## Configuration

### Environment Variables

```bash
# ENA endpoint (default: https://pool.animica.org)
export ENA_ENDPOINT=https://pool.animica.org

# Animica RPC (default: https://mainnet.animica.org/rpc)
export ANIMICA_RPC_URL=https://mainnet.animica.org/rpc

# ENA service address (for payments)
export ENA_SERVICE_ADDRESS=anim1ena_service_address
```

### Local vs Remote Inference

ENA can run inference locally (CPU) or remotely (GPU cluster):

```bash
# Local CPU inference (slower, free)
animica ena infer "Test" --local

# Remote GPU inference (faster, paid)
animica ena infer "Test" --remote https://pool.animica.org
```

**Note**: `--local` flag is planned for future implementation.

## Fees

### Fee Structure

Fees are calculated as:

```
total_fee = base_fee + (output_tokens × per_token_fee)
```

Example with 100 token response:
- Base fee: 0.01 ANM
- Token fee: 100 × 0.0001 = 0.01 ANM
- **Total**: 0.02 ANM

### AICF Contribution

By default, 25% of each fee goes to AICF:

```
aicf_contribution = total_fee × 0.25
service_fee = total_fee - aicf_contribution
```

Using the example above:
- Total: 0.02 ANM
- Service: 0.015 ANM
- AICF: 0.005 ANM

## Receipts

Each inference call returns a detailed receipt:

```json
{
  "answer": "Animica is...",
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 50,
    "total_tokens": 60
  },
  "receipt": {
    "id": "req_abc123",
    "mode": "per_call_tx",
    "amount": 15000000,
    "tx_hash_service": "0xabc...123",
    "tx_hash_aicf": "0xdef...456",
    "aicf_paid": 5000000,
    "aicf_required": 5000000,
    "aicf_explicit": true
  }
}
```

### Receipt Fields

- `id`: Unique request identifier
- `mode`: Payment mode (per_call_tx or credit)
- `amount`: Total paid (base units)
- `tx_hash_service`: Service payment transaction
- `tx_hash_aicf`: AICF payment transaction
- `aicf_paid`: Amount sent to AICF
- `aicf_required`: Required AICF amount
- `aicf_explicit`: True if AICF verified on-chain

## Troubleshooting

### Doctor Command

Run diagnostics if you encounter issues:

```bash
# Full diagnostics
animica ena doctor

# Custom endpoints
animica ena doctor \
  --endpoint https://ena.custom.org \
  --rpc-url https://rpc.custom.org

# JSON output
animica ena doctor --json
```

The doctor checks:
1. ENA endpoint connectivity
2. RPC connectivity
3. Wallet configuration
4. Wallet balances
5. AICF pricing configuration

### Common Issues

#### "Wallet file not found"

**Error**: `Error: Wallet file not found: ~/.animica/wallets.json`

**Fix**:
```bash
# Create a new wallet
animica wallet new
```

#### "Insufficient balance"

**Error**: `Error: Insufficient balance for transaction`

**Fix**:
```bash
# Check balance
animica wallet balance

# Use faucet (testnet/devnet only)
animica faucet request
```

#### "Transaction failed"

**Error**: `Error: Transaction failed`

**Fixes**:
1. Check RPC is reachable:
   ```bash
   animica rpc call chain.getChainId
   ```

2. Verify wallet has funds:
   ```bash
   animica wallet balance
   ```

3. Check transaction status:
   ```bash
   animica tx status <hash>
   ```

#### "ENA endpoint not reachable"

**Error**: `Error: Failed to fetch pricing: Connection timeout`

**Fixes**:
1. Check network connection
2. Verify endpoint URL:
   ```bash
   curl https://pool.animica.org/v1/models
   ```
3. Use different endpoint:
   ```bash
   animica ena infer "Test" --endpoint https://backup.ena.org
   ```

#### "BigInt serialization error"

**Error**: `TypeError: Do not know how to serialize a BigInt`

**Fix**: This should not occur in latest version. If it does:
1. Update animica CLI: `pip install -U animica`
2. Report issue with exact command that caused error

## Integration

### Using ENA in Your Application

```python
import httpx
import json

# Configuration
ENA_ENDPOINT = "https://pool.animica.org"
ANIMICA_RPC = "https://mainnet.animica.org/rpc"

# Get pricing
response = httpx.get(f"{ENA_ENDPOINT}/v1/pricing")
pricing = response.json()

# Calculate fee
base_fee = pricing["fee_per_call"]
aicf_fee = (base_fee * pricing["aicf_bp"]) // 10000
service_fee = base_fee - aicf_fee

# Send payments (use animica CLI or SDK)
# tx1 = send_payment(to=ena_service, amount=service_fee)
# tx2 = send_payment(to=aicf_address, amount=aicf_fee)

# Run inference
response = httpx.post(
    f"{ENA_ENDPOINT}/v1/infer",
    json={
        "prompt": "Hello world",
        "max_tokens": 100,
        "payment": {
            "mode": "per_call_tx",
            "payer": "anim1...",
            "tx_hash_service": "0x...",
            "tx_hash_aicf": "0x...",
        }
    }
)

result = response.json()
print(result["answer"])
```

### SDK Integration

```python
from animica import ENA

# Initialize client
ena = ENA(
    endpoint="https://pool.animica.org",
    rpc_url="https://mainnet.animica.org/rpc",
    wallet_path="~/.animica/wallets.json",
)

# Run inference with automatic payment
result = ena.infer("What is Animica?", max_tokens=200)
print(result.answer)
print(f"Cost: {result.receipt.amount_anm} ANM")
```

## Security

- All payments are on-chain and verifiable
- Private keys never leave your machine
- ENA service cannot access your wallet
- Receipts are cryptographically signed

## Privacy

- Prompts are not stored on-chain
- AICF only tracks payment amounts, not content
- Inference logs are ephemeral (configurable retention)

## Rate Limits

Default rate limits:
- **10 requests/minute** per wallet
- **1000 requests/day** per wallet

Contact support for higher limits.

## Further Reading

- [AICF Documentation](./AICF.md)
- [ENA Architecture](../ena/ARCHITECTURE.md)
- [Payment Integration Guide](../ena/AICF_INTEGRATION_GUIDE.md)
- [Troubleshooting Guide](./TROUBLESHOOTING.md)
