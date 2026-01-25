# Matching Engine Service

A fully deterministic matching engine for centralized exchange with strict price-time priority (FIFO).

## Features

- **Deterministic Matching**: Price-time priority with FIFO ordering within price levels
- **Order Types**: LIMIT and MARKET orders
- **Time-in-Force**: GTC, IOC, FOK, POST_ONLY support
- **Cancel & Replace**: Atomic order amendments with idempotency
- **Maker/Taker Fees**: Deterministic fee calculation with rounding policy
- **Event Publishing**: Exactly-once semantics via outbox pattern to NATS
- **Single-Writer Model**: One engine instance per market for concurrency safety
- **Replayability**: Full state recovery from database

## Architecture

```
┌─────────────────┐
│  NATS Commands  │
│ (order.submit)  │
└────────┬────────┘
         │
         v
┌─────────────────┐
│ Market Worker   │  <-- Single writer per market
│ (Idempotency)   │
└────────┬────────┘
         │
         v
┌─────────────────┐
│ Matching Engine │  <-- In-memory orderbook
│   (OrderBook)   │
└────────┬────────┘
         │
         v
┌─────────────────┐      ┌──────────────┐
│   PostgreSQL    │──────>│ Outbox Table │
│ (Orders/Trades) │      └──────┬───────┘
└─────────────────┘             │
                                v
                        ┌───────────────┐
                        │ NATS Publisher│
                        │ (Trade Events)│
                        └───────────────┘
```

## Order Lifecycle

```
NEW -> ACCEPTED -> [PARTIAL_FILL] -> FILLED
                 \-> CANCELED
                 \-> REJECTED
                 \-> EXPIRED
                 \-> CANCELED_REPLACED
```

## Determinism Guarantees

### Price-Time Priority
- Orders are matched strictly by price (best first)
- Within a price level, orders are matched by:
  1. `accepted_at` timestamp (earlier first)
  2. `order_id` lexicographic order (tie-breaker)

### Atomic Values
- All prices and sizes use `BigInt` "atoms" to avoid floating-point issues
- Conversions: `decimal * 10^decimals = atoms`
- Example: `123.45` with 8 decimals = `12345000000n` atoms

### Fee Rounding
- Fees are calculated on quote amount (price × size)
- Always round **UP** to prevent dust accumulation
- Formula: `fee = (amount * bps) / 10000`, round up remainder

### Sequence Numbers
- Each market has a monotonic sequence counter
- All events include sequence for deterministic ordering
- Replay uses sequence to reconstruct exact state

## Database Schema

See `cex/packages/db/src/migrations/002_matching_engine.js` for full schema.

Key tables:
- `markets`: Market configuration (tick, step, fees)
- `orders`: Order records with status lifecycle
- `trades`: Executed trades with fees
- `order_events`: Audit trail of order events
- `market_sequence`: Sequence tracking per market
- `outbox_events`: Event publishing queue
- `idempotency_keys`: Command deduplication

## API Usage

### Place Limit Order

```typescript
const result = await marketWorker.placeLimitOrder({
  userId: "user-123",
  clientOrderId: "my-order-1",
  marketId: "market-uuid",
  side: "BUY",
  priceAtoms: 12345000000n, // 123.45 with 8 decimals
  sizeAtoms: 100000000n,    // 1.0 with 8 decimals
  timeInForce: "GTC",
  postOnly: false,
  idempotencyKey: "unique-key-1"
});

if (result.success) {
  console.log(`Order ${result.order.id} created`);
  console.log(`Fills: ${result.fills.length}`);
  console.log(`Trades: ${result.trades.length}`);
}
```

### Place Market Order

```typescript
const result = await marketWorker.placeMarketOrder({
  userId: "user-123",
  clientOrderId: "my-market-order",
  marketId: "market-uuid",
  side: "SELL",
  sizeAtoms: 100000000n,
  idempotencyKey: "unique-key-2"
});
```

### Cancel Order

```typescript
const result = await marketWorker.cancelOrder({
  userId: "user-123",
  orderId: "order-uuid",
  idempotencyKey: "unique-key-3"
});
```

### Replace Order

```typescript
const result = await marketWorker.replaceOrder({
  userId: "user-123",
  orderId: "order-uuid",
  newPriceAtoms: 12350000000n, // New price
  newSizeAtoms: 200000000n,    // New size
  idempotencyKey: "unique-key-4"
});
```

## Running Locally

### Prerequisites

```bash
# Install dependencies
pnpm install

# Set up environment variables
cp .env.example .env
# Edit .env with your DB/NATS/Redis credentials
```

### Run Migrations

```bash
cd cex/packages/db
pnpm migrate
```

### Start Service

```bash
cd cex/services/matching-engine
pnpm dev
```

## Testing

### Unit Tests

```bash
pnpm test
```

Tests include:
- Orderbook correctness (FIFO, price-time priority)
- Matching logic (limit, market, partial fills)
- Fee calculations
- Deterministic tie-breakers
- Cancel and replace operations

### Determinism Tests

Golden data tests ensure stable behavior:
```bash
pnpm test tests/engine.sim.test.ts
```

These tests:
- Feed fixed command sequences
- Assert deterministic outcomes
- Save golden JSON snapshots
- Verify stability across runs

## Event Publishing

Events are published to NATS subjects:

### Order Events
- Subject: `cex.order.event.{marketId}`
- Types: ACCEPTED, PARTIAL_FILL, FILLED, CANCELED, REJECTED, CANCELED_REPLACED

### Trade Events
- Subject: `cex.trade.event.{marketId}`
- Contains: trade details, fees, maker/taker order IDs

### Guarantees
- Exactly-once semantics via outbox pattern
- Idempotent consumers can dedup by `(market_id, seq)` or `key`
- Events are ordered by sequence within each market

## Concurrency Model

### Single-Writer Per Market
- One `MarketWorker` instance processes all commands for a market
- Uses NATS queue groups or DB advisory locks
- No concurrent writes to the same market's orderbook

### Horizontal Scaling
- Each market can run on a separate worker
- Markets are independent and can scale out
- Worker assignment via consistent hashing or manual partitioning

## Configuration

Environment variables:

```bash
SERVICE_NAME=matching-engine
PORT=3000
LOG_LEVEL=info

# Database
DB_HOST=localhost
DB_PORT=5432
DB_USER=cex
DB_PASSWORD=secret
DB_NAME=cex_db

# NATS
NATS_URL=nats://localhost:4222

# Redis
REDIS_URL=redis://localhost:6379

# Matching Engine
PRICE_DECIMALS=8
SIZE_DECIMALS=8
```

## Monitoring

Health check endpoint:
```bash
curl http://localhost:3000/healthz
```

Returns:
```json
{
  "status": "ok",
  "service": "matching-engine",
  "postgres": true,
  "redis": true,
  "nats": "open"
}
```

## Production Considerations

### Database Indexes
All critical queries are indexed:
- `(market_id, status)` for open order lookups
- `(market_id, accepted_at)` for deterministic ordering
- `(market_id, sequence)` for event replay

### Idempotency TTL
- Default: 24 hours (86400 seconds)
- Configurable per command type
- Cleanup job recommended for expired keys

### Outbox Polling
- Default: 1 second interval
- Adjust based on throughput requirements
- Consider separate publisher instances for high volume

### Sequence Overflow
- Uses `bigint` (int64) for sequences
- Max value: 9,223,372,036,854,775,807
- At 10,000 trades/sec: ~29 million years

## Troubleshooting

### Orders not matching
1. Check price tick/step validation
2. Verify timestamps are deterministic (use fixed times in tests)
3. Ensure orderbook rebuild uses sorted open orders

### Duplicate events
1. Verify outbox `published_at` is set only after NATS ack
2. Check consumers implement deduplication by key or sequence
3. Review idempotency key uniqueness

### Sequence gaps
1. Sequences should be strictly monotonic
2. Gaps indicate failed transactions (expected)
3. Replay skips gaps automatically

## References

- [CEX Common Package](../packages/common/)
- [Database Schema](../packages/db/src/migrations/)
- [NATS Documentation](https://docs.nats.io/)
- [Outbox Pattern](https://microservices.io/patterns/data/transactional-outbox.html)
