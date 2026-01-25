# Security Baseline - Animica CEX Platform

**Version:** 1.0 | **Last Updated:** January 2025 | **Owner:** Security Team

---

## Executive Summary

This document defines the comprehensive security baseline for the Animica CEX platform. It establishes minimum security controls across all system components to ensure regulatory compliance, protect customer assets, and maintain operational integrity.

**Scope:** All CEX services (matching, ledger, wallet routing), authentication systems, custody integrations (BitGo, Animica chain), API gateway, and administrative tools.

---

## 1. Secrets Management

### 1.1 Classification & Handling

| Type | Examples | Storage Method | Rotation |
|------|----------|---------------|----------|
| **Critical** | BitGo tokens, DB passwords, HSM keys | Secrets manager only (AWS/GCP SM) | 90 days |
| **High** | API keys, service tokens, JWT secrets | Secrets manager or encrypted env | 180 days |
| **Medium** | Redis passwords, NATS credentials | Encrypted configuration | 365 days |
| **Low** | Non-prod tokens | Environment variables | As needed |

### 1.2 Storage Requirements

**MANDATORY CONTROLS:**
- Production secrets MUST be in managed secrets services (AWS Secrets Manager, GCP Secret Manager)
- MUST NOT appear in source code, config files, or container images
- Encrypted at rest (AES-256 minimum), transmitted over TLS 1.3 only
- Access only via IAM roles/service accounts (no human access except break-glass)
- All secret access logged to immutable audit trail

**Implementation Reference:**
```typescript
// packages/security/src/secrets/providers/
// - aws_sm.ts: AWS Secrets Manager
// - gcp_sm.ts: GCP Secret Manager  
// - env.ts: Environment (non-prod only)
```

### 1.3 Redaction & Logging

**NEVER LOG:** Complete secrets, API keys, passwords, private keys, BitGo tokens, JWT tokens, session IDs, mnemonic phrases, backup codes, TOTP seeds, credit card data

**Auto-Redaction:** `packages/security/src/secrets/redaction.ts` redacts password, token, secret, api_key, private_key, mnemonic, backup_code, access_token

**Safe Practices:**
- Log only key IDs/fingerprints: `"api_key": "a1b2c3d4****"` or `"key_id": "key_prod_12345"`
- Mask PII: email → `u***@domain.com`, address → `1abc...xyz`

### 1.4 Rotation Procedures

**90-DAY ROTATION (BitGo, Database):**
1. Generate new credential
2. Update in secrets manager
3. Rolling deploy to services
4. Revoke old after 24h grace period
5. Verify functionality

**180-DAY ROTATION (API Keys):**
1. Generate new key with same permissions
2. Notify client 7 days advance
3. Enable new, disable old after grace
4. Monitor failed auth attempts

**EMERGENCY:** Immediate revoke, deploy replacement within 1 hour, audit all access, file incident report

---

## 2. Authentication & Authorization

### 2.1 User Authentication

**PASSWORD REQUIREMENTS:**
- Minimum 12 characters: uppercase + lowercase + number + special character
- No common passwords (check breach databases)
- Prevent reuse of last 10 passwords
- Argon2id hashing (work factor 19, per-user salt)

**Implementation:** `packages/security/src/auth/password.ts`

### 2.2 Two-Factor Authentication (2FA)

**MANDATORY FOR:**
- All user accounts (7-day grace period after signup)
- Admin/operator accounts (immediate)
- Withdrawals >$1,000 USD equivalent
- Account security changes

**SUPPORTED METHODS:**

**1. TOTP (Primary)** - `packages/security/src/auth/totp.ts`
- RFC 6238 compliant, 30-second window, 6-digit codes
- QR code enrollment (Google Authenticator, Authy, 1Password)
- Secret encrypted in database
- Clock skew: ±1 window (90s total)

**2. Backup Codes (Recovery)** - `packages/security/src/auth/backup_codes.ts`
- 10 single-use codes at enrollment
- Format: `XXXX-XXXX` (8 chars, alphanumeric)
- Bcrypt hashed (cost 12)
- Regeneration requires 2FA verification
- One-time use, then invalidated

**FLOW:** Registration → [7-day grace] → 2FA Required → Login → Password → 2FA Challenge → TOTP/Backup → Session

**RECOVERY:**
- Lost TOTP: Use backup codes
- Lost backup codes: ID verification + manual review (24-48h)
- Suspicious recovery: Government ID + video verification required

### 2.3 Session Management

**Implementation:** `packages/security/src/auth/session.ts`

**TOKEN:** 256-bit cryptographic random, base64url encoded  
**STORAGE:** Redis with TTL (30min idle, 24h absolute)  
**COOKIES:**
- `HttpOnly`: Yes (prevent XSS)
- `Secure`: Yes (HTTPS only)
- `SameSite`: Strict (CSRF protection)
- `Domain`: `.animica.exchange`

**POLICIES:**
- Idle timeout: 30 minutes
- Absolute timeout: 24 hours
- Max concurrent: 3 per user
- Optional IP binding (high-risk accounts)
- Device fingerprinting for anomalies

**INVALIDATION:**
- Logout: Immediate
- Password change: All sessions terminated
- 2FA reset: All sessions terminated
- Suspicious activity: Immediate + alert
- Admin-initiated: Immediate + audit log

### 2.4 Anti-Phishing

**Implementation:** `packages/security/src/auth/anti_phishing.ts`

- **Security Image:** User selects image + passphrase at enrollment, shown after username entry
- **Email Auth:** All emails include user's anti-phishing phrase + unique code
- **Domain Verification:** Official: `animica.exchange`, warn about lookalikes, DMARC/SPF/DKIM

### 2.5 API Key Management

**FORMAT:**
- Production: `ak_live_<32_bytes_base58>` / `sk_live_<64_bytes_base58>`
- Testnet: `ak_test_...` / `sk_test_...`

**PERMISSIONS:**
- Read-only: Market data, balances (no trading)
- Trade: Place/cancel orders (no withdrawals)
- Transfer: Withdrawals (requires IP whitelist + 2FA)

**SECURITY:**
- Secret shown once (never retrievable)
- Argon2id hashed before storage
- IP whitelist (up to 10 addresses)
- Rate limits per key
- Auto-revoke after 180 days inactivity
- Max 5 active keys per user

**HMAC AUTH:**
```
Authorization: HMAC-SHA256 KeyId=ak_live_..., Signature=<base64>
Canonical: METHOD + URI + QUERY + TIMESTAMP + BODY_SHA256
```

---

## 3. Service-to-Service Authentication

### 3.1 Internal Service Mesh

**METHOD:** mTLS + JWT (`packages/middleware/src/service_auth.ts`)

**SERVICE IDENTITY:**
- Unique X.509 certificate per service
- CN: `service.<service_name>.animica.internal`
- 30-day validity, auto-rotation (cert-manager/ACM)

**JWT TOKENS:**
- Issued by auth service
- Payload: `{sub: "service_name", scope: "read:ledger write:orders", exp: ...}`
- Signed with ES256 (ECDSA P-256)
- 1-hour expiration, auto-renewed

**POLICIES:**
```typescript
const servicePolicies = {
  "api-gateway": ["read:ledger", "write:matching"],
  "matching-engine": ["read:ledger", "write:ledger"],
  "wallet-router": ["read:ledger", "write:bitgo", "write:animica"],
  "admin-service": ["read:*", "write:*"]
};
```

### 3.2 Database Access (Least Privilege)

**SERVICE-SPECIFIC ROLES:**
```sql
-- Withdrawals Service
CREATE ROLE withdrawals_service;
GRANT CONNECT ON DATABASE cex_prod TO withdrawals_service;
GRANT USAGE ON SCHEMA withdrawals TO withdrawals_service;
GRANT SELECT, INSERT, UPDATE ON withdrawals.withdrawal_requests TO withdrawals_service;
GRANT SELECT ON ledger.balances TO withdrawals_service; -- Read-only
-- NO DELETE, NO TRUNCATE, NO ALTER

-- Ledger Service (only balance writer)
CREATE ROLE ledger_service;
GRANT SELECT, INSERT, UPDATE ON ledger.journal_entries TO ledger_service;
GRANT SELECT, INSERT, UPDATE ON ledger.balances TO ledger_service;
-- EXCLUSIVE write access
```

**CONNECTION:**
- SSL/TLS required (`sslmode=require`)
- Certificate validation (`sslrootcert=/path/to/ca.crt`)
- Private subnet only (no public access)
- Connection pooling with limits

**AUDIT:** pgaudit extension, log all DDL + writes to sensitive tables, forward to SIEM

### 3.3 NATS Message Queue

- JWT auth per service
- Subject-level ACLs (publish/subscribe)
- TLS encryption
- No anonymous access

**EXAMPLE ACLs:**
```
api-gateway: publish[cex.orders.submit], subscribe[cex.orders.status.>, cex.trades.>]
matching-engine: publish[cex.orders.status.>, cex.trades.*], subscribe[cex.orders.submit]
ledger-service: publish[cex.ledger.updated], subscribe[cex.trades.*, cex.deposits.*, cex.withdrawals.*]
```

---

## 4. Rate Limiting & Abuse Prevention

### 4.1 Rate Limits (Redis Token Bucket)

**Implementation:** `packages/middleware/src/rate_limit.ts`

| Endpoint | Auth Users | API Read | API Trade | Unauth |
|----------|-----------|----------|-----------|--------|
| Market Data | 100 req/s | 50 req/s | 50 req/s | 10 req/s |
| Orders | 10 req/s | N/A | 5 req/s | N/A |
| Withdrawals | 5 req/hr | N/A | 3 req/hr | N/A |
| Login | 5 req/15min | N/A | N/A | 3 req/15min |
| Account | 20 req/min | N/A | N/A | N/A |

**BURST:** Market data 2x/5s, orders 1.5x/1s, none for sensitive ops

### 4.2 DDoS Protection (Layered)

**1. Edge (Cloudflare/AWS Shield):** L3/L4 volumetric mitigation, geo-blocking, CAPTCHA challenges  
**2. Gateway:** 1MB request limit, query depth limits, 100 connections/IP, 30s timeout  
**3. Application:** Redis rate limiting, adaptive throttling, health-based fail-open

**ALERTS:**
- 5x traffic: Warning
- 10x traffic: Critical + auto-mitigation
- 50% 4xx increase: Attack/misconfiguration
- Unusual geo distribution: Botnet

### 4.3 Fraud Prevention

**WITHDRAWAL LIMITS (24h rolling):**
```typescript
{ tier1_unverified: {daily: 1000, weekly: 3000},
  tier2_basic_kyc:  {daily: 10000, weekly: 50000},
  tier3_enhanced:   {daily: 100000, weekly: 500000},
  tier4_institutional: {daily: 1000000, weekly: 5000000} }
```

**ANOMALIES:**
- First withdrawal address: Extra confirmation
- New address <24h after add: Manual review
- Withdrawal >50% balance: 2FA + email confirm
- Unusual trading: Flag for review
- Failed 2FA attempts: Temporary lock

**ADDRESS WHITELIST:** Optional, 48h delay for new addresses, immediate to whitelisted

---

## 5. Logging & Audit Trails

### 5.1 Requirements

**IMMUTABILITY:** Append-only, no deletion/modification, tamper-evident (hash chaining), 7-year retention minimum

**WHAT TO LOG:**

| Event | Fields | Retention |
|-------|--------|-----------|
| Auth | user_id, ip, user_agent, timestamp, success, 2fa_method | 7 years |
| Authz Failures | user_id, resource, action, reason, timestamp | 7 years |
| Financial Tx | tx_id, user_id, asset, amount, type, status, balance_before/after | 7 years |
| Withdrawals | withdrawal_id, user_id, asset, amount, address, status, approvals | 7 years |
| Admin Actions | admin_id, action, target, old/new_value, reason, timestamp | 7 years |
| API Access | api_key_id, endpoint, method, status, response_time | 90 days |
| Security Events | type, severity, details, timestamp, source_ip | 7 years |

**FORMAT (JSON):**
```json
{"timestamp": "2025-01-25T10:30:45.123Z", "level": "INFO", "service": "withdrawals-service",
 "event_type": "withdrawal.approved", "user_id": "usr_abc123", "session_id": "sess_xyz789",
 "ip_address": "203.0.113.42", "withdrawal_id": "wd_123456", "asset": "BTC", "amount": "0.5",
 "destination": "bc1q...xyz", "approver_id": "adm_admin001", "approval_method": "2fa_totp",
 "risk_score": 0.15, "trace_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}
```

### 5.2 Aggregation

**INFRASTRUCTURE:** ELK Stack / AWS CloudWatch, real-time streaming, TLS in-transit + AES-256 at-rest, access: security team + on-call only

**ALERTS:**
```yaml
- "Multiple Failed Logins": count(auth.failure) > 5 in 15min by user → lock + notify
- "Large Withdrawal": withdrawal.approved.usd > 100000 → notify senior admin
- "Service Error Rate": error_rate > 5% in 5min → page on-call + auto-scale
- "Privilege Escalation": admin.role.changed to super_admin → immediate security notify
```

### 5.3 Common Queries

```sql
-- User activity timeline
SELECT timestamp, event_type, details FROM audit_logs WHERE user_id='usr_abc123' ORDER BY timestamp DESC LIMIT 100;

-- Failed withdrawals (24h)
SELECT * FROM audit_logs WHERE event_type='withdrawal.failed' AND timestamp > NOW() - INTERVAL '24 hours' ORDER BY timestamp DESC;

-- Admin actions on user
SELECT admin_id, action, timestamp FROM audit_logs WHERE event_type LIKE 'admin.%' AND target_user_id='usr_abc123' ORDER BY timestamp DESC;
```

---

## 6. Key Custody & Wallet Security

### 6.1 BitGo Integration

**MULTI-SIG:** 2-of-3 or 3-of-5 (1 BitGo, 1 Exchange HSM, 1 Backup offline)

**CONTROLS:**
- Token rotation: 90 days (automated)
- IP whitelist: Exchange servers only
- Webhook signature: HMAC-SHA256 mandatory
- Policies: Velocity limits, max tx size, multi-approver for large transfers

**IMPLEMENTATION:**
```typescript
// services/bitgo-webhook-ingestor/src/http/middleware/auth.ts
BITGO_ENV=prod
BITGO_ACCESS_TOKEN=<secrets_manager>
BITGO_WEBHOOK_SECRET=<secrets_manager>

const sig = req.headers['x-bitgo-signature'];
const computed = crypto.createHmac('sha256', SECRET).update(req.body).digest('hex');
if (sig !== computed) throw Error('Invalid signature');
```

### 6.2 HSM (Hardware Security Module)

**PRODUCTION:** FIPS 140-2 Level 3 (AWS CloudHSM, Thales Luna), keys never leave HSM, dual custodian control

**KEY HIERARCHY:**
```
Master (HSM, never exported)
├─ BTC Signing (m/44'/0'/0')
├─ ETH Signing (m/44'/60'/0')
├─ Service Auth (ephemeral, daily rotation)
└─ Backup Encryption (cold storage)
```

**DEV ONLY:** `packages/security/src/signing/hsm_stub.ts` (filesystem storage - NOT FOR PROD)

### 6.3 Hot vs Cold Wallets

**HOT (Online, BitGo Multi-sig):** 5-10% total assets, daily withdrawals, BitGo policies, real-time monitoring  
**COLD (Offline, Multi-sig):** 90-95% total assets, physical custodian access, weekly rebalancing

**SEGREGATION:** Customer funds segregated from operational, no commingling, daily reconciliation

### 6.4 Animica Native Asset

**LOCAL NODE:**
- Dedicated `animica-node` on localhost RPC
- Wallet encrypted with master passphrase (HSM-stored)
- Private keys never exposed via RPC

**Implementation:** `services/animica-indexer/`, `services/animica-asset-service/`

**CONFIRMATIONS:**
- Standard deposits: 12 confirmations (~2min at 10s blocks)
- Large deposits (>$10k): 24 confirmations
- Reorg protection: Monitor forks, pause if detected

---

## 7. Network Security

### 7.1 Segmentation

```
PUBLIC ZONE (Internet) → API Gateway, CDN, BitGo Webhook
   ↓ TLS + Rate Limiting + DDoS
DMZ (App Tier) → Auth, Matching, Withdrawals, Admin (IP-restricted)
   ↓ mTLS + JWT
PRIVATE (Data) → PostgreSQL, Redis, NATS, Animica Node (no public IP)
   ↓ VPC Peering
MANAGEMENT → Bastion (MFA SSH), Monitoring, Logs
```

### 7.2 Firewall Rules

**PUBLIC:** 443 allow all, 80 redirect, else deny  
**DMZ:** 3000-4000 from Gateway only, SSH from Bastion only, else deny  
**PRIVATE:** 5432/6379/4222 from DMZ only, else deny  
**EGRESS:** DMZ→Internet 443 (BitGo), Private→Internet deny all, All→Secrets Manager 443

### 7.3 TLS/SSL

**MIN VERSION:** TLS 1.3 (TLS 1.2 for legacy)

**CIPHERS:** TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256, TLS_AES_128_GCM_SHA256, TLS_ECDHE_RSA_WITH_AES_256/128_GCM_SHA384/256

**CERTS:** Let's Encrypt / ACM, 90-day validity, auto-renew at 60d, OCSP stapling, HSTS: `max-age=31536000; includeSubDomains; preload`

**HEADERS:** `packages/middleware/src/security_headers.ts`
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Content-Security-Policy: default-src 'self'; ...
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=(), camera=()
```

---

## 8. Deployment Security

### 8.1 Containers

**BASE IMAGES:** Alpine/Distroless, scan with Trivy/Snyk, no secrets in images, run as non-root (UID 1000+)

**DOCKERFILE:**
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production

FROM node:20-alpine
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /app/node_modules ./node_modules
COPY --chown=appuser:appgroup . .
EXPOSE 3000
CMD ["node", "dist/index.js"]
```

**SCANNING:** Fail CI on HIGH/CRITICAL, daily scans with alerts, monthly base updates, 48h emergency patches

### 8.2 Kubernetes

**NAMESPACES:** Separate dev/staging/prod, deny inter-namespace by default, resource quotas

**POD SECURITY:** Restricted policy, read-only root FS, drop all capabilities except NET_BIND_SERVICE
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
  capabilities: {drop: ["ALL"]}
```

**SECRETS:** External secrets operator (AWS SM, Vault), never K8s Secrets directly, auto-rotate

### 8.3 CI/CD

**BUILD:** `npm audit`, Snyk/Dependabot, SonarQube, git-secrets/TruffleHog, fail on HIGH+ vulns or secrets

**DEPLOYMENT:**
- Dev: Auto-deploy on `develop` merge
- Staging: Auto-deploy on `main` merge
- Production: Manual (2 senior engineer approvals)

**ROLLBACK:** Blue/green (instant), canary (gradual), keep last 3 versions

---

## 9. Third-Party Integrations

### 9.1 BitGo Checklist

- ✅ Token in secrets manager
- ✅ Webhook signature validation
- ✅ IP whitelist configured
- ✅ Multi-sig (2-of-3 min)
- ✅ Spending policies (velocity, thresholds)
- ✅ Separate prod/test tokens
- ✅ 90-day rotation

**INCIDENT:** Token compromise → revoke immediately, suspicious withdrawals → pause + investigate, outage → queue + manual backup keys

### 9.2 Monitoring

**TOOLS:** Wazuh/OSSEC (IDS), Splunk/Elastic Security (SIEM), Nessus/Qualys (vuln scan weekly), quarterly pen tests + annual red team

**METRICS:** Auth failures, API error spikes, DB latency anomalies, network traffic, cert expiration

---

## 10. Compliance & Standards

### 10.1 Regulatory Alignment

- **FinCEN:** KYC/AML
- **SOC 2 Type II:** Security, availability, confidentiality
- **ISO 27001:** Info security management
- **GDPR:** Data protection (EU)
- **CCPA:** Privacy (California)

### 10.2 Maturity Roadmap

**CURRENT (MVP):**
- ✅ Env var secrets + manual rotation
- ✅ Argon2id passwords
- ✅ 2FA (TOTP + backup codes)
- ✅ Redis rate limiting
- ✅ Audit logging
- ✅ BitGo multi-sig
- ⚠️ Manual security reviews

**NEXT 3 MONTHS:**
- 🎯 Secrets manager (AWS/GCP)
- 🎯 Automated vuln scanning
- 🎯 HSM integration
- 🎯 WAF
- 🎯 DDoS protection (Cloudflare/Shield)

**NEXT 6 MONTHS:**
- 🎯 SOC 2 audit
- 🎯 Penetration testing
- 🎯 Bug bounty program
- 🎯 ML-based fraud detection
- 🎯 Zero-trust architecture

---

## 11. Roles & Responsibilities

| Role | Responsibilities |
|------|------------------|
| CISO | Strategy, risk, compliance |
| Security Engineer | Controls, vulns, incidents |
| DevOps/SRE | Deployments, hardening, monitoring |
| Backend Devs | Secure coding, reviews, threat modeling |
| Compliance Officer | Reporting, audits, policy docs |
| All Employees | Awareness, report suspicious activity |

---

## 12. Review & Updates

- **Review:** Quarterly or post-incident
- **Approval:** CISO + CTO
- **Change Mgmt:** All changes documented with rationale
- **Version Control:** Git with full history

---

## Appendix A: Contacts

- **Security Email:** security@animica.exchange
- **Incident Hotline:** +1-XXX-XXX-XXXX (24/7)
- **Bug Bounty:** https://bugcrowd.com/animica (coming soon)
- **Disclosure:** security@animica.exchange (PGP available)

---

## Appendix B: Critical Secrets Quick Reference

| Secret | Location | Rotation | Emergency Contact |
|--------|----------|----------|-------------------|
| BitGo Access Token | AWS SM: `prod/bitgo/access_token` | 90d | Security + BitGo Support |
| DB Master Password | AWS SM: `prod/db/master_password` | 90d | Database Admin |
| JWT Signing Key | AWS SM: `prod/jwt/signing_key` | 180d | Auth Service Owner |
| HSM Master Key | AWS CloudHSM | Never (offline backup) | CISO + 2 Custodians |

---

**Classification:** Internal - Security Sensitive  
**Distribution:** Security Team, Engineering Leadership, Compliance  
**Confidentiality:** Do not share externally without CISO approval
