# Exchange API Quick Reference

## Table of Contents
- [Schema Overview](#schema-overview)
- [Common Operations](#common-operations)
- [Database Migrations](#database-migrations)
- [Testing](#testing)
- [Double-Entry Examples](#double-entry-examples)

## Schema Overview

### Core Tables (35 total)

**Users & Auth (4 tables)**
- `users` - User accounts
- `user_profiles` - Extended profiles
- `api_keys` - API credentials
- `sessions` - Login sessions

**KYC/AML (2 tables)**
- `kyc_cases` - Verification workflows
- `kyc_documents` - Document storage

**Assets & Networks (6 tables)**
- `networks` - Blockchain networks
- `assets` - Tradable assets
- `asset_networks` - Asset x Network mapping
- `wallets` - House custody wallets
- `user_deposit_addresses` - Per-user addresses

**Markets & Trading (4 tables)**
- `markets` - Trading pairs
- `orders` - User orders
- `order_events` - Order lifecycle log
- `trades` - Executed trades

**Ledger (4 tables)** ⭐️ CORE
- `ledger_accounts` - Double-entry accounts
- `ledger_transactions` - Transaction headers
- `ledger_entries` - Debit/credit lines
- `balances_cache` - Performance cache

**Deposits/Withdrawals (3 tables)**
- `deposits` - Incoming transfers
- `withdrawals` - Outgoing transfers
- `withdrawal_approvals` - Multi-sig approvals

**System (3 tables)**
- `fee_schedules` - Fee configuration
- `audit_logs` - Audit trail
- `idempotency_keys` - Duplicate prevention

## Common Operations

### 1. Credit a Deposit

```typescript
import { prisma, LedgerService } from '@animica/exchange-api';

const ledger = new LedgerService(prisma);

await ledger.creditDeposit(
  userId,
  assetId,
  amount,
  txid,
  idempotencyKey // prevents double-credit
);
```

**Ledger entries created:**
```
DEBIT:  SYSTEM:CLEARING (asset)     amount
CREDIT: USER:AVAILABLE (asset)      amount
```

### 2. Place an Order (Lock Funds)

**BUY Order:**
```typescript
const lockAmount = calculateBuyOrderLock(price, size, feeRateBps);
await ledger.lockFunds(userId, quoteAssetId, lockAmount, orderId);
```

**SELL Order:**
```typescript
const lockAmount = calculateSellOrderLock(size);
await ledger.lockFunds(userId, baseAssetId, lockAmount, orderId);
```

**Ledger entries created:**
```
DEBIT:  USER:AVAILABLE (asset)      amount
CREDIT: USER:LOCKED (asset)         amount
```

### 3. Settle a Trade

```typescript
await ledger.settleTrade({
  buyerUserId,
  sellerUserId,
  baseAssetId,
  quoteAssetId,
  baseAmount,
  quoteAmount,
  buyerFee,
  sellerFee,
  tradeId,
});
```

**Ledger entries created:**
```
// Base asset: seller -> buyer
DEBIT:  SELLER:LOCKED (base)        size
CREDIT: BUYER:AVAILABLE (base)      size

// Quote asset: buyer -> seller (with fees)
DEBIT:  BUYER:LOCKED (quote)        price * size
CREDIT: SELLER:AVAILABLE (quote)    price * size - seller_fee
CREDIT: SYSTEM:FEE (quote)          buyer_fee + seller_fee
```

### 4. Process Withdrawal

**Request:**
```typescript
// Step 1: Lock funds
await ledger.lockFunds(userId, assetId, amount + fee, withdrawalId);
```

**Broadcast:**
```typescript
// Step 2: Transfer to hot wallet
await ledger.createTransaction({
  type: 'WITHDRAWAL_DEBIT',
  entries: [
    { accountId: userLockedAccount, direction: 'DEBIT', amount: total },
    { accountId: hotWalletAccount, direction: 'CREDIT', amount: total },
  ],
  externalRef: withdrawalId,
});
```

**Cancel/Fail:**
```typescript
// Step 3: Unlock funds back to user
await ledger.unlockFunds(userId, assetId, amount + fee, withdrawalId);
```

## Database Migrations

### Create Migration

```bash
cd services/exchange-api
pnpm prisma migrate dev --name descriptive_name
```

### Deploy to Production

```bash
pnpm prisma migrate deploy
```

### Reset Database (DEV ONLY)

```bash
pnpm prisma migrate reset
```

### View Schema

```bash
pnpm prisma studio
```

## Testing

### Run All Tests

```bash
pnpm test
```

### Run Specific Test Suite

```bash
pnpm test ledger
pnpm test invariants
```

### With Coverage

```bash
pnpm test -- --coverage
```

### Watch Mode

```bash
pnpm test:watch
```

## Double-Entry Examples

### Example 1: Simple Deposit

**Transaction:** Credit 100 USDT from BitGo webhook

```typescript
const userId = '...';
const usdtAssetId = '...';
const amount = '100.00';
const txid = 'bitgo-tx-123';

await ledger.creditDeposit(userId, usdtAssetId, amount, txid);
```

**Resulting entries:**
| Account | Type | Owner | Asset | Direction | Amount |
|---------|------|-------|-------|-----------|--------|
| System Clearing | CLEARING | SYSTEM | USDT | DEBIT | 100.00 |
| User Available | AVAILABLE | USER | USDT | CREDIT | 100.00 |

**Verification:**
- Total DEBIT = Total CREDIT ✓
- User balance increased by 100 USDT ✓

### Example 2: Buy Order

**Scenario:** User wants to buy 0.5 BTC at $50,000 with 0.1% fee

```typescript
const price = new Decimal('50000');
const size = new Decimal('0.5');
const feeRateBps = 10; // 0.1%

// Calculate lock amount: (50000 * 0.5) + (25000 * 0.001) = 25025
const lockAmount = calculateBuyOrderLock(price, size, feeRateBps);

await ledger.lockFunds(userId, usdAssetId, lockAmount, orderId);
```

**Resulting entries:**
| Account | Type | Owner | Asset | Direction | Amount |
|---------|------|-------|-------|-----------|--------|
| User Available | AVAILABLE | USER | USD | DEBIT | 25025 |
| User Locked | LOCKED | USER | USD | CREDIT | 25025 |

### Example 3: Trade Settlement

**Scenario:** Buyer and seller matched at 0.5 BTC @ $50,000

```typescript
await ledger.settleTrade({
  buyerUserId: '...',
  sellerUserId: '...',
  baseAssetId: btcAssetId,
  quoteAssetId: usdAssetId,
  baseAmount: '0.5',
  quoteAmount: '25000',
  buyerFee: '25',    // 0.1% taker
  sellerFee: '12.50', // 0.05% maker
  tradeId: 'trade-123',
});
```

**Resulting entries:**
| Account | Type | Owner | Asset | Direction | Amount |
|---------|------|-------|-------|-----------|--------|
| Seller Locked | LOCKED | SELLER | BTC | DEBIT | 0.5 |
| Buyer Available | AVAILABLE | BUYER | BTC | CREDIT | 0.5 |
| Buyer Locked | LOCKED | BUYER | USD | DEBIT | 25000 |
| Seller Available | AVAILABLE | SELLER | USD | CREDIT | 24987.50 |
| System Fee | FEE | SYSTEM | USD | CREDIT | 37.50 |

**Verification:**
- BTC: DEBIT (0.5) = CREDIT (0.5) ✓
- USD: DEBIT (25000) = CREDIT (24987.50 + 37.50) = 25025 ✓

### Example 4: Multiple Deposits (Idempotency)

**Scenario:** BitGo webhook fires twice for same deposit

```typescript
const idempotencyKey = 'bitgo-webhook-abc123';

// First call - succeeds
await ledger.creditDeposit(userId, assetId, '100', txid, idempotencyKey);

// Second call - throws error (duplicate idempotency key)
await ledger.creditDeposit(userId, assetId, '100', txid, idempotencyKey);
// Error: Unique constraint violation on idempotency_key
```

**Result:** User balance only increases once ✓

## Reconciliation

### Daily Reconciliation Job

```typescript
import { ReconciliationService } from '@animica/exchange-api';

const reconciliation = new ReconciliationService(prisma, ledger);

// Run daily reconciliation
const result = await reconciliation.reconcileAllBalances();

if (result.mismatches.length > 0) {
  // Alert ops team
  console.error('Balance mismatches detected:', result.mismatches);
  
  // Rebuild caches from ledger
  await reconciliation.rebuildBalanceCaches();
}
```

### Verify Single Transaction

```typescript
const verification = await reconciliation.verifyTransactionBalance(txId);

if (!verification.balanced) {
  console.error('Unbalanced transaction:', txId);
  console.error('Asset balances:', verification.assetBalances);
}
```

## Key Constraints

### Unique Constraints
- `users.email` - One email per user
- `assets.symbol` - Unique asset symbols
- `deposits(asset_network_id, txid, address, tag)` - Prevent duplicate deposits
- `ledger_accounts(owner_type, owner_id, account_type, asset_id)` - One account per user/asset/type
- `ledger_transactions.idempotency_key` - Prevent duplicate transactions

### Foreign Keys
- All foreign keys use `RESTRICT` to prevent data loss
- Cascade deletes only for profiles, keys, sessions (user-owned data)
- Ledger entries cannot be deleted (enforced by application)

### Check Constraints
- All amounts must be > 0 (enforced in application)
- Status enums enforced by Prisma
- Balance calculations must equal from ledger

## Performance Tips

1. **Use balance cache** for user-facing queries
2. **Reconcile daily** to catch cache drift
3. **Index by query patterns** (user_id, status, created_at)
4. **Batch operations** when possible
5. **Use SERIALIZABLE isolation** for ledger transactions

## Security Checklist

- [ ] Idempotency keys for all external events
- [ ] Validate amounts are positive
- [ ] Check sufficient balance before debits
- [ ] Verify transaction balance before commit
- [ ] Audit log all sensitive operations
- [ ] Rate limit API endpoints
- [ ] Require 2FA for withdrawals
- [ ] Multi-signature approval for large withdrawals
- [ ] Regular reconciliation runs
- [ ] Monitor for anomalies

## Troubleshooting

### Balance Mismatch

```typescript
// 1. Check if cache is out of sync
const result = await reconciliation.reconcileAccount(accountId);

if (!result.reconciled) {
  console.log('Calculated:', result.calculated);
  console.log('Cached:', result.cached);
  console.log('Difference:', result.difference);
  
  // 2. Rebuild cache from ledger
  await reconciliation.rebuildBalanceCaches();
}
```

### Unbalanced Transaction

```typescript
// Query the transaction and its entries
const tx = await prisma.ledgerTransaction.findUnique({
  where: { id: txId },
  include: {
    entries: {
      include: {
        account: {
          select: { assetId: true }
        }
      }
    }
  }
});

// Group by asset and verify balance
// This should never happen with our validation!
```

### Duplicate Deposit

```typescript
// Check if idempotency key exists
const existing = await prisma.ledgerTransaction.findFirst({
  where: { idempotencyKey: key }
});

if (existing) {
  // Return cached response, don't process again
  return cachedResponse;
}
```
