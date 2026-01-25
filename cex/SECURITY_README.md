# Security Hardening & Compliance Baseline

This document describes the security implementation for the Animica CEX platform, implementing **Codex Prompt #10**.

## Overview

This implementation provides a production-ready security and compliance baseline including:
- ✅ Secrets management with rotation support
- ✅ Enhanced authentication (2FA, backup codes, anti-phishing)
- ✅ Service-to-service authentication (JWT)
- ✅ Rate limiting and abuse prevention
- ✅ Structured logging with audit trails
- ✅ Key custody (BitGo integration, HSM interface)
- ✅ Deployment hardening (Docker, Nginx)
- ✅ Compliance foundations (KYC gating, travel rule)

## Quick Start

### 1. Install Dependencies

```bash
cd cex
pnpm install
```

### 2. Configure Environment

```bash
# Copy template
cp ops/env/.env.example .env.local

# Generate secure keys
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"

# Edit .env.local with secure values
```

### 3. Run Database Migrations

```bash
pnpm --filter @cex/db migrate
```

### 4. Start Services (Secure Mode)

```bash
cd ops
docker-compose -f docker-compose.secure.yml up
```

## Package Structure

### Core Packages

#### `packages/security`
Core security utilities used across all services:

- **`secrets/`** - Secrets management abstraction
  - `providers/env.ts` - Environment variable provider
  - `providers/aws_sm.ts` - AWS Secrets Manager (stub)
  - `providers/gcp_sm.ts` - GCP Secrets Manager (stub)
  - `redaction.ts` - Automatic secret redaction for logs
  - `types.ts` - Keyring support for rotation

- **`auth/`** - Authentication utilities
  - `password.ts` - Argon2id password hashing
  - `totp.ts` - TOTP/2FA implementation
  - `backup_codes.ts` - Backup code generation/verification
  - `session.ts` - Session security (cookies, CSRF)
  - `anti_phishing.ts` - Anti-phishing phrase generation

- **`signing/`** - Signing abstraction
  - `local.ts` - Local HMAC signer (with keyring)
  - `hsm_stub.ts` - HSM interface (vendor-agnostic)
  - `types.ts` - Signer interface

```typescript
// Example: Using secrets management
import { requireSecret, configureSecrets } from '@cex/security/secrets';

// Configure provider (optional, defaults to env)
configureSecrets(new AwsSecretsManagerProvider({
  region: 'us-east-1',
  prefix: 'prod/exchange/'
}));

// Access secrets
const dbPassword = await requireSecret('DB_PASSWORD');
```

#### `packages/observability`
Structured logging and distributed tracing:

- `logger.ts` - Pino-based structured logger with redaction
- `tracer.ts` - OpenTelemetry setup for distributed tracing

```typescript
// Example: Structured logging
import { createLogger } from '@cex/observability';

const logger = createLogger({
  service: 'exchange-api',
  environment: 'production',
  redact: true
});

logger.info({ userId, amount }, 'Withdrawal initiated');
```

#### `packages/middleware`
Express middleware for security:

- `service_auth.ts` - Service-to-service JWT authentication
- `rate_limit.ts` - Rate limiting with Redis backend
- `security_headers.ts` - Security headers (HSTS, CSP, etc.)
- `csrf.ts` - CSRF protection

```typescript
// Example: Service authentication
import { createServiceAuthMiddleware, generateServiceToken } from '@cex/middleware';

// Protect internal endpoint
app.use('/internal', createServiceAuthMiddleware({
  serviceId: 'ledger-service',
  signingKey: process.env.SERVICE_AUTH_KEYRING,
  logger
}));

// Call another service
const token = generateServiceToken(config, 'ledger-service', ['credit']);
const response = await fetch('http://ledger-service/internal/credit', {
  headers: { Authorization: `Bearer ${token}` }
});
```

### Database Migrations

**Migration 007**: Security Hardening
- Backup codes table
- Anti-phishing phrases
- Device tracking
- Withdrawal address book (with cooldown)
- Login attempt tracking
- Account lockouts
- API key rotation history
- Audit log hash chain
- Service tokens
- KYC status fields
- Travel rule fields

```bash
# Run migrations
cd packages/db
pnpm migrate

# Rollback if needed
pnpm migrate:rollback
```

## Security Features

### 1. Secrets Management

**Architecture**: Abstraction layer supporting multiple backends
- **Dev**: Environment variables
- **Prod**: AWS Secrets Manager / GCP Secret Manager

**Key Rotation**: Keyring support for zero-downtime rotation
```bash
# Example keyring (comma-separated)
API_SIGNING_KEYS=key1:abc123...,key2:def456...
# Last key is current; all keys accepted for verification
```

**Redaction**: Automatic PII/secret redaction in logs
- Passwords, tokens, API keys
- Credit cards, private keys
- Database URLs, AWS keys

### 2. Authentication & 2FA

**Password Security**:
- Argon2id hashing with strong parameters
- Memory: 64 MiB, Iterations: 3, Parallelism: 4

**TOTP/2FA**:
- RFC 6238 compliant
- 30-second time steps
- ±1 window for clock drift
- QR code generation

**Backup Codes**:
- 10 codes per user (8 hex chars each)
- One-time use
- Argon2id hashed

**Anti-Phishing**:
- User-set phrase shown on login
- Displayed in emails to verify authenticity

**Session Security**:
- HttpOnly, Secure, SameSite cookies
- CSRF protection for state-changing requests
- Refresh token rotation with reuse detection

### 3. Service-to-Service Auth

**JWT-based** with scopes:
```typescript
// Token structure
{
  iss: 'matching-engine',      // Issuer service
  sub: 'matching-engine',       // Subject (service ID)
  aud: 'ledger-service',        // Target service
  scopes: ['credit', 'debit'],  // Permissions
  exp: <timestamp>              // 5 min default
}
```

**Keyring support**: Multiple active keys for rotation

**Least privilege**: Each service has minimal required scopes

### 4. Rate Limiting & Abuse Prevention

**Edge Layer (Nginx)**:
- Login: 5 req/min per IP
- API: 100 req/min per IP
- WebSocket: 10 conn/min per IP
- Admin: 50 req/min per IP

**App Layer**:
- Per-route custom limits
- Per-user limits (API key)
- Redis-backed distributed rate limiting

**Ban List**:
- IP-based bans with TTL
- User-based bans
- Persistent in Redis

**Auth Lockout**:
- 5 failed attempts → 15 min lockout
- Tracked per IP and per user
- Email notifications

### 5. Logging & Audit

**Structured JSON Logs**:
```json
{
  "ts": "2024-01-25T10:30:00.000Z",
  "level": "info",
  "service": "exchange-api",
  "env": "production",
  "request_id": "req_abc123",
  "trace_id": "def456",
  "user_id": "user_xyz",
  "ip": "1.2.3.4",
  "method": "POST",
  "route": "/api/v1/orders",
  "status": 201,
  "latency_ms": 45
}
```

**Audit Trail Immutability**:
- Hash chain (SHA-256) linking entries
- Monotonic sequence numbers
- Append-only (no updates/deletes)
- Daily signed evidence bundles

**Correlation**:
- `request_id` propagated across services
- `trace_id` / `span_id` for distributed tracing
- OpenTelemetry integration

### 6. Key Custody

**BitGo**:
- Multi-signature wallets
- Multi-approval for high-value withdrawals
- Webhook signature verification (HMAC)
- Policy enforcement (thresholds, whitelists)

**Animica Native Assets**:
- Encrypted keystore (AES-GCM)
- Passphrase from secrets manager
- Unlock only for signing, then lock
- Supports external signer interface

**HSM Interface**:
- Vendor-agnostic abstraction
- Pluggable providers (AWS CloudHSM, Azure Key Vault, etc.)
- Stub included for integration

### 7. Compliance

**KYC Gating**:
- Withdrawal limits based on KYC status
- `none`: $1,000/day
- `approved`: $50,000/day, $200,000/month
- Asset restrictions for non-KYC users

**Travel Rule**:
- Counterparty info collection for >$3,000
- Stored in `travel_rule_data` JSONB field

**Sanctions Screening**:
- Pluggable interface for address checks
- Logged for audit trail
- Blocks until resolved

**Admin Access Reviews**:
- Monthly export of admin permissions
- Last login tracking
- Automated reports

### 8. Anti-Phishing & User Safety

**Withdrawal Address Book**:
- Users must add and confirm addresses
- 24-hour cooldown before first withdrawal
- 2FA required for confirmation

**Email Notifications**:
- Login from new device
- New API key created
- Withdrawal initiated/completed
- Falls back to in-app notifications

### 9. Deployment Hardening

**Docker**:
- Non-root user (UID 1000)
- Read-only filesystem
- Dropped capabilities (ALL, add only needed)
- Resource limits (CPU, memory, PIDs)

**Nginx**:
- TLS 1.2/1.3 only
- Strong cipher suites
- Security headers (HSTS, CSP, X-Frame-Options)
- Bot detection and blocking
- Request size limits (1 MB)
- Timeout controls

**Kubernetes** (if used):
- NetworkPolicies for pod isolation
- PodSecurityContext (runAsNonRoot, etc.)
- Resource quotas
- Secret management via sealed secrets

## Configuration

### Required Secrets

Generate strong random values for these:

```bash
# JWT & Session
JWT_SECRET                    # 32+ characters
SESSION_SECRET               # 32 bytes, base64
TOTP_ENCRYPTION_KEY          # 32 bytes, base64

# Service Auth
SERVICE_AUTH_KEYRING         # key1:base64,key2:base64
API_SIGNING_KEYS             # key1:base64,key2:base64

# Database & Redis
DB_PASSWORD                  # Strong password
REDIS_PASSWORD               # Strong password

# BitGo
BITGO_ACCESS_TOKEN           # From BitGo dashboard
BITGO_WEBHOOK_SECRET         # From BitGo webhook config
BITGO_WALLET_PASSPHRASE      # Wallet unlock passphrase

# Animica Wallet
ANIMICA_WALLET_PASSPHRASE    # Keystore encryption passphrase
```

### Rotation Procedures

**API Keys** (keyring-based):
1. Generate new key: `node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"`
2. Add to keyring: `API_SIGNING_KEYS=old_key:...,new_key:...`
3. Deploy updated config
4. Wait for all services to update
5. Remove old key from keyring

**Service Tokens**: Same process as API keys

**Database/Redis Passwords**:
1. Update in secrets manager
2. Rolling restart services
3. No downtime required

## Testing

### Security Test Suite

```bash
# Run all security tests
cd packages/security
pnpm test

# Test rate limiting
cd packages/middleware
pnpm test rate_limit

# Test auth flows
cd services/admin-api
pnpm test auth
```

### Manual Testing

**2FA Setup**:
1. Admin signs up → email + password
2. Admin enables TOTP → QR code displayed
3. Admin scans with authenticator app
4. Admin generates backup codes
5. Test login with TOTP
6. Test login with backup code

**Rate Limiting**:
```bash
# Should succeed
for i in {1..5}; do curl -X POST https://api/login; done

# Should return 429 (rate limited)
for i in {6..10}; do curl -X POST https://api/login; done
```

**Service Auth**:
```bash
# Should fail (no token)
curl http://ledger-service/internal/credit

# Should succeed (with token)
TOKEN=$(node generate_service_token.js)
curl -H "Authorization: Bearer $TOKEN" http://ledger-service/internal/credit
```

## Monitoring

### Key Metrics

- `auth_login_attempts_total` - Track login attempts
- `auth_login_failures_total` - Track failures
- `auth_lockouts_total` - Account lockouts
- `rate_limit_exceeded_total` - Rate limit hits
- `api_request_duration_seconds` - Request latency
- `audit_log_entries_total` - Audit log growth

### Alerts

1. **High login failure rate**: >10% failures
2. **Repeated lockouts**: Same user/IP locked multiple times
3. **Rate limit abuse**: Sustained 429s from IP
4. **Failed service auth**: Internal auth failures
5. **Audit log gaps**: Missing sequence numbers

### Dashboards

- Authentication overview (logins, failures, lockouts)
- Rate limiting (per endpoint, per IP)
- Service health (internal auth, latency)
- Audit trail (events/sec, export status)

## Documentation

- **[Security Baseline](./docs/security_baseline.md)** - Comprehensive security overview
- **[Audit Readiness](./docs/audit_readiness.md)** - Compliance and audit procedures
- **[Incident Runbook](./docs/incident_runbook.md)** - Incident response procedures

## Compliance Checklist

- [ ] SOC 2 Type II controls implemented
- [ ] Audit logs immutable and exportable
- [ ] Access reviews scheduled (monthly/quarterly)
- [ ] Secrets rotated regularly
- [ ] 2FA enforced for all admins
- [ ] Withdrawal limits by KYC status
- [ ] Travel rule data collection
- [ ] Data retention policies documented
- [ ] Incident response plan tested
- [ ] Security training completed

## Support

For security issues or questions:
- **Security Team**: security@animica.com
- **On-call**: Use PagerDuty escalation
- **Documentation**: See `docs/` folder

## License

Apache 2.0
