# Withdrawals Service

Comprehensive cryptocurrency withdrawal processing service for the CEX platform. Handles the complete withdrawal lifecycle from request validation through BitGo submission to on-chain confirmation.

## Architecture Overview

```
┌─────────────┐
│   User API  │
│  Requests   │
└──────┬──────┘
       │
       v
┌─────────────────────────────────────────────┐
│         Withdrawal Pipeline                  │
│                                             │
│  1. Request → 2. Risk → 3. Approve →       │
│  4. Submit → 5. Track → 6. Finalize        │
└──────┬──────────────────────────┬───────────┘
       │                          │
       v                          v
┌─────────────┐          ┌────────────────┐
│   Outbox    │          │   Background   │
│   Worker    │          │     Jobs       │
│             │          │                │
│ • Ledger    │          │ • Poll Pending │
│ • BitGo     │          │ • Reconcile    │
└──────┬──────┘          └────────────────┘
       │
       v
┌─────────────┐          ┌────────────────┐
│   Ledger    │          │     BitGo      │
│   Service   │◄─────────┤   Custodian    │
└─────────────┘          └────────────────┘
```

## Features

### Core Capabilities

- **Multi-asset Support**: Handles various cryptocurrencies and networks
- **Risk-based Approval**: Automatic risk scoring with configurable approval workflows
- **Idempotency**: Prevents duplicate withdrawal submissions
- **Rate Limiting**: Protects against abuse with Redis-backed rate limiting
- **Ledger Integration**: Coordinates with ledger service for balance management
- **BitGo Integration**: Secure custody and blockchain broadcasting via BitGo
- **Webhook Processing**: Real-time status updates from BitGo
- **Audit Trail**: Complete audit logging of all operations
- **Background Jobs**: Polling and reconciliation for reliability

### Withdrawal Lifecycle

```
REQUESTED ──> RISK_REVIEW ──> APPROVED ──> SIGNING ──> BROADCAST ──> CONFIRMED
    │              │              │            │           │             
    └──> REJECTED  └──> REJECTED  └──────> FAILED ───> FAILED
    └──> CANCELED
```

### Policy-Based Controls

- Minimum/maximum withdrawal amounts
- Daily withdrawal limits (amount and count)
- KYC tier requirements
- Whitelist enforcement
- Multi-approver workflows
- High-risk thresholds

## Setup

### Prerequisites

- Node.js 20+
- PostgreSQL 14+
- Redis 6+
- BitGo account with API access

### Installation

```bash
# Install dependencies
pnpm install

# Copy environment file
cp .env.example .env

# Edit .env with your configuration
nano .env

# Run database migrations (from cex/packages/db)
cd ../packages/db
pnpm run migrate:latest

# Return to service directory
cd ../../services/withdrawals-service
```

### Database Migration

The service requires migration `005_withdrawals_infrastructure.js` which creates:

- `withdrawal_policies` - Configurable policies per asset/network
- `withdrawals` - Main withdrawal records
- `withdrawal_approvals` - Approval workflow tracking
- `withdrawal_ledger_links` - Links to ledger transactions
- `withdrawal_outbox` - Outbox pattern for async operations
- `withdrawal_audit_log` - Complete audit trail
- `withdrawal_idempotency` - HTTP idempotency records

### Running the Service

```bash
# Development mode (with auto-reload)
pnpm run dev

# Production mode
pnpm run build
node dist/index.js
```

## API Documentation

### User Endpoints

All user endpoints require authentication via `Authorization: Bearer {token}` header.

#### POST /withdrawals

Create a new withdrawal request.

**Headers:**
- `Authorization: Bearer {token}` - User authentication
- `Idempotency-Key: {unique-key}` - Required for idempotency

**Request Body:**
```json
{
  "assetNetworkId": "uuid",
  "destinationAddress": "string",
  "destinationTag": "string (optional)",
  "amount": "string (atoms)",
  "clientWithdrawalId": "string (optional)"
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "status": "REQUESTED|RISK_REVIEW|APPROVED|REJECTED",
  "amount": "string",
  "feeAmount": "string",
  "totalDebitAmount": "string",
  "destinationAddress": "string",
  "destinationTag": "string|null",
  "riskScore": "number|null",
  "riskFlags": ["string"],
  "requestedAt": "ISO8601",
  "createdAt": "ISO8601"
}
```

**Rate Limit:** 5 requests per hour per user (configurable)

#### GET /withdrawals

List user's withdrawals with pagination.

**Query Parameters:**
- `limit` - Max results (default: 50, max: 100)
- `offset` - Pagination offset (default: 0)
- `status` - Filter by status (optional)

**Response (200):**
```json
{
  "withdrawals": [
    {
      "id": "uuid",
      "status": "string",
      "assetNetworkId": "uuid",
      "amount": "string",
      "feeAmount": "string",
      "totalDebitAmount": "string",
      "destinationAddress": "string",
      "destinationTag": "string|null",
      "txid": "string|null",
      "riskScore": "number|null",
      "riskFlags": ["string"],
      "requestedAt": "ISO8601",
      "approvedAt": "ISO8601|null",
      "broadcastAt": "ISO8601|null",
      "confirmedAt": "ISO8601|null",
      "failureCode": "string|null",
      "failureMessage": "string|null",
      "createdAt": "ISO8601"
    }
  ],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "hasMore": false
  }
}
```

#### GET /withdrawals/:id

Get details of a specific withdrawal.

**Response (200):**
```json
{
  "id": "uuid",
  "status": "string",
  "assetNetworkId": "uuid",
  "amount": "string",
  "feeAmount": "string",
  "totalDebitAmount": "string",
  "destinationAddress": "string",
  "destinationTag": "string|null",
  "providerRef": "string|null",
  "txid": "string|null",
  "riskScore": "number|null",
  "riskFlags": ["string"],
  "riskReason": "string|null",
  "requestedAt": "ISO8601",
  "approvedAt": "ISO8601|null",
  "broadcastAt": "ISO8601|null",
  "confirmedAt": "ISO8601|null",
  "failureCode": "string|null",
  "failureMessage": "string|null",
  "attemptCount": 0,
  "createdAt": "ISO8601",
  "updatedAt": "ISO8601"
}
```

### Admin Endpoints

All admin endpoints require `X-Admin-Api-Key` header.

#### GET /admin/withdrawals

List all withdrawals with filters.

**Query Parameters:**
- `limit`, `offset` - Pagination
- `status` - Filter by status
- `userId` - Filter by user
- `assetNetworkId` - Filter by asset/network

**Response:** Similar to user withdrawals list, but includes `userId` field.

#### GET /admin/withdrawals/:id

Get complete withdrawal details including approvals and audit log.

**Response (200):**
```json
{
  "withdrawal": { /* ... withdrawal object ... */ },
  "approvals": [
    {
      "id": "uuid",
      "approverId": "uuid",
      "approverRole": "string",
      "action": "APPROVE|REJECT",
      "reason": "string|null",
      "createdAt": "ISO8601"
    }
  ],
  "auditLog": [
    {
      "id": "uuid",
      "eventType": "string",
      "actorId": "uuid|null",
      "actorType": "SYSTEM|ADMIN|USER",
      "changes": {},
      "metadata": {},
      "createdAt": "ISO8601"
    }
  ]
}
```

#### POST /admin/withdrawals/:id/approve

Approve or reject a withdrawal.

**Request Body:**
```json
{
  "action": "APPROVE|REJECT",
  "reason": "string (optional)"
}
```

**Response (200):**
```json
{
  "success": true,
  "message": "Withdrawal approved",
  "newStatus": "APPROVED|REJECTED"
}
```

#### POST /admin/withdrawals/:id/reject

Shorthand for rejection (sets `action: "REJECT"`).

#### POST /admin/withdrawals/:id/cancel

Cancel a withdrawal (admin override).

**Request Body:**
```json
{
  "reason": "string (optional)"
}
```

#### POST /admin/withdrawals/:id/retry

Force retry a failed or stuck withdrawal.

**Response (200):**
```json
{
  "success": true,
  "message": "Withdrawal queued for retry"
}
```

### Webhook Endpoints

#### POST /webhooks/bitgo

Receive BitGo webhook notifications.

**Headers:**
- `X-BitGo-Signature` - Webhook signature (if secret configured)

**Request Body:** BitGo webhook payload

**Response (200):**
```json
{
  "success": true,
  "message": "Webhook processed"
}
```

## Withdrawal States

### State Transitions

| Current State | Next States | Trigger |
|--------------|-------------|---------|
| REQUESTED | RISK_REVIEW, APPROVED, REJECTED | Risk evaluation |
| RISK_REVIEW | APPROVED, REJECTED | Admin approval |
| APPROVED | SIGNING, FAILED | BitGo submission |
| SIGNING | BROADCAST, FAILED | BitGo signing |
| BROADCAST | CONFIRMED, FAILED | On-chain confirmation |
| CONFIRMED | - | Terminal state |
| REJECTED | - | Terminal state |
| CANCELED | - | Terminal state |
| FAILED | APPROVED (via retry) | Admin retry |

### Risk Flags

- `HIGH_AMOUNT` - Amount exceeds high-risk threshold
- `VELOCITY_EXCEEDED` - Daily limits exceeded
- `NEW_ADDRESS` - First withdrawal to this address
- `ADDRESS_NOT_WHITELISTED` - Address not in whitelist (if required)

## Ledger Integration

The service coordinates with the ledger service through the outbox pattern:

### Ledger Operations

1. **LOCK** (on request approval):
   - Moves funds from available to locked balance
   - Prevents double-spending
   - Links to withdrawal via `withdrawal_ledger_links.lock_tx_id`

2. **BROADCAST** (on BitGo broadcast):
   - Moves funds from locked to system balance
   - Records outgoing transaction
   - Links via `withdrawal_ledger_links.broadcast_tx_id`

3. **CANCEL** (on rejection/failure):
   - Returns funds from locked to available balance
   - Links via `withdrawal_ledger_links.cancel_tx_id`

### Integration Points

The outbox worker calls ledger service endpoints:

- `POST /internal/lock` - Lock funds for withdrawal
- `POST /internal/broadcast` - Record broadcast transaction
- `POST /internal/cancel` - Release locked funds

## Background Jobs

### Outbox Worker

**Interval:** 5 seconds (configurable via `OUTBOX_WORKER_INTERVAL_MS`)

**Purpose:** Processes pending outbox operations with at-least-once delivery semantics.

**Operations:**
- `APPLY_LEDGER_LOCK` - Calls ledger service to lock funds
- `SUBMIT_TO_BITGO` - Submits withdrawal to BitGo API
- `APPLY_LEDGER_BROADCAST` - Records broadcast in ledger
- `APPLY_LEDGER_CANCEL` - Releases locked funds

**Retry Strategy:** Exponential backoff with jitter, max 10 attempts

### Poll Pending Job

**Interval:** 1 minute (configurable via `POLL_PENDING_INTERVAL_MS`)

**Purpose:** Queries BitGo for status of withdrawals in `SIGNING` or `BROADCAST` states.

**Why Needed:** Ensures status is updated even if webhooks are missed.

### Reconciliation Job

**Interval:** Every 6 hours

**Purpose:** Identifies inconsistencies and stuck withdrawals.

**Checks:**
- Withdrawals stuck in non-terminal states for > 24 hours
- Withdrawals with missing ledger links
- Confirmed withdrawals without on-chain txid

**Output:** Logs warnings for manual review (TODO: store reports, send alerts)

## Testing

### Unit Tests

```bash
pnpm test
```

### Manual Testing

1. **Create a withdrawal policy:**

```sql
INSERT INTO withdrawal_policies (
  asset_network_id, min_withdrawal_atoms, max_withdrawal_atoms,
  daily_limit_atoms, daily_limit_count, kyc_tier_required,
  required_approvals, high_risk_threshold_atoms, high_risk_approvals,
  whitelist_only, enabled
) VALUES (
  '...asset-network-uuid...', 1000000, 100000000,
  500000000, 10, '["VERIFIED"]',
  1, 50000000, 2,
  false, true
);
```

2. **Submit a withdrawal request:**

```bash
curl -X POST http://localhost:3003/withdrawals \
  -H "Authorization: Bearer user-{user-uuid}" \
  -H "Idempotency-Key: unique-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "assetNetworkId": "...uuid...",
    "destinationAddress": "bc1q...",
    "amount": "10000000"
  }'
```

3. **Check withdrawal status:**

```bash
curl http://localhost:3003/withdrawals/{withdrawal-id} \
  -H "Authorization: Bearer user-{user-uuid}"
```

4. **Admin approval (if needed):**

```bash
curl -X POST http://localhost:3003/admin/withdrawals/{id}/approve \
  -H "X-Admin-Api-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "APPROVE",
    "reason": "Reviewed and approved"
  }'
```

## Troubleshooting

### Withdrawal stuck in SIGNING

**Symptom:** Withdrawal remains in SIGNING state for extended period.

**Diagnosis:**
1. Check BitGo dashboard for transfer status
2. Review outbox operations: `SELECT * FROM withdrawal_outbox WHERE withdrawal_id = '...'`
3. Check logs for BitGo API errors

**Resolution:**
- Use admin retry endpoint: `POST /admin/withdrawals/:id/retry`
- If BitGo transfer failed, it will be resubmitted
- If BitGo transfer succeeded but webhook was missed, poll pending job will update status

### Ledger operations failing

**Symptom:** Outbox operations stuck with ledger errors.

**Diagnosis:**
1. Check ledger service health: `curl http://localhost:3002/healthz`
2. Review outbox errors: `SELECT * FROM withdrawal_outbox WHERE status = 'PENDING' AND attempt_count > 5`

**Resolution:**
- Ensure ledger service is running and accessible
- Check network connectivity between services
- Verify ledger service has necessary balance/permissions

### Idempotency not working

**Symptom:** Duplicate withdrawals created despite idempotency key.

**Diagnosis:**
1. Check if idempotency records exist: `SELECT * FROM withdrawal_idempotency WHERE idempotency_key = '...'`
2. Verify user ID matches
3. Check if records expired

**Resolution:**
- Idempotency records expire after 24 hours by default
- Ensure client sends same idempotency key for retries
- Ensure user authentication is stable

### Rate limiting too strict

**Symptom:** Users hitting rate limits frequently.

**Configuration:**
- Adjust `WITHDRAWAL_REQUEST_RATE_LIMIT` in `.env`
- Default is 5 requests per hour per user
- Consider per-tier limits in future enhancement

## Security Considerations

1. **Authentication:** Currently uses placeholder bearer token auth. Implement proper JWT verification in production.

2. **Admin API Key:** Rotate `ADMIN_API_KEY` regularly and store securely.

3. **BitGo Credentials:** Never commit `BITGO_ACCESS_TOKEN` to version control.

4. **Webhook Signatures:** Always configure `BITGO_WEBHOOK_SECRET` in production to verify webhook authenticity.

5. **Rate Limiting:** Use Redis-backed rate limiting in production (in-memory fallback is for dev only).

6. **Address Validation:** Implement chain-specific address validation before submission.

7. **Whitelist Enforcement:** Implement address whitelist table and verification if policy requires.

## Future Enhancements

- [ ] JWT-based user authentication
- [ ] Per-tier withdrawal limits
- [ ] Address whitelist management UI
- [ ] Automatic reconciliation alerts
- [ ] Withdrawal scheduling
- [ ] Multi-signature requirements
- [ ] Hot/cold wallet routing
- [ ] Fee estimation API integration
- [ ] Withdrawal templates
- [ ] Bulk withdrawal processing

## License

See parent repository for license information.
