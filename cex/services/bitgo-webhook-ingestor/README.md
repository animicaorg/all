# BitGo Webhook Ingestor Service

Production-ready service for ingesting cryptocurrency deposit webhooks from BitGo and processing them through a secure, idempotent pipeline.

## Overview

The BitGo Webhook Ingestor receives real-time notifications from BitGo wallets about incoming cryptocurrency deposits, validates them, tracks confirmations, applies risk checks, and coordinates with the ledger service to credit user balances.

### Architecture

```
┌─────────────┐
│   BitGo     │
│   Webhook   │
└──────┬──────┘
       │ POST /webhook
       │ (HMAC signed)
       ▼
┌─────────────────────────────────────────────────┐
│         BitGo Webhook Ingestor Service         │
│                                                 │
│  ┌─────────────────────────────────────────┐  │
│  │  HTTP Server (Express)                  │  │
│  │  - Rate limiting (Redis)                │  │
│  │  - Signature verification               │  │
│  │  - Replay attack prevention             │  │
│  └────────────┬────────────────────────────┘  │
│               ▼                                 │
│  ┌─────────────────────────────────────────┐  │
│  │  Ingestion Pipeline                      │  │
│  │  1. Normalize webhook payload           │  │
│  │  2. Resolve address → user mapping      │  │
│  │  3. Upsert deposit (idempotent)         │  │
│  │  4. Run risk checks                     │  │
│  │  5. Create outbox entry if confirmed    │  │
│  └────────────┬────────────────────────────┘  │
│               ▼                                 │
│  ┌─────────────────────────────────────────┐  │
│  │  Background Jobs                         │  │
│  │  - Outbox processor (credit sender)     │  │
│  │  - Confirmation backfill (updates)      │  │
│  └─────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
       │
       │ NATS: ledger.deposit.credit
       ▼
┌──────────────────┐
│  Ledger Service  │
│  (Balance Credit)│
└──────────────────┘
```

## Features

- **Webhook Security**: HMAC signature verification with replay attack prevention
- **Rate Limiting**: Redis-backed rate limiting per IP address
- **Idempotency**: Multi-level idempotency guarantees (deposits, outbox, ledger)
- **Risk Checks**: Automated risk validation (amount limits, velocity, contract allowlists)
- **Confirmation Tracking**: Automatic confirmation updates via backfill job
- **Observability**: Comprehensive logging and audit trails
- **Admin API**: Management endpoints for holds, deposits, and statistics

## Setup

### Prerequisites

- Node.js 18+
- PostgreSQL 14+
- Redis 7+
- NATS 2.9+

### Installation

```bash
# Install dependencies
pnpm install

# Build
pnpm build

# Run in development mode
pnpm dev
```

### Database Schema

The service requires the following tables (managed by parent CEX schema):

- `deposits` - Main deposit records
- `deposit_outbox` - Outbox pattern for credit processing
- `deposit_audit_log` - Audit trail for all deposit events
- `deposit_addresses` - User address mappings
- `asset_networks` - Asset and network configuration
- `assets` - Asset definitions
- `networks` - Blockchain network definitions

## Configuration

### Environment Variables

```bash
# Service
SERVICE_NAME=bitgo-webhook-ingestor
PORT=3003
LOG_LEVEL=info

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/cex

# Redis
REDIS_URL=redis://localhost:6379

# NATS
NATS_URL=nats://localhost:4222

# BitGo Configuration
BITGO_WEBHOOK_SECRET=your-webhook-secret-here
BITGO_API_TOKEN=your-api-token-here  # For confirmation backfill
BITGO_ENV=test  # or 'prod'

# Rate Limiting
WEBHOOK_RATE_LIMIT_PER_MINUTE=100
WEBHOOK_REPLAY_WINDOW_SECONDS=300  # 5 minutes

# Background Jobs
CONFIRMATION_BACKFILL_INTERVAL_MS=60000  # 1 minute
OUTBOX_PROCESSOR_INTERVAL_MS=5000        # 5 seconds

# Admin API
ADMIN_KEY=your-admin-secret-key

# Ledger Service Integration
LEDGER_SERVICE_URL=http://localhost:3002
LEDGER_SERVICE_NATS_SUBJECT=ledger.deposit.credit
```

### BitGo Webhook Configuration

1. Go to BitGo dashboard → Webhooks
2. Create new webhook for your wallet
3. Set URL: `https://your-domain.com/webhook`
4. Set events: `transfer` (required)
5. Copy the webhook secret to `BITGO_WEBHOOK_SECRET`

## API Documentation

### Webhook Endpoint

#### POST /webhook

Receives BitGo webhook callbacks.

**Headers:**
- `x-bitgo-signature`: HMAC-SHA256 signature of request body
- `Content-Type`: application/json

**Request Body:**
```json
{
  "type": "transfer",
  "walletId": "wallet-id",
  "coin": "btc",
  "transfer": {
    "id": "transfer-id",
    "coin": "btc",
    "wallet": "wallet-id",
    "txid": "transaction-hash",
    "height": 12345,
    "heightId": "block-hash",
    "date": "2024-01-15T10:00:00Z",
    "confirmations": 1,
    "value": 100000000,
    "valueString": "100000000",
    "state": "unconfirmed",
    "entries": [
      {
        "address": "bc1quser...",
        "value": 100000000,
        "valueString": "100000000"
      }
    ]
  }
}
```

**Response:**
```json
{
  "status": "ok",
  "message": "Webhook processed",
  "processed": 1,
  "results": [
    {
      "depositId": "dep-123",
      "status": "DETECTED",
      "isNew": true,
      "userId": "user-456"
    }
  ]
}
```

### Admin Endpoints

All admin endpoints require `Authorization: Bearer <ADMIN_KEY>` header.

#### GET /admin/deposits/:depositId

Get deposit details by ID.

**Response:**
```json
{
  "id": "dep-123",
  "userId": "user-456",
  "assetNetworkId": "an-btc-mainnet",
  "provider": "BITGO",
  "txid": "transaction-hash",
  "address": "bc1quser...",
  "amountAtoms": "100000000",
  "confirmations": 3,
  "confirmationsRequired": 3,
  "status": "CONFIRMED",
  "detectedAt": "2024-01-15T10:00:00Z",
  "confirmedAt": "2024-01-15T10:30:00Z",
  "creditedAt": null,
  "unassigned": false,
  "riskHold": false,
  "createdAt": "2024-01-15T10:00:00Z",
  "updatedAt": "2024-01-15T10:30:00Z"
}
```

#### GET /admin/deposits?status=DETECTED&limit=100

List deposits with optional filtering.

**Query Parameters:**
- `status`: Filter by status (DETECTED, CONFIRMED, CREDITED, FAILED, HOLD)
- `limit`: Max results (default: 100)

#### POST /admin/deposits/:depositId/release-hold

Release a deposit from risk hold.

**Response:**
```json
{
  "status": "ok",
  "message": "Hold released",
  "depositId": "dep-123"
}
```

#### GET /admin/outbox?limit=100

Get pending outbox items.

#### GET /admin/stats

Get deposit statistics for last 24 hours.

**Response:**
```json
{
  "deposits": [
    {
      "status": "DETECTED",
      "count": 15,
      "unassignedCount": 2,
      "riskHoldCount": 1
    },
    {
      "status": "CONFIRMED",
      "count": 42,
      "unassignedCount": 0,
      "riskHoldCount": 0
    }
  ],
  "outbox": {
    "pending": 3,
    "processed": 39,
    "maxRetries": 2
  },
  "timestamp": "2024-01-15T12:00:00Z"
}
```

### Health Check

#### GET /healthz

Service health check.

**Response:**
```json
{
  "status": "ok",
  "service": "bitgo-webhook-ingestor",
  "postgres": true,
  "redis": true
}
```

## Deposit Lifecycle

```
DETECTED → CONFIRMED → CREDITED
   ↓           ↓
 FAILED      HOLD
```

1. **DETECTED**: Initial webhook received, deposit created
2. **CONFIRMED**: Confirmations >= required threshold
3. **CREDITED**: Balance credited to user via ledger service
4. **FAILED**: Transaction failed or removed
5. **HOLD**: Manual or automated risk hold

## Risk Checks

The service applies automated risk checks to all deposits:

1. **Amount Validation**
   - Reject zero or negative amounts
   - Flag abnormally large amounts (>100B atoms)

2. **Address Assignment**
   - Flag unassigned addresses for review

3. **Token Contract Allowlist**
   - Verify ERC20 contracts against database allowlist
   - Hold unknown contracts

4. **Velocity Check**
   - Detect high deposit frequency (>10 in 5 minutes)
   - Hold suspicious patterns

5. **Duplicate Detection**
   - Flag same txid with different addresses
   - Allow multi-output transactions

Deposits flagged by risk checks enter `HOLD` status and require manual review via admin API.

## Idempotency

The service guarantees idempotency at multiple levels:

### Deposit Level
- Unique constraint: `(asset_network_id, txid, address, tag, vout)`
- Duplicate webhooks update existing deposits using `GREATEST()` for confirmations

### Outbox Level
- Unique constraint: `(idempotency_key)` where key = `deposit:{depositId}`
- `ON CONFLICT DO NOTHING` prevents duplicate entries

### Ledger Level
- Credit commands include idempotency key
- Ledger service deduplicates using the key

## Background Jobs

### Outbox Processor

Processes pending outbox items to send deposit credits to ledger service.

- **Interval**: 5 seconds (configurable)
- **Batch Size**: 50 items per iteration
- **Retry Strategy**: Exponential backoff with 30s minimum delay
- **Max Retries**: 10 (flagged for manual intervention)

### Confirmation Backfill

Updates confirmation counts for pending deposits by querying BitGo API.

- **Interval**: 1 minute (configurable)
- **Batch Size**: 50 deposits per iteration
- **Age Threshold**: Only updates deposits older than 1 minute

## Example BitGo Payloads

### Bitcoin Transfer

```json
{
  "type": "transfer",
  "walletId": "5f9e...",
  "coin": "btc",
  "transfer": {
    "id": "5f9e...",
    "coin": "btc",
    "wallet": "5f9e...",
    "txid": "abc123...",
    "height": 700000,
    "heightId": "000000000000000000034dbd...",
    "date": "2024-01-15T10:00:00.000Z",
    "confirmations": 1,
    "value": 100000000,
    "valueString": "100000000",
    "state": "unconfirmed",
    "entries": [
      {
        "address": "bc1quser...",
        "value": 100000000,
        "valueString": "100000000"
      }
    ]
  }
}
```

### Ethereum Transfer

```json
{
  "type": "transfer",
  "walletId": "5f9e...",
  "coin": "eth",
  "transfer": {
    "id": "5f9e...",
    "coin": "eth",
    "wallet": "5f9e...",
    "txid": "0xabc123...",
    "height": 18000000,
    "heightId": "0x789def...",
    "date": "2024-01-15T10:00:00.000Z",
    "confirmations": 12,
    "value": 1000000000000000000,
    "valueString": "1000000000000000000",
    "state": "confirmed",
    "entries": [
      {
        "address": "0xuser...",
        "value": 1000000000000000000,
        "valueString": "1000000000000000000"
      }
    ]
  }
}
```

### ERC20 Token Transfer (USDT)

```json
{
  "type": "transfer",
  "walletId": "5f9e...",
  "coin": "erc20:usdt",
  "tokenContractAddress": "0xdac17f958d2ee523a2206206994597c13d831ec7",
  "transfer": {
    "id": "5f9e...",
    "coin": "erc20:usdt",
    "wallet": "5f9e...",
    "txid": "0xabc123...",
    "height": 18000000,
    "date": "2024-01-15T10:00:00.000Z",
    "confirmations": 12,
    "valueString": "1000000",
    "state": "confirmed",
    "entries": [
      {
        "address": "0xuser...",
        "valueString": "1000000"
      }
    ]
  }
}
```

## Monitoring

### Key Metrics

- **Webhook ingestion rate**: Requests per minute
- **Deposit status distribution**: Count by status
- **Risk hold rate**: Percentage of deposits on hold
- **Outbox processing lag**: Time between CONFIRMED and CREDITED
- **Confirmation backfill coverage**: Deposits updated per iteration

### Logs

All operations are logged with structured JSON:

```json
{
  "level": "info",
  "msg": "Deposit detected",
  "depositId": "dep-123",
  "userId": "user-456",
  "txid": "abc123...",
  "amountAtoms": "100000000",
  "status": "DETECTED",
  "isNew": true
}
```

### Alerts

Recommended alerts:

- Rate limit exceeded frequently (>10% of requests)
- High risk hold rate (>5%)
- Outbox processing lag >5 minutes
- Deposits stuck in DETECTED for >1 hour
- Database connection failures

## Testing

```bash
# Run tests
pnpm test

# Run with coverage
pnpm test -- --coverage
```

Tests cover:
- Webhook normalization and ingestion
- Idempotency guarantees
- Risk check logic
- Confirmation tracking
- Credit flow
- Error handling

## Security Considerations

1. **Webhook Verification**: Always verify HMAC signatures in production
2. **Replay Protection**: Enforce timestamp window for webhooks
3. **Rate Limiting**: Prevent webhook flooding attacks
4. **Admin API**: Protect with strong authentication
5. **SQL Injection**: Use parameterized queries
6. **Secrets Management**: Use environment variables, never commit secrets

## Troubleshooting

### Webhooks not processing

- Check `BITGO_WEBHOOK_SECRET` is correct
- Verify signature header is present
- Check rate limiting (Redis)
- Review logs for normalization errors

### Deposits stuck in DETECTED

- Check confirmation backfill job is running
- Verify `BITGO_API_TOKEN` is valid
- Review network confirmation requirements

### Credits not processing

- Check outbox processor is running
- Verify NATS connection
- Check ledger service availability
- Review outbox retry counts

### High risk hold rate

- Review risk check thresholds
- Check for legitimate high-value deposits
- Investigate velocity patterns

## Development

### Project Structure

```
src/
├── bitgo/              # BitGo integration
│   ├── types.ts        # Type definitions
│   ├── verify.ts       # Signature verification
│   └── normalize.ts    # Webhook normalization
├── db/
│   └── repositories/   # Database access layer
├── http/
│   ├── middleware/     # Express middleware
│   ├── routes/         # HTTP routes
│   └── server.ts       # Server setup
├── jobs/               # Background jobs
│   ├── outbox_processor.ts
│   └── confirmation_backfill.ts
├── pipeline/           # Processing pipeline
│   ├── ingest.ts       # Deposit ingestion
│   ├── risk.ts         # Risk checks
│   └── credit.ts       # Credit service integration
├── tests/              # Test suite
├── config.ts           # Configuration
└── index.ts            # Entry point
```

## License

See LICENSE file in repository root.

## Support

For issues and questions, contact the CEX platform team.
