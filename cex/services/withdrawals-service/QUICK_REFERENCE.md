# Test Suite Quick Reference

## Test Files Overview

```
src/tests/
├── README.md                    # Comprehensive documentation
├── helpers.ts                   # Mocks, fixtures, utilities (548 lines)
├── request.lock.test.ts         # Request creation & locking (431 lines, 18 tests)
├── risk.policy.test.ts          # Risk evaluation & policy (452 lines, 18 tests)
├── approve.submit.test.ts       # Approvals & BitGo (481 lines, 15 tests)
├── webhook.status.test.ts       # Webhook processing (482 lines, 17 tests)
├── idempotency.test.ts          # Comprehensive idempotency (500 lines, 18 tests)
└── outbox.worker.test.ts        # Outbox pattern & retries (592 lines, 16 tests)

Total: 3,486 lines | 89+ tests | 7 files
```

## Running Tests

```bash
# All tests
npm test

# Specific file
npx vitest run src/tests/request.lock.test.ts

# Watch mode
npx vitest watch

# Coverage
npx vitest run --coverage
```

## Test Coverage Checklist

### ✅ Request & Lock (request.lock.test.ts)
- [x] Creates withdrawal and locks funds once
- [x] Idempotency: same key returns same withdrawal
- [x] Validates amount > 0, min/max limits
- [x] Validates network enabled
- [x] Validates policy exists and enabled
- [x] Fee calculation from policy
- [x] Total debit = amount + fee
- [x] Outbox entry created

### ✅ Risk & Policy (risk.policy.test.ts)
- [x] Risk scoring calculation
- [x] High amount threshold
- [x] Velocity limits: 24h amount
- [x] Velocity limits: 24h count
- [x] Address whitelist enforcement
- [x] New address detection
- [x] Multiple flags combine correctly
- [x] Risk blocks withdrawal

### ✅ Approval & Submit (approve.submit.test.ts)
- [x] Approval threshold enforcement
- [x] Prevent duplicate approvals
- [x] Multiple approvers allowed
- [x] BitGo submission creates transfer
- [x] Stores provider_ref
- [x] Idempotent submission
- [x] Rejection flow
- [x] Cancel flow

### ✅ Webhook & Status (webhook.status.test.ts)
- [x] BROADCAST triggers ledger operation
- [x] FAILED releases lock
- [x] CONFIRMED updates status
- [x] State transitions correct
- [x] No state regression
- [x] Idempotent webhook handling
- [x] Out-of-order webhooks

### ✅ Idempotency (idempotency.test.ts)
- [x] HTTP idempotency key
- [x] Outbox operations idempotent
- [x] Ledger operations use unique keys
- [x] BitGo uses sequenceId
- [x] Retry uses same key
- [x] End-to-end idempotency

### ✅ Outbox Worker (outbox.worker.test.ts)
- [x] Retry with exponential backoff
- [x] Dead letter after max attempts
- [x] SKIP LOCKED prevents duplicates
- [x] Process all operation types
- [x] Worker lifecycle management
- [x] Idempotent retry

## Mock Infrastructure

### MockDatabase
In-memory state for withdrawals, policies, networks, wallets, approvals, audit logs, outbox.

```typescript
const db = new MockDatabase();
db.setupTestData();
```

### Mock Clients
- **PostgreSQL**: `createMockClient(db)` - 20+ query patterns
- **BitGo**: `createMockBitGoClient()` - Transfer tracking
- **Ledger**: `createMockLedgerService()` - Lock/broadcast/cancel
- **Redis**: `createMockRedis()` - Key-value store
- **Logger**: `createMockLogger()` - Silent logger

### Test Fixtures
```typescript
fixtures.users.alice              // "user-alice-123"
fixtures.approvers.admin1         // "admin-john-001"
fixtures.addresses.btc.valid      // Valid BTC address
fixtures.addresses.eth.valid      // Valid ETH address
fixtures.amounts.btc.medium       // 0.1 BTC (10000000 satoshis)
fixtures.amounts.eth.large        // 6 ETH (triggers high risk)
```

## Common Test Patterns

### Basic Test Structure
```typescript
describe("Feature", () => {
  let db: MockDatabase;
  let mockClient: any;
  let mockLogger: any;

  beforeEach(() => {
    db = new MockDatabase();
    db.setupTestData();
    mockClient = createMockClient(db);
    mockLogger = createMockLogger();
  });

  it("should do something", async () => {
    // Arrange
    const request = { ... };
    
    // Act
    const result = await someFunction(mockClient, request, mockLogger);
    
    // Assert
    expect(result.success).toBe(true);
  });
});
```

### Testing Withdrawals
```typescript
// Create withdrawal
const result = await validateAndCreateWithdrawal(
  mockClient,
  fixtures.users.alice,
  { assetNetworkId: "an-btc-mainnet", ... },
  "idem-key-001",
  mockLogger
);

// Check state
const withdrawal = db.withdrawals.get(result.withdrawalId);
expect(withdrawal.status).toBe("REQUESTED");
```

### Testing Outbox
```typescript
// Check outbox operation created
const lockOps = db.outbox.filter(
  op => op.type === "APPLY_LEDGER_LOCK" && op.withdrawal_id === withdrawalId
);
expect(lockOps).toHaveLength(1);
```

### Testing BitGo
```typescript
// Submit to BitGo
await submitToBitGo(mockClient, withdrawalId, mockBitGo, mockLogger);

// Check BitGo called
expect(mockBitGo.transfers.size).toBe(1);

// Check withdrawal updated
const withdrawal = db.withdrawals.get(withdrawalId);
expect(withdrawal.provider_ref).toBeDefined();
```

## Edge Cases Covered

- ✅ Zero/negative amounts
- ✅ Disabled networks/policies
- ✅ Missing configurations
- ✅ API timeouts
- ✅ Concurrent operations
- ✅ Out-of-order webhooks
- ✅ Duplicate requests
- ✅ Max retries exceeded
- ✅ State regressions

## Verification Commands

```bash
# Count tests
grep -r "it(" src/tests/*.test.ts | wc -l

# Count lines
wc -l src/tests/*.ts

# Check syntax (requires deps)
npx tsc --noEmit

# List all test names
grep "it(\"" src/tests/*.test.ts
```

## Documentation

- **README.md**: Full test suite documentation
- **TEST_SUITE_COMPLETE.md**: Implementation summary
- **QUICK_REFERENCE.md**: This file

## Status

✅ **COMPLETE** - All acceptance criteria covered with 89+ tests
