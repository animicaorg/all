# ENA - Animica LLM Inference Service

ENA is a CPU-first LLM inference service integrated with the Animica blockchain for pay-per-call access.

## Features

- **CPU-Only**: Runs on standard hardware without GPU requirements
- **Pay-Per-Call**: Micropayment integration with Animica blockchain
- **Two Payment Modes**:
  - Per-call transaction: Each inference request includes a payment transaction
  - Credit/deposit: Pre-fund an account and use credits per call
- **Model Versioning**: Support for multiple model versions with backward compatibility
- **Security**: Rate limiting, replay protection, audit logs
- **CLI Integration**: Use via `animica ena` commands

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
# List available models
animica ena models

# Get pricing information
animica ena pricing

# Run inference (per-call mode)
animica ena infer --prompt "Hello, world!" --fee-mode per_call_tx

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
└────────┬────────┘      └──────────────┘
         │
         ▼
┌─────────────────┐
│  Model Registry │
│  CPU Inference  │
└─────────────────┘
```

## API Endpoints

- `POST /v1/infer` - Run inference with payment
- `GET /v1/models` - List available models
- `GET /v1/health` - Health check
- `GET /v1/pricing` - Get pricing information
- `POST /v1/infer_stream` - Streaming inference (optional)

## Payment Modes

### Per-Call Transaction

Each inference request must include a transaction hash that:
1. Pays the required fee to the ENA service address
2. Hasn't been used for a previous inference call (replay protection)
3. Is confirmed in mempool or on-chain

### Credit/Deposit

1. Deposit ANM to the ENA service address
2. Service tracks your credit balance
3. Each call deducts credits atomically
4. Requires signature over request to prevent abuse

## Security

- **Rate Limiting**: Per-address and per-IP limits
- **Replay Protection**: Transaction hashes cannot be reused
- **Input Validation**: Strict validation of all inputs
- **Circuit Breaker**: Graceful handling of RPC outages
- **Audit Logs**: All requests logged with structured data

## Development

### Run Tests

```bash
pytest ena/tests/
```

### Dev Mode (Skip Payment Verification)

```bash
ENA_DEV_MODE=1 python -m ena.services.ena_node.main
```

**WARNING**: Never use `ENA_DEV_MODE=1` in production!

## Configuration

See `.env.example` for all available configuration options.

Key settings:
- `ENA_RPC_URL`: Animica RPC endpoint
- `ENA_SERVICE_ADDRESS`: Address to receive payments
- `ENA_FEE_PER_CALL`: Base fee per inference call
- `ENA_FEE_PER_TOKEN`: Additional fee per output token
- `ENA_DEFAULT_MODEL`: Default model to use

## License

See LICENSE.txt in the repository root.
