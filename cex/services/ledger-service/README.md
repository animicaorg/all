# Ledger Service

**Double-entry accounting service for the Animica CEX**

The Ledger Service is the financial source of truth that maintains user balances with strict double-entry bookkeeping. It consumes trade and order events from the matching engine via NATS and produces balanced ledger transactions with proper audit trails.

## Features

- ✅ **Strict double-entry accounting** - Every transaction is balanced (debits = credits)
- ✅ **Atomic BigInt arithmetic** - No floating point errors
- ✅ **Exactly-once processing** - Idempotent event consumption with sequence tracking
- ✅ **Append-only ledger** - Immutable audit trail
- ✅ **Balance reconciliation** - Periodic jobs to verify balance cache against ledger
- ✅ **Admin API** - Read-only endpoints for querying balances and ledger entries
- ✅ **Health monitoring** - Continuous checks for gaps, negative balances, and reconciliation status

## Architecture

```
┌─────────────────┐      NATS Events       ┌──────────────────┐
│ Matching Engine │ ───────────────────▶   │ Ledger Service   │
│   (Outbox)      │  TradeCreated          │   (Consumer)     │
└─────────────────┘  OrderAccepted, etc.   └──────────────────┘
                                                     │
                                                     ▼
                                           ┌─────────────────┐
                                           │   PostgreSQL    │
                                           │                 │
                                           │ • ledger_accounts
                                           │ • ledger_transactions
                                           │ • ledger_entries
                                           │ • balances_cache
                                           │ • order_locks
                                           └─────────────────┘
```

## Database Schema

### Core Ledger Tables

**ledger_accounts** - Chart of accounts
- Each user has: `USER:AVAILABLE` and `USER:LOCKED` per asset
- System accounts: `SYSTEM:FEE`, `SYSTEM:CLEARING`, `SYSTEM:INSURANCE`

**ledger_transactions** - Transaction headers
- Type: `TRADE_SETTLE`, `TRANSFER`, `DEPOSIT`, `WITHDRAWAL`, `FEE`
- Links to multiple ledger_entries
- Stores metadata (trade_id, order_ids, sequence)

**ledger_entries** - Individual debits and credits
- Append-only, never updated
- Must balance per transaction per asset
- Uses BigInt atoms (no decimals)

### Supporting Tables

**ledger_event_offsets** - Track processed event sequences per market
**order_locks** - Track locked funds per order for release calculation
**balances_cache** - Fast balance lookups (recomputed from ledger in reconciliation)
**reconciliation_reports** - Audit trail of reconciliation runs

## Double-Entry Bookkeeping

### Trade Settlement Example

When a BUY order (taker) matches a SELL order (maker):

```
Trade: 1.5 BTC @ 50,000 USDT
Maker Fee: 10 bps (0.1%)
Taker Fee: 20 bps (0.2%)
```

**Ledger Entries:**

```
Base Asset (BTC):
  DEBIT  seller:LOCKED      -1.5 BTC
  CREDIT buyer:AVAILABLE    +1.5 BTC

Quote Asset (USDT):
  DEBIT  buyer:LOCKED       -75,000 USDT
  CREDIT seller:AVAILABLE   +75,000 USDT

Maker Fee (USDT):
  DEBIT  maker:AVAILABLE    -75 USDT (0.1% of 75,000)
  CREDIT SYSTEM:FEE         +75 USDT

Taker Fee (BTC):
  DEBIT  taker:AVAILABLE    -0.003 BTC (0.2% of 1.5)
  CREDIT SYSTEM:FEE         +0.003 BTC
```

**Invariants:**
- Sum of debits = Sum of credits (per asset)
- All amounts are positive BigInt atoms
- No negative balances after application

## Event Processing Flow

1. **Receive event** from NATS (trade or order event)
2. **Start SERIALIZABLE transaction**
3. **Check idempotency** - Already processed?
4. **Validate sequence** - Monotonic? (seq = last_seq + 1)
5. **Execute handler** - Create balanced ledger transaction
6. **Verify invariants** - Debits = Credits
7. **Update balances cache** - Atomic with ledger writes
8. **Mark processed** - Update offsets and processed_events
9. **Commit transaction**
10. **ACK message**

## API Endpoints

### Public

- `GET /health` - Health check with database and sequence gap status

### Admin (requires `X-Admin-Key` header)

- `GET /balances/:userId` - Get user's balances across all assets
- `GET /ledger/accounts/:userId` - Get user's ledger accounts
- `GET /ledger/tx/:id` - Get transaction with all entries
- `GET /ledger/account/:accountId/entries?limit=100&offset=0` - Get account entries
- `POST /reconcile/run` - Trigger reconciliation job
- `GET /reconcile/latest` - Get latest reconciliation report

## Configuration

Environment variables:

```bash
# Service
SERVICE_NAME=ledger-service
PORT=3003
LOG_LEVEL=info

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/cex

# NATS
NATS_URL=nats://localhost:4222

# Redis (for distributed locks if needed)
REDIS_URL=redis://localhost:6379

# Admin API
ADMIN_KEY=your-secret-key

# Reconciliation
AUTO_FIX_BALANCES=false
RECONCILE_INTERVAL_MS=86400000  # 24 hours
HEALTH_CHECK_INTERVAL_MS=60000  # 1 minute
```

## Running Locally

### Prerequisites

1. PostgreSQL running with CEX database
2. NATS running
3. Migrations applied: `pnpm --filter @cex/db migrate`

### Development

```bash
cd cex/services/ledger-service
pnpm install
pnpm dev
```

### Production

```bash
pnpm build
node dist/index.js
```

## Testing

```bash
# Run unit tests
pnpm test

# Run specific test file
pnpm test money.test.ts

# Run with coverage
pnpm test --coverage
```

### Test Structure

- `src/tests/money.test.ts` - BigInt arithmetic and conversions
- `src/tests/invariants.test.ts` - Double-entry validation
- Integration tests require database (TODO)

## Jobs

### Reconciliation

Recomputes all balances from the ledger and compares to the cache:

```bash
curl -X POST http://localhost:3003/reconcile/run \
  -H "X-Admin-Key: your-key"
```

**Auto-fix mode** (use with caution):
```bash
AUTO_FIX_BALANCES=true pnpm dev
```

### Health Check

Monitors:
- Database connectivity
- Sequence gaps (missing events)
- Negative balances (should never happen)
- Recent reconciliation status

```bash
curl http://localhost:3003/health
```

## Monitoring

### Key Metrics

- **Event processing rate** - Events/second per market
- **Lag** - Time between event creation and processing
- **Reconciliation mismatches** - Should be zero
- **Sequence gaps** - Should be zero
- **Negative balances** - Should be zero

### Alerts

- Reconciliation finds mismatches
- Sequence gaps detected
- Health check fails
- Processing rate drops significantly

## Security

1. **Admin endpoints** - Protected by `X-Admin-Key` header
2. **SERIALIZABLE transactions** - Prevent race conditions
3. **Append-only ledger** - Audit trail cannot be modified
4. **Balance guards** - Prevent negative balances
5. **Idempotency** - Prevent duplicate credits/debits

## Troubleshooting

### Reconciliation finds mismatches

1. Check logs for errors during event processing
2. Look for sequence gaps in `ledger_event_offsets`
3. Verify ledger entries balance with `SELECT` queries
4. Check for concurrent writers (should be only one consumer per market)
5. Run with `AUTO_FIX_BALANCES=true` if source of truth is confirmed

### Sequence gaps

1. Check NATS outbox publisher is running
2. Check for failed events in matching engine logs
3. Manually backfill missing sequences (TODO: implement backfill job)

### Negative balances

This should never happen. If it does:
1. Check logs for the transaction that caused it
2. Check if guards were bypassed
3. Review the handler logic
4. File an incident report

## Development

### Adding a new event handler

1. Create handler in `src/consumers/handlers/`
2. Implement with signature: `(client, event, market) => Promise<{ ok: boolean; error?: string }>`
3. Ensure idempotency and balance checks
4. Wire into `nats_consumer.ts`
5. Add tests

### Adding a new account type

1. Update `AccountName` type in `src/domain/types.ts`
2. Update migration if needed
3. Update `ensureAccounts` logic
4. Document the account purpose

## License

Proprietary - Animica
