# Ledger Service Quick Start

## 🚀 Quick Start (5 minutes)

### Prerequisites

```bash
# PostgreSQL running
psql -U postgres -c "CREATE DATABASE cex;"

# NATS running
docker run -d -p 4222:4222 nats:latest
```

### Setup

```bash
# 1. Clone and install
cd cex/services/ledger-service
pnpm install

# 2. Configure
cp .env.example .env
# Edit .env with your DATABASE_URL and NATS_URL

# 3. Run migrations
cd ../../packages/db
pnpm migrate

# 4. Start service
cd ../../services/ledger-service
pnpm dev
```

Service starts on http://localhost:3003

### Verify

```bash
# Health check
curl http://localhost:3003/health

# Should return:
# {
#   "ok": true,
#   "database": { "ok": true },
#   "sequenceGaps": { "ok": true, "gaps": [] },
#   "negativeBalances": { "ok": true, "count": 0 },
#   "reconciliation": { "ok": true, "lastRun": "..." }
# }
```

## �� Key Commands

```bash
# Development
pnpm dev           # Start with hot reload
pnpm build         # Build to dist/
pnpm test          # Run unit tests
pnpm lint          # Lint (placeholder)

# Database
cd ../../packages/db
pnpm migrate       # Run migrations
pnpm seed          # Seed data (if available)

# Testing
pnpm test                    # All tests
pnpm test money.test.ts      # Specific test
pnpm test --coverage         # With coverage
```

## 🔍 API Examples

```bash
# Get user balances (requires admin key)
curl http://localhost:3003/balances/{userId} \
  -H "X-Admin-Key: your-secret-key"

# Trigger reconciliation
curl -X POST http://localhost:3003/reconcile/run \
  -H "X-Admin-Key: your-secret-key"

# Get latest reconciliation report
curl http://localhost:3003/reconcile/latest \
  -H "X-Admin-Key: your-secret-key"

# Get transaction details
curl http://localhost:3003/ledger/tx/{txId} \
  -H "X-Admin-Key: your-secret-key"

# Get account entries
curl "http://localhost:3003/ledger/account/{accountId}/entries?limit=10" \
  -H "X-Admin-Key: your-secret-key"
```

## 🎯 Common Tasks

### Check Balance

```typescript
// Using the repository
import { BalancesRepo } from './db/repositories/index.js';

const repo = new BalancesRepo(client);
const balances = await repo.getUserBalances(userId);

balances.forEach(b => {
  console.log(`${b.assetId}: ${b.availableAtoms} available, ${b.lockedAtoms} locked`);
});
```

### Process a Trade Event

```typescript
// Handled automatically by nats_consumer.ts
// But you can test manually:
import { handleTradeEvent } from './consumers/handlers/trade_settle.js';

const result = await withSerializableTransaction(pool, async (client) => {
  return handleTradeEvent(client, tradeEvent, market);
});

if (result.ok) {
  console.log('Trade settled successfully');
} else {
  console.error('Failed:', result.error);
}
```

### Run Reconciliation

```typescript
import { runReconciliation } from './jobs/index.js';

const report = await runReconciliation(pool, logger);

if (report.ok) {
  console.log('✅ All balances match');
} else {
  console.log(`❌ Found ${report.mismatches.length} mismatches`);
  report.mismatches.forEach(m => {
    console.log(`${m.accountId}/${m.assetId}: expected ${m.expected}, got ${m.actual}`);
  });
}
```

## 🐛 Troubleshooting

### Tests failing

```bash
# Clean and reinstall
rm -rf node_modules
pnpm install
pnpm test
```

### Build errors

```bash
# Check TypeScript version
npx tsc --version

# Clean build
rm -rf dist
pnpm build
```

### Database connection fails

```bash
# Check DATABASE_URL
echo $DATABASE_URL

# Test connection
psql $DATABASE_URL -c "SELECT 1"

# Check migrations
cd ../../packages/db
pnpm migrate
```

### NATS connection fails

```bash
# Check NATS is running
nc -zv localhost 4222

# Or with Docker
docker run -d -p 4222:4222 nats:latest
```

## 📖 Further Reading

- [README.md](./README.md) - Comprehensive documentation
- [IMPLEMENTATION.md](./IMPLEMENTATION.md) - Implementation details
- [src/consumers/handlers/README.md](./src/consumers/handlers/README.md) - Handler guide
- [src/jobs/README.md](./src/jobs/README.md) - Jobs guide

## 🆘 Need Help?

1. Check the logs: Service logs to stdout with structured JSON
2. Check health endpoint: `curl http://localhost:3003/health`
3. Check database: Run migrations, check connectivity
4. Check NATS: Ensure matching engine is publishing events
5. Run reconciliation: Detect balance mismatches

## 🎓 Learning Path

1. **Start here:** Read [README.md](./README.md) Architecture section
2. **Understand domain:** Read `src/domain/types.ts` and `money.ts`
3. **See it work:** Run tests with `pnpm test`
4. **Follow a trade:** Read `src/consumers/handlers/trade_settle.ts`
5. **Check invariants:** Read `src/domain/invariants.ts`
6. **Explore repos:** Read `src/db/repositories/*.ts`
7. **Deep dive:** Read [IMPLEMENTATION.md](./IMPLEMENTATION.md)

## ✅ Checklist for Production

- [ ] Run all migrations
- [ ] Set ADMIN_KEY environment variable
- [ ] Configure DATABASE_URL (with connection pooling)
- [ ] Configure NATS_URL (with auth if needed)
- [ ] Set LOG_LEVEL=info (or warn for production)
- [ ] Test with sample trades
- [ ] Run reconciliation job
- [ ] Set up monitoring/alerts
- [ ] Configure backup strategy
- [ ] Document runbooks

Happy coding! 🎉
