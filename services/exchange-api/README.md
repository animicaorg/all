# Exchange API Service

A centralized exchange (CEX) service for Animica and multiple blockchain networks, implementing strict double-entry accounting with PostgreSQL and Prisma ORM.

## Architecture

This service provides the core data model and business logic for a cryptocurrency exchange, supporting:

- Multiple assets and networks (Bitcoin, Ethereum, Animica, etc.)
- BitGo-managed and Animica-native wallets
- Strict double-entry accounting ledger
- Order book trading with multiple order types
- Deposit and withdrawal processing
- KYC/AML compliance tracking
- Comprehensive audit logging
- Idempotency for all external events

## Core Principles

### 1. Double-Entry Accounting

The ledger follows strict double-entry accounting principles:

- **Every transaction must balance**: For each asset, total debits = total credits
- **No magic balances**: Balances are derived from the ledger, not stored separately
- **Immutable entries**: Ledger entries can never be updated or deleted
- **Audit trail**: Every transaction has a clear external reference

### 2. Data Model

#### Users & Authentication
- `users`: Core user accounts with email/phone and status
- `user_profiles`: Extended profile information
- `api_keys`: API key management with scopes and IP allowlists
- `sessions`: Session management for web/mobile clients

#### KYC/AML
- `kyc_cases`: KYC verification workflows
- `kyc_documents`: Document storage references

#### Assets & Networks
- `networks`: Blockchain networks (BTC, ETH, Animica, etc.)
- `assets`: Tradable assets (BTC, ETH, USDT, ANM, etc.)
- `asset_networks`: Asset deployment per network (e.g., USDT on Ethereum)
- `wallets`: House wallets for custody
- `user_deposit_addresses`: Per-user deposit addresses

#### Markets & Trading
- `markets`: Trading pairs (ANM-USD, BTC-USDT, etc.)
- `orders`: User orders with lifecycle tracking
- `order_events`: Append-only order event log
- `trades`: Executed trades with fees

#### Ledger (Core)
- `ledger_accounts`: Double-entry accounts (AVAILABLE, LOCKED, FEE, etc.)
- `ledger_transactions`: Transaction headers (journal entries)
- `ledger_entries`: Individual debit/credit entries
- `balances_cache`: Performance cache (derived from ledger)

#### Deposits & Withdrawals
- `deposits`: Incoming blockchain transactions
- `withdrawals`: Outgoing transactions with approval workflow
- `withdrawal_approvals`: Multi-signature approval tracking

#### Fees & Audit
- `fee_schedules`: Configurable fee structures
- `audit_logs`: Comprehensive audit trail
- `idempotency_keys`: Prevent duplicate processing

## Invariants

The system enforces these invariants at all times:

1. **DOUBLE-ENTRY BALANCE**: For any ledger transaction, debits = credits per asset
2. **IMMUTABILITY**: Ledger entries are never updated or deleted
3. **POSITIVE AMOUNTS**: All entry amounts must be > 0
4. **ACCOUNT CONSISTENCY**: Balances from ledger must match cache
5. **NO NEGATIVE BALANCES**: User available balances cannot go negative
6. **FUND LOCKING**: Orders lock sufficient funds before acceptance
7. **TRADE SETTLEMENT**: Trades transfer exact amounts with correct fees
8. **IDEMPOTENCY**: External events are processed exactly once
9. **ATOMIC OPERATIONS**: All ledger operations are atomic
10. **AUDIT TRAIL**: All transactions have external references

## Double-Entry Rules

### Deposit Processing

When a deposit is confirmed:
```
DEBIT:  SYSTEM:CLEARING (asset)    amount
CREDIT: USER:AVAILABLE (asset)     amount
```

### Order Placement

When placing a BUY order:
```
DEBIT:  USER:AVAILABLE (quote)     price * size + fees
CREDIT: USER:LOCKED (quote)        price * size + fees
```

When placing a SELL order:
```
DEBIT:  USER:AVAILABLE (base)      size
CREDIT: USER:LOCKED (base)         size
```

### Trade Settlement

When a trade executes:
```
// Base asset transfer (seller -> buyer)
DEBIT:  SELLER:LOCKED (base)       size
CREDIT: BUYER:AVAILABLE (base)     size

// Quote asset transfer (buyer -> seller, minus fees)
DEBIT:  BUYER:LOCKED (quote)       price * size
CREDIT: SELLER:AVAILABLE (quote)   price * size - seller_fee
CREDIT: SYSTEM:FEE (quote)         buyer_fee + seller_fee
```

### Withdrawal Processing

On withdrawal request:
```
DEBIT:  USER:AVAILABLE (asset)     amount + fee
CREDIT: USER:LOCKED (asset)        amount + fee
```

On broadcast:
```
DEBIT:  USER:LOCKED (asset)        amount + fee
CREDIT: SYSTEM:HOT_WALLET (asset)  amount + fee
```

On failure/cancellation:
```
DEBIT:  USER:LOCKED (asset)        amount + fee
CREDIT: USER:AVAILABLE (asset)     amount + fee
```

## Setup

### Prerequisites

- Node.js >= 18.17
- PostgreSQL >= 14
- pnpm >= 9.0.0

### Installation

```bash
cd services/exchange-api
pnpm install
```

### Database Setup

1. Create a PostgreSQL database:
```bash
createdb exchange_api
```

2. Configure environment:
```bash
cp .env.example .env
# Edit .env and set DATABASE_URL
```

3. Run migrations:
```bash
pnpm db:migrate
```

4. Generate Prisma client:
```bash
pnpm db:generate
```

## Development

### Running Tests

```bash
# Run all tests
pnpm test

# Run tests in watch mode
pnpm test:watch

# Run tests with coverage
pnpm test -- --coverage
```

### Database Management

```bash
# Create a new migration
pnpm db:migrate

# Deploy migrations (production)
pnpm db:migrate:deploy

# Push schema changes (development only)
pnpm db:push

# Open Prisma Studio (database GUI)
pnpm db:studio
```

### Code Quality

```bash
# Lint code
pnpm lint

# Build TypeScript
pnpm build
```

## Usage

### Basic Ledger Operations

```typescript
import { prisma } from './src/db/client.js';
import { LedgerService } from './src/services/ledger.js';

const ledger = new LedgerService(prisma);

// Credit a deposit
await ledger.creditDeposit(
  userId,
  assetId,
  '100.50',
  'txid-abc123',
  'unique-idempotency-key'
);

// Lock funds for an order
await ledger.lockFunds(
  userId,
  assetId,
  '50.25',
  'order-xyz789'
);

// Settle a trade
await ledger.settleTrade({
  buyerUserId,
  sellerUserId,
  baseAssetId,
  quoteAssetId,
  baseAmount: '0.5',
  quoteAmount: '5000',
  buyerFee: '15',
  sellerFee: '7.50',
  tradeId: 'trade-123',
});
```

### Reconciliation

```typescript
import { ReconciliationService } from './src/services/reconciliation.js';

const reconciliation = new ReconciliationService(prisma, ledger);

// Reconcile all balances
const result = await reconciliation.reconcileAllBalances();

if (result.mismatches.length > 0) {
  console.error('Balance mismatches detected:', result.mismatches);
  // Alert operations team
}

// Rebuild balance caches from ledger
await reconciliation.rebuildBalanceCaches();
```

## Extending the Model

### Adding a New Asset

1. Insert into `assets` table
2. Add network mappings in `asset_networks`
3. Configure deposit/withdrawal parameters
4. Ledger accounts are created automatically on first use

### Adding a New Market

1. Ensure base and quote assets exist
2. Insert into `markets` table with trading parameters
3. Configure fee schedule in `fee_schedules`
4. Market is ready for order placement

### Adding a New Network

1. Insert into `networks` table
2. Configure chain parameters (confirmations, RPC URL, etc.)
3. Set up wallets in `wallets` table
4. Map assets to network in `asset_networks`

## Security Considerations

### Database Constraints

- All foreign keys use `RESTRICT` or `CASCADE` appropriately
- Unique constraints prevent duplicate deposits/withdrawals
- Check constraints enforce positive amounts
- Enum types enforce valid states

### Code-Level Guards

- Ledger service validates balance before operations
- All transactions use `SERIALIZABLE` isolation level
- Idempotency keys prevent double-processing
- Amount validation enforces positive values

### Audit Trail

- Every operation logged in `audit_logs`
- Ledger entries are immutable
- All transactions have external references
- User actions tracked with IP and user agent

## Monitoring & Alerts

### Daily Reconciliation

Run automated reconciliation daily:

```bash
node scripts/daily-reconciliation.js
```

Alert if:
- Balance mismatches detected
- Unbalanced transactions found
- Ledger immutability violations

### Performance Metrics

Monitor:
- Balance cache hit rate
- Transaction processing time
- Database connection pool utilization
- Failed transaction rate

## Disaster Recovery

### Balance Reconstruction

If balance caches become corrupted:

```typescript
await reconciliation.rebuildBalanceCaches();
```

All balances are recalculated from immutable ledger entries.

### Audit Trail

The ledger provides a complete audit trail:
- All transactions are immutable
- Every change has a timestamp
- External references link to source events
- Cryptographic proofs available for blockchain events

## Future Enhancements

- [ ] Add database triggers for ledger immutability enforcement
- [ ] Implement real-time balance reconciliation
- [ ] Add support for margin trading
- [ ] Implement stop-loss orders
- [ ] Add support for recurring buys/sells
- [ ] Implement maker/taker rebates
- [ ] Add support for staking/earning products
- [ ] Implement cross-chain atomic swaps

## Contributing

See main repository [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## License

Apache-2.0 - See [LICENSE](../../LICENSE.txt)
