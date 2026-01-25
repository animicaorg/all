# Quick Start Guide - Withdrawals Service

Get the withdrawals service running in 5 minutes.

## Prerequisites

- Node.js 20+
- PostgreSQL 14+ (running)
- Redis 6+ (running)
- BitGo test account (for testing)

## Step 1: Install Dependencies

```bash
cd /home/runner/work/all/all/cex/services/withdrawals-service
npm install
```

## Step 2: Configure Environment

```bash
# Copy example environment
cp .env.example .env

# Edit with your values
nano .env
```

**Minimum required:**
```env
# Database
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=cex_dev

# Redis
REDIS_URL=redis://localhost:6379

# NATS (required by common package)
NATS_URL=nats://localhost:4222

# BitGo
BITGO_ENV=test
BITGO_ACCESS_TOKEN=your_test_token
BITGO_WEBHOOK_SECRET=your_webhook_secret

# Admin
ADMIN_API_KEY=test-admin-key-123
```

## Step 3: Run Database Migration

```bash
cd ../../packages/db
npm run migrate:latest
```

This creates:
- `withdrawal_policies`
- `withdrawals`
- `withdrawal_approvals`
- `withdrawal_ledger_links`
- `withdrawal_outbox`
- `withdrawal_audit_log`
- `withdrawal_idempotency`

## Step 4: Seed Test Data

```sql
-- Connect to database
psql -U postgres -d cex_dev

-- Create test user (if not exists)
INSERT INTO users (id, email, kyc_tier) 
VALUES ('00000000-0000-0000-0000-000000000001', 'test@example.com', 'VERIFIED');

-- Create asset network (example: BTC testnet)
INSERT INTO asset_networks (id, asset_symbol, network_name, address_type, confirmations_required, enabled)
VALUES ('11111111-1111-1111-1111-111111111111', 'BTC', 'TESTNET', 'BECH32', 1, true);

-- Create wallet
INSERT INTO wallets (asset_network_id, wallet_type, provider, provider_wallet_id, enabled)
VALUES ('11111111-1111-1111-1111-111111111111', 'HOT', 'BITGO', 'your-bitgo-wallet-id', true);

-- Create withdrawal policy
INSERT INTO withdrawal_policies (
  asset_network_id,
  min_withdrawal_atoms,
  max_withdrawal_atoms,
  daily_limit_atoms,
  daily_limit_count,
  kyc_tier_required,
  required_approvals,
  high_risk_threshold_atoms,
  high_risk_approvals,
  whitelist_only,
  enabled
) VALUES (
  '11111111-1111-1111-1111-111111111111',
  1000000,           -- 0.01 BTC min
  100000000,         -- 1 BTC max
  500000000,         -- 5 BTC daily limit
  10,                -- 10 withdrawals per day
  '["VERIFIED"]',    -- KYC tier required
  1,                 -- 1 approval required
  50000000,          -- 0.5 BTC high risk threshold
  2,                 -- 2 approvals for high risk
  false,             -- whitelist not required
  true               -- enabled
);
```

## Step 5: Start the Service

```bash
cd ../../services/withdrawals-service

# Development mode (with auto-reload)
npm run dev

# Production mode
npm run build
node dist/index.js
```

Expected output:
```
{"level":30,"time":...,"msg":"Starting withdrawals service"}
{"level":30,"time":...,"msg":"Database and Redis connections established"}
{"level":30,"time":...,"msg":"HTTP server listening","port":3003}
{"level":30,"time":...,"msg":"Background jobs started"}
{"level":30,"time":...,"msg":"Withdrawals service started successfully"}
```

## Step 6: Test the API

### Health Check
```bash
curl http://localhost:3003/healthz
```

### Create Withdrawal
```bash
curl -X POST http://localhost:3003/withdrawals \
  -H "Authorization: Bearer user-00000000-0000-0000-0000-000000000001" \
  -H "Idempotency-Key: test-withdrawal-001" \
  -H "Content-Type: application/json" \
  -d '{
    "assetNetworkId": "11111111-1111-1111-1111-111111111111",
    "destinationAddress": "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",
    "amount": "10000000"
  }'
```

Expected response:
```json
{
  "id": "uuid",
  "status": "REQUESTED",
  "amount": "10000000",
  "feeAmount": "0",
  "totalDebitAmount": "10000000",
  "destinationAddress": "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",
  "destinationTag": null,
  "riskScore": 20.0,
  "riskFlags": ["NEW_ADDRESS"],
  "requestedAt": "2024-01-25T...",
  "createdAt": "2024-01-25T..."
}
```

### List Withdrawals
```bash
curl http://localhost:3003/withdrawals \
  -H "Authorization: Bearer user-00000000-0000-0000-0000-000000000001"
```

### Get Withdrawal Details
```bash
curl http://localhost:3003/withdrawals/{withdrawal-id} \
  -H "Authorization: Bearer user-00000000-0000-0000-0000-000000000001"
```

### Admin: List All Withdrawals
```bash
curl http://localhost:3003/admin/withdrawals \
  -H "X-Admin-Api-Key: test-admin-key-123"
```

### Admin: Approve Withdrawal
```bash
curl -X POST http://localhost:3003/admin/withdrawals/{id}/approve \
  -H "X-Admin-Api-Key: test-admin-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "APPROVE",
    "reason": "Manual review completed"
  }'
```

## Verification Checklist

- [ ] Health check returns `{"status":"ok"}`
- [ ] Can create withdrawal (201 response)
- [ ] Can list withdrawals (200 response)
- [ ] Idempotency works (same key returns cached response)
- [ ] Rate limiting works (6th request in hour returns 429)
- [ ] Admin endpoints require admin key (403 without key)
- [ ] Logs show outbox worker processing
- [ ] Logs show poll pending job running
- [ ] Database has withdrawal record
- [ ] Database has outbox operations

## Troubleshooting

### Service won't start

**Error: "Cannot find module '@cex/common'"**
```bash
# Install workspace dependencies
cd ../../
npm install
```

**Error: "Connection refused" for database**
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql
# Or
pg_isready
```

**Error: "Connection refused" for Redis**
```bash
# Check Redis is running
redis-cli ping
# Should return PONG
```

### Withdrawal stuck in REQUESTED

Check logs for errors:
```bash
# Search for errors
grep ERROR logs/*.log

# Check outbox operations
psql -U postgres -d cex_dev -c \
  "SELECT * FROM withdrawal_outbox WHERE status = 'FAILED';"
```

### BitGo submission failing

Verify BitGo credentials:
```bash
# Test BitGo API access
curl https://app.bitgo-test.com/api/v2/user/session \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Next Steps

1. **Configure BitGo Webhook**: Add `https://your-domain.com/webhooks/bitgo` in BitGo dashboard
2. **Setup Ledger Service**: Ensure ledger service is running on http://localhost:3002
3. **Implement JWT Auth**: Replace placeholder auth in `src/http/middleware/auth.ts`
4. **Add Monitoring**: Setup logging aggregation and alerting
5. **Load Testing**: Test with concurrent requests to verify rate limiting

## Development Tips

### Watch Logs
```bash
tail -f logs/withdrawals-service.log
```

### Query Database
```bash
# Connect to database
psql -U postgres -d cex_dev

# Useful queries
SELECT id, status, amount, destination_address, created_at 
FROM withdrawals 
ORDER BY created_at DESC 
LIMIT 10;

SELECT type, status, attempt_count, last_error 
FROM withdrawal_outbox 
WHERE status != 'COMPLETED' 
ORDER BY created_at DESC;

SELECT event_type, actor_type, created_at 
FROM withdrawal_audit_log 
WHERE withdrawal_id = 'YOUR_ID' 
ORDER BY created_at ASC;
```

### Test Different Scenarios

**High-risk withdrawal (triggers review):**
```bash
# Amount > 0.5 BTC
curl -X POST http://localhost:3003/withdrawals \
  -H "Authorization: Bearer user-..." \
  -H "Idempotency-Key: high-risk-001" \
  -H "Content-Type: application/json" \
  -d '{"assetNetworkId":"...","destinationAddress":"...","amount":"60000000"}'
```

**Rate limit test:**
```bash
# Send 6 requests rapidly
for i in {1..6}; do
  curl -X POST http://localhost:3003/withdrawals \
    -H "Authorization: Bearer user-..." \
    -H "Idempotency-Key: rate-test-$i" \
    -H "Content-Type: application/json" \
    -d '{"assetNetworkId":"...","destinationAddress":"...","amount":"10000000"}'
  echo
done
```

## Documentation

- **Full API docs**: See [README.md](./README.md)
- **Architecture**: See [ARCHITECTURE.md](./ARCHITECTURE.md)
- **Implementation details**: See [IMPLEMENTATION_SUMMARY.md](./IMPLEMENTATION_SUMMARY.md)

## Support

For issues or questions:
1. Check logs for error details
2. Review [README.md](./README.md) troubleshooting section
3. Verify database migrations ran successfully
4. Ensure all environment variables are set correctly
