# Ledger Service Jobs

Reconciliation and maintenance jobs for the ledger service.

## Jobs

### 1. Reconciliation (`reconcile.ts`)

Recomputes all user balances from ledger entries and compares with cached balances.

```typescript
import { runReconciliation } from './jobs/index.js';

const report = await runReconciliation(pool, logger);
console.log(`Reconciliation ${report.ok ? 'passed' : 'failed'}`);
console.log(`Found ${report.mismatches.length} mismatches`);
```

**Features:**
- Recomputes balances from ledger_entries for all user accounts
- Compares with balances_cache
- Reports detailed mismatches
- Writes report to reconciliation_reports table
- Optionally fixes mismatches if `AUTO_FIX=true` environment variable is set (default: false)

**Environment Variables:**
- `AUTO_FIX` - Set to `"true"` to automatically fix balance mismatches (default: false)

### 2. Balance Backfill (`backfill_balances.ts`)

Recomputes ALL balances from ledger and populates balances_cache.

```typescript
import { backfillBalances } from './jobs/index.js';

await backfillBalances(pool, logger);
```

**Features:**
- Recomputes all balances from ledger_entries
- Uses `BalancesRepo.recomputeFromLedger()` for each account
- Progress logging every 100 accounts
- Safe to run multiple times (idempotent)
- Useful for initial setup or after cache corruption

### 3. Health Check (`health.ts`)

Monitors system health and data integrity.

```typescript
import { checkHealth } from './jobs/index.js';

const status = await checkHealth(pool, logger);
console.log(`Health: ${status.ok ? 'OK' : 'FAILED'}`);
console.log(`Summary: ${status.summary}`);
```

**Checks:**
1. **Database connection** - Verifies database is accessible
2. **Sequence gaps** - Detects missing sequences per market in ledger_transactions
3. **Negative balances** - Finds accounts with negative balances (should never happen)
4. **Recent reconciliation** - Checks when last reconciliation ran and if it passed

**Return Type:**
```typescript
interface HealthStatus {
  ok: boolean;
  timestamp: Date;
  checks: {
    database: HealthCheck;
    sequenceGaps: HealthCheck;
    negativeBalances: HealthCheck;
    recentReconciliation: HealthCheck;
  };
  summary: string;
}
```

## Usage Examples

### Run reconciliation with auto-fix enabled

```bash
# Set environment variable
export AUTO_FIX=true

# Run reconciliation
node -e "
import { createPgPool, createLogger } from '@cex/common';
import { runReconciliation } from './dist/jobs/index.js';

const pool = createPgPool(process.env.DATABASE_URL);
const logger = createLogger('reconcile');

const report = await runReconciliation(pool, logger);
console.log(JSON.stringify(report, null, 2));
"
```

### Scheduled reconciliation (cron)

```bash
# Run reconciliation daily at 2am
0 2 * * * cd /app && AUTO_FIX=false node -e "..." >> /var/log/reconcile.log 2>&1
```

### Health check endpoint

```typescript
// Add to your Express app
app.get('/health/detailed', async (req, res) => {
  const status = await checkHealth(pool, logger);
  res.status(status.ok ? 200 : 503).json(status);
});
```

## Database Schema Requirements

These jobs expect the following tables:

### reconciliation_reports
```sql
CREATE TABLE reconciliation_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type VARCHAR(50) NOT NULL,
  ok BOOLEAN NOT NULL,
  mismatches JSONB NOT NULL,
  summary JSONB NOT NULL,
  run_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reconciliation_reports_run_at ON reconciliation_reports(run_at DESC);
CREATE INDEX idx_reconciliation_reports_job_type ON reconciliation_reports(job_type);
```

### balances (cache table)
```sql
CREATE TABLE balances (
  user_id UUID NOT NULL,
  asset_id VARCHAR(50) NOT NULL,
  available_atoms NUMERIC(78, 0) NOT NULL DEFAULT 0,
  locked_atoms NUMERIC(78, 0) NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, asset_id)
);
```

## Error Handling

All jobs:
- Accept `Pool` and `Logger` as parameters
- Use database transactions for consistency
- Log errors with context
- Rollback on failure
- Return structured results or throw on critical errors

## Monitoring Recommendations

1. **Run reconciliation daily** - Catch balance drift early
2. **Alert on health failures** - Monitor sequence gaps and negative balances
3. **Track reconciliation reports** - Query reconciliation_reports for trends
4. **Log aggregation** - Collect job logs for debugging

```sql
-- Check recent reconciliation status
SELECT job_type, ok, summary->>'accountsWithMismatches' as mismatches, run_at
FROM reconciliation_reports
ORDER BY run_at DESC
LIMIT 10;
```
