# Withdrawals Service Test Suite

Comprehensive test suite covering all acceptance criteria for the withdrawals service.

## Overview

This test suite validates the complete withdrawal lifecycle from request creation through BitGo submission to final confirmation, including:

- ✅ Request validation and ledger locking
- ✅ Risk evaluation and policy enforcement  
- ✅ Approval workflows
- ✅ BitGo submission and tracking
- ✅ Webhook processing
- ✅ Idempotency at all levels
- ✅ Outbox pattern with retries

## Test Files

### 1. `helpers.ts` - Test Utilities

Provides mock implementations and test fixtures:

- **MockDatabase**: In-memory database state tracker
- **createMockClient**: PostgreSQL client mock with query handling
- **createMockBitGoClient**: BitGo API client mock
- **createMockLedgerService**: Ledger service mock
- **createMockRedis**: Redis client mock
- **createMockLogger**: Pino logger mock
- **fixtures**: Test data (users, addresses, amounts)

### 2. `request.lock.test.ts` - Request Creation & Lock

Tests withdrawal request creation and ledger locking:

- ✅ Request creates withdrawal + locks funds once (ledger-service mocked)
- ✅ Idempotency: same Idempotency-Key returns same withdrawal, does not relock
- ✅ Validation: amount > 0, valid address format, network enabled
- ✅ Validation: min/max withdrawal limits enforced
- ✅ Fee calculation from policy metadata
- ✅ Total debit = amount + fee
- ✅ Outbox entry created for ledger lock
- ✅ Audit log entries
- ✅ Handles destination tags and client IDs

**Key Tests:**
- `should create withdrawal and lock funds once`
- `should validate amount > 0`
- `should validate amount against min/max withdrawal`
- `should reject if network not found or disabled`
- `should calculate total debit as amount + fee`

### 3. `risk.policy.test.ts` - Risk & Policy

Tests risk evaluation and policy enforcement:

- ✅ Risk scoring calculation
- ✅ High amount threshold triggers extra approvals
- ✅ Velocity limits: 24h amount and count checks
- ✅ Address whitelist enforcement (if enabled)
- ✅ New address detection
- ✅ Multiple risk flags combine correctly
- ✅ KYC tier requirements (documented)
- ✅ Risk blocks withdrawal and releases lock

**Key Tests:**
- `should assign low risk score for normal withdrawal`
- `should flag high amounts`
- `should enforce 24h amount limit`
- `should enforce 24h count limit`
- `should REVIEW when score >= 40`
- `should require more approvals for high risk amounts`

### 4. `approve.submit.test.ts` - Approval & Submission

Tests approval workflow and BitGo submission:

- ✅ Approval threshold: 1 approval required → submission enqueued
- ✅ Prevent same approver from approving twice
- ✅ Different approvers can approve
- ✅ Submit to BitGo creates transfer, stores provider_ref
- ✅ Idempotency: same withdrawal doesn't create duplicate BitGo transfers
- ✅ Rejection flow queues ledger cancel
- ✅ Cancel flow
- ✅ BitGo API error handling

**Key Tests:**
- `should require 2 approvals before submission` (configurable threshold)
- `should prevent same approver from approving twice`
- `should submit to BitGo and store provider reference`
- `should be idempotent - no duplicate transfers`
- `should handle rejection properly`

### 5. `webhook.status.test.ts` - Webhook Processing

Tests webhook processing and status tracking:

- ✅ Webhook BROADCAST triggers ledger broadcast move exactly once
- ✅ Webhook FAILED pre-broadcast triggers release lock
- ✅ Webhook CONFIRMED updates status
- ✅ State transitions are correct (APPROVED → SIGNING → BROADCAST → CONFIRMED)
- ✅ No state regression (e.g., CONFIRMED → BROADCAST)
- ✅ Idempotent webhook handling
- ✅ Out-of-order webhook handling
- ✅ Signature verification (documented)

**Key Tests:**
- `should process BROADCAST webhook and trigger ledger operation exactly once`
- `should process FAILED webhook pre-broadcast and release lock`
- `should transition BROADCAST -> CONFIRMED`
- `should not regress state from CONFIRMED`
- `should handle duplicate webhook deliveries idempotently`

### 6. `idempotency.test.ts` - Comprehensive Idempotency

Tests idempotency at all levels:

- ✅ HTTP idempotency key prevents duplicate withdrawals
- ✅ Idempotency key scoped to user
- ✅ Outbox operations are idempotent
- ✅ Ledger operations use unique reference IDs
- ✅ BitGo submission uses sequenceId
- ✅ Retry uses same idempotency key
- ✅ End-to-end idempotency

**Key Tests:**
- `should prevent duplicate withdrawals with same idempotency key`
- `should scope idempotency key to user`
- `should not create duplicate outbox entries`
- `should use sequenceId for BitGo idempotency`
- `should maintain idempotency across entire withdrawal lifecycle`

### 7. `outbox.worker.test.ts` - Outbox Pattern

Tests outbox pattern implementation:

- ✅ Retry: BitGo API fails → outbox retries → eventually succeeds
- ✅ Exponential backoff calculation
- ✅ Dead letter after max attempts (10 attempts)
- ✅ SKIP LOCKED prevents duplicate processing
- ✅ Process APPLY_LEDGER_LOCK operations
- ✅ Process APPLY_LEDGER_BROADCAST operations
- ✅ Process APPLY_LEDGER_CANCEL operations
- ✅ Process SUBMIT_TO_BITGO operations
- ✅ Worker lifecycle (start/stop)

**Key Tests:**
- `should retry failed operations with exponential backoff`
- `should use exponential backoff for retries`
- `should mark operation as permanently failed after max attempts`
- `should eventually succeed after transient failures`
- `should process APPLY_LEDGER_LOCK operation`
- `should prevent duplicate processing with FOR UPDATE SKIP LOCKED`

## Running Tests

### Prerequisites

```bash
# Install dependencies (from monorepo root)
pnpm install

# Or in this directory
npm install
```

### Run All Tests

```bash
npm test
```

### Run Specific Test File

```bash
npx vitest run src/tests/request.lock.test.ts
```

### Run in Watch Mode

```bash
npx vitest watch
```

### Run with Coverage

```bash
npx vitest run --coverage
```

## Test Coverage

The test suite covers:

- **Request Pipeline**: 15+ tests
- **Risk Evaluation**: 15+ tests
- **Approval Workflow**: 10+ tests
- **Webhook Processing**: 15+ tests
- **Idempotency**: 15+ tests
- **Outbox Worker**: 15+ tests

**Total**: 85+ test cases

## Test Patterns

### Mock Database State

All tests use an in-memory `MockDatabase` that simulates PostgreSQL:

```typescript
const db = new MockDatabase();
db.setupTestData(); // Creates test networks, policies, wallets
const mockClient = createMockClient(db);
```

### Test Fixtures

Reusable test data in `helpers.ts`:

```typescript
fixtures.users.alice // "user-alice-123"
fixtures.addresses.btc.valid // Valid BTC address
fixtures.amounts.btc.medium // 0.1 BTC
```

### Assertion Examples

```typescript
// Check withdrawal created
expect(result.withdrawalId).toBeDefined();

// Check outbox entry
const lockOps = db.outbox.filter(op => op.type === "APPLY_LEDGER_LOCK");
expect(lockOps).toHaveLength(1);

// Check state transition
expect(withdrawal.status).toBe("BROADCAST");
```

## Edge Cases Tested

1. **Validation**:
   - Amount = 0
   - Amount below minimum
   - Amount above maximum
   - Disabled networks
   - Missing policies

2. **Concurrency**:
   - Duplicate idempotency keys
   - Out-of-order webhooks
   - Concurrent worker processing (SKIP LOCKED)

3. **Failures**:
   - BitGo API timeouts
   - Ledger service unavailable
   - Max retries exceeded
   - Missing wallet configuration

4. **Idempotency**:
   - Duplicate HTTP requests
   - Duplicate webhook deliveries
   - Retry after failure
   - Multiple workers processing same operations

## Mock Implementations

### PostgreSQL Client

Handles common query patterns:
- INSERT/SELECT withdrawals
- INSERT/SELECT approvals
- Velocity limit checks (SUM, COUNT)
- Outbox operations
- FOR UPDATE SKIP LOCKED

### BitGo Client

Simulates BitGo API:
- `createTransfer()` - Creates transfer with sequenceId
- `getTransfer()` - Retrieves transfer by ID
- Tracks all transfers in-memory

### Ledger Service

Simulates ledger API:
- `/internal/lock` - Lock funds
- `/internal/broadcast` - Move funds to system
- `/internal/cancel` - Release locked funds

## Notes

1. **Whitelist Enforcement**: Current implementation always returns `true` for whitelist checks. Tests document expected behavior when fully implemented.

2. **Required Approvals**: Mock uses threshold of 1 by default. Tests demonstrate configurable approval counts.

3. **Transaction Isolation**: Tests use mock transactions (BEGIN/COMMIT/ROLLBACK) but don't enforce full ACID properties.

4. **Signature Verification**: Webhook signature verification is documented but not fully implemented in tests.

5. **Real BitGo**: The mock BitGo client doesn't enforce all real BitGo behaviors (e.g., sequenceId uniqueness). Tests document expected production behavior.

## Future Enhancements

- [ ] Add performance tests (e.g., 1000 withdrawals/second)
- [ ] Add integration tests with real database (testcontainers)
- [ ] Add load tests for outbox worker
- [ ] Add chaos engineering tests (network partitions, etc.)
- [ ] Add property-based tests (fast-check)
- [ ] Add visual regression tests for any UI components

## Troubleshooting

### Tests Not Running

```bash
# Ensure dependencies installed
pnpm install

# Check vitest is available
npx vitest --version
```

### Type Errors

```bash
# Rebuild TypeScript
npm run build
```

### Import Errors

Ensure `tsconfig.json` has correct module resolution:

```json
{
  "compilerOptions": {
    "module": "ESNext",
    "moduleResolution": "node"
  }
}
```

## Contributing

When adding new tests:

1. Follow existing patterns in `helpers.ts`
2. Use descriptive test names
3. Test both happy path and error cases
4. Add edge case coverage
5. Update this README with new test categories
