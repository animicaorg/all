# AICF Integration Guide

## Overview

AICF (AI Compute Fund) is a mandatory contribution mechanism for ENA pay-per-call transactions. Every inference request must include a provable contribution to the AICF address, which funds AI infrastructure development.

## Key Concepts

### What is AICF?

The AI Compute Fund (AICF) is a designated on-chain address that receives a configurable percentage of all ENA inference fees. This creates a sustainable funding mechanism for:

- AI infrastructure development
- Compute resource expansion
- Research and development
- Community initiatives

### Configuration

AICF is configured via environment variables:

```bash
# AICF recipient address
ENA_AICF_ADDRESS=anim1aicftest...

# AICF contribution percentage (basis points)
# 2500 = 25%, 1000 = 10%, etc.
ENA_AICF_BP=2500

# Hard requirement flag (production should be true)
ENA_REQUIRE_AICF=true

# Minimum fee per call
ENA_MIN_FEE_PER_CALL=10000000  # 0.01 ANM
```

### Fee Calculation

Given a total fee requirement, the split is calculated as:

```python
total_fee = ENA_MIN_FEE_PER_CALL
aicf_fee = ceil(total_fee * ENA_AICF_BP / 10000)
service_fee = total_fee - aicf_fee
```

**Example (25% AICF):**
- Total fee: 10,000,000 base units (0.01 ANM)
- AICF contribution: 2,500,000 base units (0.0025 ANM)
- Service fee: 7,500,000 base units (0.0075 ANM)

## Payment Modes

### Mode 1: Per-Call Transactions (Two-TX)

The client sends **two separate transactions**:

1. **Service Payment**: Pays service fee to `ENA_SERVICE_ADDRESS`
2. **AICF Payment**: Pays AICF contribution to `ENA_AICF_ADDRESS`

**API Request Format:**

```json
{
  "prompt": "Hello, world!",
  "model": "ena.latest",
  "maxTokens": 200,
  "payment": {
    "mode": "per_call_tx",
    "payer": "anim1user123...",
    "tx_hash_service": "0xabc123...",
    "tx_hash_aicf": "0xdef456..."
  }
}
```

**CLI Usage:**

```bash
# The CLI automatically handles the two-transaction flow
animica ena infer --prompt "Hello" --fee-mode per_call_tx
```

This will:
1. Fetch pricing from ENA endpoint (includes AICF details)
2. Calculate service and AICF fees
3. Send service payment transaction
4. Send AICF payment transaction
5. Submit both hashes to ENA API

### Mode 2: Credit/Deposit (With Reserve)

**Deposit Flow:**

1. User deposits total amount to ENA service
2. ENA splits deposit into:
   - `service_credits`: Available for inference
   - `aicf_reserve`: Reserved for AICF
3. Each call deducts from both pools
4. AICF reserve is periodically swept to AICF address

**Split Calculation:**

```python
deposit_amount = 100_000_000  # 0.1 ANM
aicf_reserve = ceil(deposit_amount * ENA_AICF_BP / 10000)
service_credits = deposit_amount - aicf_reserve
```

**Per-Call Deduction:**

```python
call_cost = calculate_cost(prompt_tokens, completion_tokens)
service_deduct = call_cost * (10000 - ENA_AICF_BP) / 10000
aicf_deduct = call_cost * ENA_AICF_BP / 10000
```

**Sweeper Job:**

ENA runs a background job that:
- Monitors `aicf_reserve` balance
- When threshold reached, sends on-chain transfer to `ENA_AICF_ADDRESS`
- Provides status via `/v1/aicf/status`

## Server-Side Verification

The ENA node performs comprehensive verification:

### 1. Input Validation

- Validates payer address (Bech32 format)
- Validates transaction hashes (hex format)
- Validates all amounts are positive integers

### 2. Transaction Fetching

- Fetches transactions from RPC (mempool or chain)
- Supports both pending and confirmed transactions
- Handles RPC failures gracefully (circuit breaker)

### 3. Verification Checks

For each transaction:
- ✓ Sender matches payer
- ✓ Recipient matches expected address
- ✓ Amount >= required amount
- ✓ Transaction not already used (replay protection)

For AICF specifically:
- ✓ AICF recipient is `ENA_AICF_ADDRESS`
- ✓ AICF amount >= `required_aicf_amount`
- ✓ Both transactions from same payer

### 4. Replay Protection

Transaction hashes are stored in database after use:

```sql
CREATE TABLE used_transactions (
    tx_hash TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    amount INTEGER NOT NULL,
    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    request_id TEXT NOT NULL
);
```

Cannot reuse any transaction hash for another call.

### 5. Error Handling

If verification fails, the request is rejected with specific error:

- `400 Bad Request` - Validation error
- `400 Bad Request` - "AICF contribution missing/insufficient"
- `400 Bad Request` - "Transaction already used (replay protection)"
- `503 Service Unavailable` - RPC unavailable

## API Endpoints

### GET /v1/pricing

Returns pricing info including AICF details:

```json
{
  "fee_per_call": 10000000,
  "fee_per_token": 1000,
  "currency": "ANM",
  "base_units": 1000000000,
  "aicf_address": "anim1aicf...",
  "aicf_bp": 2500,
  "aicf_description": "AI Compute Fund - supports AI infrastructure",
  "example_call_cost": 10000000,
  "example_aicf_cost": 2500000,
  "example_service_cost": 7500000
}
```

### POST /v1/infer

Request includes payment with AICF:

**Request:**
```json
{
  "prompt": "...",
  "payment": {
    "mode": "per_call_tx",
    "payer": "anim1...",
    "tx_hash_service": "0x...",
    "tx_hash_aicf": "0x..."
  }
}
```

**Response:**
```json
{
  "ok": true,
  "answer": "...",
  "receipt": {
    "id": "req-123",
    "paid": true,
    "mode": "per_call_tx",
    "amount": 10000000,
    "service_paid": 7500000,
    "aicf_paid": 2500000,
    "aicf_required": 2500000,
    "aicf_explicit": true,
    "tx_hash_service": "0x...",
    "tx_hash_aicf": "0x..."
  }
}
```

### GET /v1/aicf/status (Credit Mode)

Returns AICF reserve status:

```json
{
  "aicf_address": "anim1aicf...",
  "reserve_balance": 15000000,
  "total_contributed": 250000000,
  "last_sweep_tx": "0x...",
  "last_sweep_at": "2026-02-18T20:00:00Z",
  "next_sweep_threshold": 100000000
}
```

## CLI Commands

### View AICF Information

```bash
animica ena aicf info
```

Output:
```
AICF (AI Compute Fund) Information:
  Address: anim1aicftest...
  Contribution: 2500 basis points (25%)
  Description: AI Compute Fund - supports AI infrastructure

Example Payment Breakdown:
  Total fee: 0.01 ANM
  → Service: 0.0075 ANM
  → AICF: 0.0025 ANM
```

### Verify AICF Transaction

```bash
animica ena aicf verify 0xdef456...
```

Output:
```
AICF Contribution Verification:
  Transaction: 0xdef456...
  Recipient: anim1aicf789...
  Amount: 0.0025 ANM
  Status: ✓ Valid AICF contribution
```

### Run Inference (Auto AICF)

```bash
animica ena infer --prompt "What is AI?" --fee-mode per_call_tx
```

Output:
```
Payment Details:
  Total required: 0.01 ANM
  Service fee: 0.0075 ANM
  AICF contribution: 0.0025 ANM (25%)

Sending service payment...
  ✓ Service payment: 0xabc123...

Sending AICF contribution...
  ✓ AICF contribution: 0xdef456...

Running inference...

✓ Inference complete!

AICF Contribution:
  Amount: 0.0025 ANM
  Required: 0.0025 ANM
  Status: ✓ Verified on-chain
  Transaction: 0xdef456...
```

## Security Considerations

### Replay Protection

- Every transaction hash can only be used once
- Database tracks all used hashes
- Attempts to reuse trigger immediate rejection

### On-Chain Verification

- All transactions are verified via RPC
- Cannot fake AICF contributions
- Blockchain serves as source of truth

### Development Mode

**NEVER** use `ENA_DEV_MODE=1` in production:
- Bypasses all payment verification
- Bypasses AICF requirement
- Only for local testing

### Rate Limiting

AICF verification is subject to rate limits:
- Per-address: 100 requests/hour
- Per-IP: 200 requests/hour

### Circuit Breaker

If RPC fails:
- Circuit opens after 5 consecutive failures
- Requests rejected with 503
- Automatically retries after timeout
- Prevents cascade failures

## Auditing AICF Contributions

### On-Chain Auditing

Anyone can verify AICF contributions:

```bash
# Get all transactions to AICF address
animica chain query-address anim1aicf... --limit 100

# Verify specific contribution
animica ena aicf verify <tx_hash>
```

### Server Logs

ENA logs all AICF contributions:

```
INFO - AICF payment verified
  payer: anim1user123...
  tx_hash_service: 0xabc...
  tx_hash_aicf: 0xdef...
  service_paid: 7500000
  aicf_paid: 2500000
  total_paid: 10000000
```

### Database Queries

Query used transactions:

```sql
SELECT tx_hash, payer, amount, used_at
FROM used_transactions
WHERE tx_hash LIKE '0xdef%'
ORDER BY used_at DESC;
```

## Troubleshooting

### "AICF contribution missing/insufficient"

**Cause**: AICF payment is missing or below requirement

**Solution**:
1. Check pricing endpoint for required AICF amount
2. Ensure AICF transaction is sent
3. Verify amount >= `example_aicf_cost` from pricing

### "Transaction already used"

**Cause**: Attempting to reuse transaction hash

**Solution**:
1. Each call requires fresh transactions
2. Do not retry with same hashes
3. Generate new payment transactions

### "Invalid AICF tx recipient"

**Cause**: AICF payment sent to wrong address

**Solution**:
1. Get AICF address from `/v1/pricing`
2. Ensure AICF transaction pays to exact address
3. Check for typos in address

### "Transaction not found"

**Cause**: Transaction not yet in mempool/chain

**Solution**:
1. Wait 2-3 seconds after sending
2. Check transaction is submitted successfully
3. Verify RPC endpoint is accessible

## Examples

### Python Example

```python
import httpx
from animica import create_transaction, sign_transaction

# Get pricing
pricing = httpx.get("https://ena.animica.org/v1/pricing").json()
service_fee = pricing["example_service_cost"]
aicf_fee = pricing["example_aicf_cost"]

# Create transactions
tx_service = create_transaction(
    to=pricing["service_address"],
    value=service_fee,
    from_address=my_address,
)
tx_aicf = create_transaction(
    to=pricing["aicf_address"],
    value=aicf_fee,
    from_address=my_address,
)

# Sign and submit
service_hash = sign_and_send(tx_service)
aicf_hash = sign_and_send(tx_aicf)

# Run inference
response = httpx.post("https://ena.animica.org/v1/infer", json={
    "prompt": "Hello!",
    "payment": {
        "mode": "per_call_tx",
        "payer": my_address,
        "tx_hash_service": service_hash,
        "tx_hash_aicf": aicf_hash,
    }
})

print(response.json()["answer"])
```

### TypeScript Example

```typescript
import axios from 'axios';
import { createTransaction, signTransaction } from '@animica/sdk';

// Get pricing
const pricing = await axios.get('https://ena.animica.org/v1/pricing');
const serviceFee = pricing.data.example_service_cost;
const aicfFee = pricing.data.example_aicf_cost;

// Create and send transactions
const serviceTx = await createAndSendTx(
  pricing.data.service_address,
  serviceFee
);
const aicfTx = await createAndSendTx(
  pricing.data.aicf_address,
  aicfFee
);

// Run inference
const response = await axios.post('https://ena.animica.org/v1/infer', {
  prompt: 'Hello!',
  payment: {
    mode: 'per_call_tx',
    payer: myAddress,
    tx_hash_service: serviceTx.hash,
    tx_hash_aicf: aicfTx.hash,
  }
});

console.log(response.data.answer);
```

## Production Checklist

Before deploying ENA with AICF to production:

- [ ] Set `ENA_AICF_ADDRESS` to real AICF multisig
- [ ] Set `ENA_AICF_BP` to agreed percentage (e.g., 2500)
- [ ] Set `ENA_REQUIRE_AICF=true`
- [ ] Set `ENA_DEV_MODE=0` (never use dev mode in prod)
- [ ] Configure proper RPC endpoint (`ENA_RPC_URL`)
- [ ] Set up database backup for used transactions
- [ ] Configure monitoring/alerts for AICF verification failures
- [ ] Test AICF verification with testnet
- [ ] Document AICF address publicly
- [ ] Set up audit logging for all AICF contributions
