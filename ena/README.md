# ENA - Animica LLM Inference Service

ENA is a CPU-first LLM inference service integrated with the Animica blockchain for pay-per-call access with mandatory AICF (AI Compute Fund) contributions.

## Features

- **CPU-Only**: Runs on standard hardware without GPU requirements
- **Pay-Per-Call**: Micropayment integration with Animica blockchain
- **AICF Integration**: Every paid call contributes to the AI Compute Fund (on-chain auditable)
- **Two Payment Modes**:
  - Per-call transaction: Each inference request includes payment transactions (service + AICF)
  - Credit/deposit: Pre-fund an account and use credits per call (with AICF reserve)
- **Model Versioning**: Support for multiple model versions with backward compatibility
- **Security**: Rate limiting, replay protection, audit logs
- **CLI Integration**: Use via `animica ena` commands

## AICF (AI Compute Fund)

Every ENA payment includes a mandatory contribution to AICF, which supports AI infrastructure development:

- **Configurable Contribution**: Set via `ENA_AICF_BP` (basis points, default 2500 = 25%)
- **On-Chain Verification**: AICF payments are verified on-chain before serving responses
- **Transparency**: All contributions are publicly auditable via blockchain
- **Dual Payment**: Payments split between service operator and AICF

### Payment Flow

**Per-Call Mode:**
```
User sends TWO transactions:
1. Service payment → ENA_SERVICE_ADDRESS (75% of total)
2. AICF contribution → ENA_AICF_ADDRESS (25% of total)

ENA verifies both transactions before serving response.
```

**Credit Mode:**
```
User deposits → Split into:
- Service credits (75%)
- AICF reserve (25%)

Per call:
- Deduct from service credits
- Deduct from AICF reserve
- Periodically sweep AICF reserve to AICF_ADDRESS
```

## Quick Start

### 1. Start the ENA Node

```bash
# Set up environment
cp .env.example .env
# Edit .env with your configuration

# Start the service
python -m ena.services.ena_node.main
```

### 2. Use via Animica CLI

```bash
# Get pricing (includes AICF info)
animica ena pricing

# View AICF information
animica ena aicf info

# Run inference (per-call mode with AICF)
# This will send TWO transactions:
#   1. Service payment
#   2. AICF contribution
animica ena infer --prompt "Hello, world!" --fee-mode per_call_tx

# Verify an AICF contribution
animica ena aicf verify <tx_hash>

# List available models
animica ena models

# Deposit credits (credit mode)
animica ena deposit --amount 10

# Run inference (credit mode)
animica ena infer --prompt "Hello, world!" --fee-mode credit
```

## Architecture

```
┌─────────────────┐
│  Animica CLI    │
│  ena commands   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────┐
│   ENA Node      │◄────►│ Animica RPC  │
│  FastAPI Server │      │   Mainnet    │
│  + AICF verify  │      └──────────────┘
└────────┬────────┘              ▲
         │                       │
         ▼                       │ Verify on-chain
┌─────────────────┐      ┌──────┴───────┐
│  Model Registry │      │ AICF Address │
│  CPU Inference  │      │  anim1...    │
└─────────────────┘      └──────────────┘
```

## API Endpoints

- `POST /v1/infer` - Run inference with payment (requires AICF contribution)
- `GET /v1/models` - List available models
- `GET /v1/health` - Health check
- `GET /v1/pricing` - Get pricing information (includes AICF details)
- `POST /v1/infer_stream` - Streaming inference (optional)

## Payment Modes

### Per-Call Transaction (with AICF)

Each inference request must include TWO transaction hashes:

1. **Service Payment** (`tx_hash_service`):
   - Pays service fee to `ENA_SERVICE_ADDRESS`
   - Hasn't been used for a previous call (replay protection)
   - Confirmed in mempool or on-chain

2. **AICF Contribution** (`tx_hash_aicf`):
   - Pays AICF fee to `ENA_AICF_ADDRESS`
   - Amount >= required AICF contribution (basis points)
   - Hasn't been used for a previous call (replay protection)
   - Confirmed in mempool or on-chain

**Request format:**
```json
{
  "prompt": "...",
  "model": "ena.latest",
  "maxTokens": 200,
  "payment": {
    "mode": "per_call_tx",
    "payer": "anim1...",
    "tx_hash_service": "0x...",
    "tx_hash_aicf": "0x..."
  }
}
```

**Response includes AICF receipt:**
```json
{
  "ok": true,
  "answer": "...",
  "receipt": {
    "id": "...",
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

### Credit/Deposit (with AICF Reserve)

1. Deposit ANM to the ENA service address
2. Service automatically splits deposit:
   - Service credits: Available for inference calls
   - AICF reserve: Set aside for AICF contributions
3. Each call deducts from both pools
4. AICF reserve is periodically swept to AICF address
5. Requires signature over request to prevent abuse

**IMPORTANT**: In credit mode, AICF contributions are reserved but swept separately. The service maintains transparency by providing sweep status via `/v1/aicf/status`.

## Security

- **Rate Limiting**: Per-address and per-IP limits
- **Replay Protection**: Transaction hashes cannot be reused (both service and AICF)
- **AICF Enforcement**: All requests must include valid AICF contribution
- **Input Validation**: Strict validation of all inputs
- **Circuit Breaker**: Graceful handling of RPC outages
- **Audit Logs**: All requests logged with structured data including AICF contributions

## Configuration

See `.env.example` for all available configuration options.

Key settings:
- `ENA_RPC_URL`: Animica RPC endpoint
- `ENA_SERVICE_ADDRESS`: Address to receive service payments
- `ENA_AICF_ADDRESS`: AI Compute Fund address
- `ENA_AICF_BP`: AICF contribution in basis points (2500 = 25%)
- `ENA_REQUIRE_AICF`: Enforce AICF requirement (true in production)
- `ENA_FEE_PER_CALL`: Base fee per inference call
- `ENA_FEE_PER_TOKEN`: Additional fee per output token
- `ENA_DEFAULT_MODEL`: Default model to use

## AICF Verification

The ENA node performs comprehensive AICF verification:

1. **Validates payer/addresses**: Bech32 format validation
2. **Fetches transaction data**: From RPC (mempool or chain)
3. **Checks recipients**: Service and AICF addresses must match
4. **Verifies amounts**:
   - `aicf_amount >= required_aicf_amount`
   - `service_amount >= required_service_amount`
   - Both amounts are positive integers
5. **Replay protection**: Transactions cannot be reused
6. **Returns structured receipt**: Full transparency on contributions

If AICF verification fails, the request is rejected with error code indicating the specific failure (missing/insufficient contribution, wrong recipient, etc.).

## Development

### Run Tests

```bash
# Run all ENA tests
pytest ena/tests/ -v

# Run AICF verification tests specifically
pytest ena/tests/test_aicf_verify.py -v
```

### Dev Mode (Skip Payment Verification)

```bash
ENA_DEV_MODE=1 python -m ena.services.ena_node.main
```

**WARNING**: Never use `ENA_DEV_MODE=1` in production! This bypasses all payment and AICF verification.

### Testing AICF Integration

1. **Start testnet/devnet** with AICF address configured
2. **Set environment variables**:
   ```bash
   export ENA_AICF_ADDRESS=anim1aicftest...
   export ENA_AICF_BP=2500
   export ENA_REQUIRE_AICF=true
   ```
3. **Test two-transaction flow**:
   ```bash
   # Send service payment
   animica tx send --to $ENA_SERVICE_ADDRESS --value 7500000
   
   # Send AICF payment
   animica tx send --to $ENA_AICF_ADDRESS --value 2500000
   
   # Run inference with both hashes
   animica ena infer --prompt "test" --fee-mode per_call_tx
   ```

### Troubleshooting

**"AICF contribution missing/insufficient"**
- Check that both transactions (service + AICF) are sent
- Verify AICF amount is >= required (check `/v1/pricing` for calculation)
- Ensure transactions are from the same payer address

**"Transaction already used (replay protection)"**
- Cannot reuse transaction hashes
- Each inference call requires fresh transactions
- Check database for used transaction tracking

**"Payment verification failed: Transaction not found"**
- Wait a few seconds for transaction propagation
- Check transaction is in mempool or confirmed
- Verify RPC endpoint is accessible

## License

See LICENSE.txt in the repository root.
