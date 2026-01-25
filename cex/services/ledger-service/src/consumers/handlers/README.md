# Trade Settlement Handlers

This directory contains handlers for processing ledger events from the matching engine.

## Files

### `trade_settle.ts` (IMPLEMENTED)

The main trade settlement handler implementing double-entry accounting for trade settlements.

#### Key Functions

- `handleTradeEvent(client, tradeEvent, market)` - Main entry point for trade settlement

#### Trade Settlement Flow

1. **Parse amounts** - Convert string atoms to BigInt
2. **Determine parties** - Query orders table to get maker/taker user IDs and determine buyer/seller
3. **Ensure accounts** - Create USER:AVAILABLE, USER:LOCKED, and SYSTEM:FEE accounts for all parties and assets
4. **Create ledger transaction** - Create transaction header with type 'TRADE_SETTLE'
5. **Create balanced entries**:
   - **Base asset**: seller LOCKED → buyer AVAILABLE
   - **Quote asset**: buyer LOCKED → seller AVAILABLE  
   - **Maker fee**: maker AVAILABLE → SYSTEM:FEE (from received asset)
   - **Taker fee**: taker AVAILABLE → SYSTEM:FEE (from received asset)
6. **Verify balance** - Use `verifyBalanced()` to ensure debits = credits per asset
7. **Update balances** - Update cached balances atomically
8. **Update order locks** - Increment usedAtoms to track how much has been settled

#### Trade Parties Logic

For a BUY order:
- Gets base asset (receives what they're buying)
- Pays quote asset (what they're buying with)

For a SELL order:
- Loses base asset (selling this)
- Gets quote asset (receives payment)

Example:
- Maker: BUY 1.5 BTC @ 50,000 USDT
- Taker: SELL 1.5 BTC @ 50,000 USDT

Result:
- Maker gets 1.5 BTC (base) into AVAILABLE
- Maker pays 75,000 USDT (quote) from LOCKED
- Taker pays 1.5 BTC (base) from LOCKED  
- Taker gets 75,000 USDT (quote) into AVAILABLE
- Both pay fees from the asset they received

#### Fee Policy

Fees are deducted from the asset the user **received**, not the asset they paid with:
- BUY side: fee from base asset (what they bought)
- SELL side: fee from quote asset (what they received as payment)

This is implemented by checking the side and deducting from the appropriate AVAILABLE account.

#### Invariants Enforced

1. **Debits = Credits** per asset (double-entry accounting)
2. **All amounts positive** (no zero or negative entries)
3. **No negative balances** after settlement
4. **All entries in same transaction** (atomicity)

### `order_lock.ts` (STUB)

Placeholder for order locking logic when orders are ACCEPTED.

**TODO**: Implement logic to:
- Determine which asset to lock (base for SELL, quote for BUY)
- Move funds from AVAILABLE to LOCKED
- Create order_locks record

### `order_release.ts` (STUB)

Placeholder for releasing locked funds when orders are CANCELED, EXPIRED, or REJECTED.

**TODO**: Implement logic to:
- Calculate amount to release (locked - used)
- Move funds from LOCKED back to AVAILABLE
- Update or delete order_locks record

## Database Schema

### Tables Used

```sql
-- Ledger accounts (chart of accounts)
ledger_accounts (
  id UUID PRIMARY KEY,
  account_type TEXT, -- 'USER' or 'SYSTEM'
  account_name TEXT, -- 'AVAILABLE', 'LOCKED', 'FEE', etc.
  user_id UUID,
  asset_id TEXT,
  created_at TIMESTAMP
)

-- Ledger transactions (headers)
ledger_transactions (
  id UUID PRIMARY KEY,
  tx_type TEXT, -- 'TRADE_SETTLE', 'TRANSFER', etc.
  market_id UUID,
  seq BIGINT,
  metadata JSONB,
  created_at TIMESTAMP
)

-- Ledger entries (debits and credits)
ledger_entries (
  id UUID PRIMARY KEY,
  transaction_id UUID REFERENCES ledger_transactions(id),
  account_id UUID REFERENCES ledger_accounts(id),
  asset_id TEXT,
  direction TEXT, -- 'DEBIT' or 'CREDIT'
  amount_atoms NUMERIC,
  description TEXT,
  created_at TIMESTAMP
)

-- Balances cache (derived from ledger)
balances (
  user_id UUID,
  asset_id TEXT,
  available_atoms NUMERIC,
  locked_atoms NUMERIC,
  updated_at TIMESTAMP,
  PRIMARY KEY (user_id, asset_id)
)

-- Order locks tracking
order_locks (
  order_id UUID PRIMARY KEY,
  user_id UUID,
  asset_id TEXT,
  locked_atoms NUMERIC,
  used_atoms NUMERIC,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

-- Orders (from matching engine)
orders (
  id UUID PRIMARY KEY,
  user_id UUID,
  side TEXT, -- 'BUY' or 'SELL'
  -- ... other fields
)
```

## Testing

To test the trade settlement handler:

```typescript
import { handleTradeEvent } from './handlers/trade_settle.js';
import { pool } from './db/pool.js';

const client = await pool.connect();
try {
  await client.query('BEGIN');
  
  const tradeEvent = {
    tradeId: 'trade-123',
    marketId: 'market-btc-usdt',
    makerOrderId: 'order-maker',
    takerOrderId: 'order-taker',
    priceAtoms: '50000000000', // 50,000 USDT (6 decimals)
    sizeAtoms: '150000000',    // 1.5 BTC (8 decimals)
    quoteAmountAtoms: '75000000000', // 75,000 USDT
    makerFeeAtoms: '15000000',  // 15 USDT (0.02%)
    takerFeeAtoms: '30000000',  // 30 USDT (0.04%)
    feeAsset: 'USDT',
    feeBpsMaker: 20,
    feeBpsTaker: 40,
    sequence: '12345',
    createdAt: new Date().toISOString()
  };
  
  const market = {
    id: 'market-btc-usdt',
    symbol: 'BTC/USDT',
    baseAsset: 'BTC',
    quoteAsset: 'USDT',
    makerFeeBps: 20,
    takerFeeBps: 40,
    feeAsset: 'USDT'
  };
  
  const result = await handleTradeEvent(client, tradeEvent, market);
  
  if (result.ok) {
    await client.query('COMMIT');
    console.log('Trade settled successfully');
  } else {
    await client.query('ROLLBACK');
    console.error('Trade settlement failed:', result.error);
  }
} finally {
  client.release();
}
```

## Error Handling

The handler returns `{ ok: boolean; error?: string }` to indicate success or failure.

Common errors:
- **Orders not found** - makerOrderId or takerOrderId don't exist
- **Entries not balanced** - Bug in entry creation logic
- **Negative balance** - Insufficient funds (should be prevented by matching engine)
- **Database errors** - Constraint violations, connection issues

All errors are logged with context for debugging.

## Logging

The handler logs:
- Trade received (tradeId, market, order IDs)
- Parsed amounts (size, quote, fees)
- Trade parties (maker/taker, buyer/seller)
- Accounts ensured (account IDs)
- Transaction created (txId)
- Entries created (count)
- Verification results
- Balance updates
- Order lock updates
- Success/failure status

## Future Enhancements

1. **Idempotency** - Add idempotency key to prevent duplicate settlements
2. **Reconciliation** - Add periodic reconciliation jobs to verify cache matches ledger
3. **Partial fills** - Handle multiple partial fills for the same order
4. **Fee rebates** - Support maker rebates (negative fees)
5. **Multi-asset fees** - Support fees in different assets (e.g., ANM token)

## References

- Double-entry accounting: https://en.wikipedia.org/wiki/Double-entry_bookkeeping
- Ledger patterns: https://www.moderntreasury.com/learn/ledger-101
- Trade lifecycle: https://www.investopedia.com/terms/t/trade-life-cycle.asp
