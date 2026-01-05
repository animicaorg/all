# Animica Compute Platform - Security Threat Model

This document outlines the security considerations, threat model, and mitigations for the Animica Compute + LLM Cloud Platform.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Trust Boundaries](#trust-boundaries)
3. [Threat Categories](#threat-categories)
4. [Threats and Mitigations](#threats-and-mitigations)
5. [Security Controls](#security-controls)
6. [Incident Response](#incident-response)
7. [Compliance](#compliance)

## Architecture Overview

```
┌─────────────┐
│   Internet  │
└──────┬──────┘
       │ HTTPS
       ▼
┌─────────────────────────────────────┐
│     Load Balancer (TLS Termination) │
└──────┬──────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│          API Gateway                 │
│  - Rate Limiting                     │
│  - Authentication                    │
│  - Request Validation                │
└──────┬──────────────────────────────┘
       │
       ├──> Auth Service (JWT, Wallet)
       ├──> Billing Service (Payments)
       ├──> Inference Service (LLM)
       ├──> Sandbox Runner (Isolated)
       ├──> GitHub App (Webhooks)
       └──> Model Registry
              │
              ▼
       ┌─────────────────┐
       │  Data Layer     │
       │  - PostgreSQL   │
       │  - Redis        │
       │  - MinIO (S3)   │
       └─────────────────┘
```

## Trust Boundaries

### External Trust Boundary
- **Untrusted**: Public internet, user input, external APIs
- **Trusted**: Internal service mesh, databases

### Internal Trust Boundaries
- **High Trust**: Database, Redis, internal service communication
- **Medium Trust**: User-authenticated requests
- **Low Trust**: Unauthenticated requests, external webhooks
- **Untrusted**: User-provided code (sandbox), model inputs

## Threat Categories

Using STRIDE methodology:

- **S**poofing: Identity verification
- **T**ampering: Data integrity
- **R**epudiation: Audit logging
- **I**nformation Disclosure: Data leakage
- **D**enial of Service: Availability
- **E**levation of Privilege: Authorization

## Threats and Mitigations

### 1. Authentication & Authorization (Spoofing, Elevation)

#### T1.1: JWT Token Theft
**Threat**: Attacker steals JWT token via XSS, MITM, or local storage access
**Impact**: High - Full account access
**Mitigations**:
- ✅ Short token expiration (15 minutes)
- ✅ Refresh token rotation
- ✅ HTTPS only (TLS 1.3)
- ⚠️ TODO: HttpOnly cookies for web clients
- ⚠️ TODO: Token blacklisting on logout
- ⚠️ TODO: Device fingerprinting

#### T1.2: Weak Password
**Threat**: User chooses weak password, susceptible to brute force
**Impact**: Medium - Account compromise
**Mitigations**:
- ✅ Bcrypt with cost factor 12
- ⚠️ TODO: Password complexity requirements
- ⚠️ TODO: Rate limiting on login attempts
- ⚠️ TODO: Account lockout after N failed attempts
- ⚠️ TODO: Breach password detection

#### T1.3: Wallet Signature Replay
**Threat**: Attacker replays wallet signature to authenticate
**Impact**: High - Account hijacking
**Mitigations**:
- ✅ Challenge-response with 5-minute expiration
- ✅ Redis-backed nonce storage
- ✅ Single-use challenges
- ✅ Timestamp validation

#### T1.4: API Key Leakage
**Threat**: API key exposed in code, logs, or GitHub
**Impact**: High - Unauthorized API access
**Mitigations**:
- ✅ SHA-256 hashing before storage
- ✅ Prefix for detection (`anm_`)
- ⚠️ TODO: Secret scanning in repos
- ⚠️ TODO: IP restrictions per key
- ⚠️ TODO: Key rotation notifications
- ⚠️ TODO: Usage anomaly detection

### 2. Payment & Billing (Tampering, Information Disclosure)

#### T2.1: Credit Balance Manipulation
**Threat**: Attacker manipulates credit balance through race conditions
**Impact**: Critical - Financial fraud
**Mitigations**:
- ✅ Database transactions
- ✅ Balance calculated from ledger
- ⚠️ TODO: Optimistic locking
- ⚠️ TODO: Real-time fraud detection
- ⚠️ TODO: Reconciliation jobs

#### T2.2: Stripe/PayPal Webhook Spoofing
**Threat**: Attacker sends fake webhook to credit account
**Impact**: Critical - Free credits
**Mitigations**:
- ⚠️ TODO: Webhook signature verification (Stripe HMAC-SHA256)
- ⚠️ TODO: Idempotency keys
- ⚠️ TODO: IP whitelist for webhook sources
- ⚠️ TODO: Webhook event validation against Stripe API

#### T2.3: Double-Spending ANM Tokens
**Threat**: User attempts to use same on-chain payment twice
**Impact**: High - Financial fraud
**Mitigations**:
- ✅ Transaction hash uniqueness constraint
- ✅ Confirmation count requirement (6 blocks)
- ⚠️ TODO: Reorg detection
- ⚠️ TODO: Payment intent expiration

#### T2.4: Usage Tracking Bypass
**Threat**: Attacker bypasses usage recording to avoid charges
**Impact**: High - Revenue loss
**Mitigations**:
- ⚠️ TODO: Middleware-enforced tracking
- ⚠️ TODO: Pre-flight credit checks
- ⚠️ TODO: Post-completion reconciliation
- ⚠️ TODO: Audit trail for all operations

### 3. LLM Inference (Information Disclosure, DoS)

#### T3.1: Prompt Injection
**Threat**: Attacker crafts prompt to extract system prompts or bypass filters
**Impact**: Medium - Information disclosure
**Mitigations**:
- ⚠️ TODO: Input sanitization
- ⚠️ TODO: System prompt isolation
- ⚠️ TODO: Output filtering
- ⚠️ TODO: Prompt injection detection

#### T3.2: Model Poisoning
**Threat**: Malicious model uploaded to model registry
**Impact**: Critical - Backdoored responses
**Mitigations**:
- ⚠️ TODO: Model signature verification
- ⚠️ TODO: Automated scanning for malicious patterns
- ⚠️ TODO: Sandboxed model loading
- ⚠️ TODO: Audit logs for model updates

#### T3.3: GPU Resource Exhaustion
**Threat**: Attacker sends requests to exhaust GPU memory/compute
**Impact**: High - Service unavailability
**Mitigations**:
- ✅ Max tokens limit
- ✅ Request timeout (60s)
- ⚠️ TODO: Per-user rate limits
- ⚠️ TODO: Cost-based prioritization
- ⚠️ TODO: Circuit breakers

#### T3.4: Model Training Data Extraction
**Threat**: Attacker extracts training data through carefully crafted prompts
**Impact**: High - Privacy violation, PII exposure
**Mitigations**:
- ⚠️ TODO: Output filtering for PII
- ⚠️ TODO: Rate limiting on similar prompts
- ⚠️ TODO: Anomaly detection
- ⚠️ TODO: Use privacy-preserving models

### 4. Code Sandbox (Elevation of Privilege, DoS)

#### T4.1: Sandbox Escape
**Threat**: Attacker breaks out of sandbox to access host system
**Impact**: Critical - Host compromise
**Mitigations**:
- ⚠️ TODO: gVisor or Firecracker isolation
- ⚠️ TODO: No privileged operations
- ⚠️ TODO: Read-only filesystem (except /tmp)
- ⚠️ TODO: Network namespace isolation
- ⚠️ TODO: Seccomp/AppArmor profiles

#### T4.2: Resource Exhaustion (Fork Bomb)
**Threat**: User code creates processes/threads to exhaust resources
**Impact**: High - DoS for all users
**Mitigations**:
- ✅ Process/thread limits
- ✅ Timeout enforcement (30s default)
- ⚠️ TODO: CPU quota (cgroups)
- ⚠️ TODO: Memory limits (512MB)
- ⚠️ TODO: Disk I/O limits

#### T4.3: Network Access
**Threat**: User code makes unauthorized external requests
**Impact**: Medium - Data exfiltration, C2 communication
**Mitigations**:
- ✅ Minimal environment variables
- ⚠️ TODO: Network disabled by default
- ⚠️ TODO: Whitelist for allowed domains
- ⚠️ TODO: Egress firewall rules
- ⚠️ TODO: DNS filtering

#### T4.4: Crypto Mining
**Threat**: User submits code to mine cryptocurrency
**Impact**: Medium - Resource abuse
**Mitigations**:
- ✅ Timeout limits
- ⚠️ TODO: CPU usage monitoring
- ⚠️ TODO: Heuristic detection (repeated patterns)
- ⚠️ TODO: Cost attribution

### 5. GitHub Integration (Tampering, Spoofing)

#### T5.1: Webhook Spoofing
**Threat**: Attacker sends fake GitHub webhook to trigger actions
**Impact**: High - Unauthorized PR creation, code execution
**Mitigations**:
- ⚠️ TODO: Webhook signature verification (HMAC-SHA256)
- ⚠️ TODO: IP whitelist (GitHub's IPs)
- ⚠️ TODO: Event validation

#### T5.2: Repository Poisoning
**Threat**: Attacker submits malicious code via PR
**Impact**: High - Supply chain attack
**Mitigations**:
- ⚠️ TODO: Dependency scanning
- ⚠️ TODO: Static analysis on PRs
- ⚠️ TODO: Manual review for sensitive repos
- ⚠️ TODO: Restricted file access

### 6. Data Storage (Information Disclosure, Tampering)

#### T6.1: SQL Injection
**Threat**: Attacker injects SQL via user input
**Impact**: Critical - Database compromise
**Mitigations**:
- ✅ SQLAlchemy ORM (parameterized queries)
- ✅ Pydantic input validation
- ⚠️ TODO: Prepared statements everywhere
- ⚠️ TODO: Least privilege DB user

#### T6.2: Database Credentials Exposure
**Threat**: Database credentials leaked in code, logs, or environment
**Impact**: Critical - Full data access
**Mitigations**:
- ✅ Environment variables (not hardcoded)
- ⚠️ TODO: Secrets manager (AWS SSM, Vault)
- ⚠️ TODO: Credential rotation
- ⚠️ TODO: IAM authentication (RDS)

#### T6.3: Unencrypted Data at Rest
**Threat**: Database backup stolen, plaintext data exposed
**Impact**: High - Privacy violation
**Mitigations**:
- ⚠️ TODO: Database encryption at rest
- ⚠️ TODO: Backup encryption
- ⚠️ TODO: Encrypted S3 buckets
- ⚠️ TODO: Key management (KMS)

### 7. Denial of Service

#### T7.1: Rate Limiting Bypass
**Threat**: Attacker bypasses rate limits via distributed IPs
**Impact**: Medium - Service degradation
**Mitigations**:
- ⚠️ TODO: Multi-tier rate limiting (IP, user, org)
- ⚠️ TODO: Token bucket algorithm
- ⚠️ TODO: Redis-backed rate limiting
- ⚠️ TODO: CAPTCHA for suspicious activity

#### T7.2: Large Request Payload
**Threat**: Attacker sends huge requests to exhaust memory
**Impact**: Medium - Memory exhaustion
**Mitigations**:
- ⚠️ TODO: Request size limits (1MB)
- ⚠️ TODO: Streaming for large files
- ⚠️ TODO: Request timeout

### 8. Supply Chain

#### T8.1: Compromised Dependencies
**Threat**: Malicious code in npm/PyPI dependencies
**Impact**: Critical - Backdoor in services
**Mitigations**:
- ⚠️ TODO: Dependency pinning
- ⚠️ TODO: Automated security updates (Dependabot)
- ⚠️ TODO: SBOM generation
- ⚠️ TODO: Regular audits (`npm audit`, `pip-audit`)

#### T8.2: Malicious Docker Image
**Threat**: Base image contains malware
**Impact**: Critical - Container compromise
**Mitigations**:
- ✅ Official base images (python:3.11-slim)
- ⚠️ TODO: Image scanning (Trivy)
- ⚠️ TODO: Signed images (Docker Content Trust)
- ⚠️ TODO: Private registry

## Security Controls

### Implemented ✅

1. **Authentication**:
   - JWT with short expiration (15 min)
   - Bcrypt password hashing (cost 12)
   - Wallet signature challenge-response
   - API key hashing (SHA-256)

2. **Authorization**:
   - RBAC (owner, admin, member)
   - Organization-based multi-tenancy
   - API key scoping

3. **Data Validation**:
   - Pydantic models for all inputs
   - SQLAlchemy ORM (parameterized queries)
   - Type checking

4. **Resource Limits**:
   - Sandbox timeouts (30s default)
   - Max tokens for LLM (configurable)
   - Process limits in sandbox

5. **Docker Security**:
   - Non-root users in containers
   - Health checks
   - Minimal base images

### TODO (High Priority) ⚠️

1. **Rate Limiting**:
   - Implement Redis-backed rate limiting
   - Per-user, per-org, per-API-key limits
   - Adaptive rate limiting

2. **Webhook Security**:
   - Signature verification (Stripe, PayPal, GitHub)
   - IP whitelisting
   - Idempotency

3. **Sandbox Hardening**:
   - Migrate to gVisor or Firecracker
   - Network isolation
   - Resource quotas (cgroups)

4. **Secrets Management**:
   - Migrate to AWS SSM or HashiCorp Vault
   - Credential rotation
   - Audit access

5. **Monitoring & Alerting**:
   - Failed auth attempt alerts
   - Credit balance anomaly detection
   - Resource usage spikes

## Incident Response

### Detection
- Failed authentication alerts (>5/min)
- Credit balance drops (>1000 in <1 hour)
- Sandbox escape attempts
- Unusual API patterns

### Response Procedure
1. **Identify**: Monitor alerts, logs, metrics
2. **Contain**: Disable compromised accounts/keys
3. **Eradicate**: Patch vulnerability, rotate secrets
4. **Recover**: Restore from backups, validate integrity
5. **Lessons Learned**: Post-mortem, update controls

### Contacts
- **Security Team**: security@animica.ai
- **On-Call**: PagerDuty
- **Incident Response Lead**: TBD

## Compliance

### GDPR
- User data deletion on request
- Data export functionality
- Audit logs retention (90 days)
- Privacy policy

### PCI DSS (Stripe/PayPal handle card data)
- Never store card numbers
- PCI-compliant payment processors
- Secure webhooks

### SOC 2 (Future)
- Access controls
- Encryption in transit and at rest
- Audit logging
- Incident response plan

## Security Testing

### Regular Activities
- Weekly dependency scans
- Monthly penetration tests (future)
- Quarterly security audits (future)
- Continuous vulnerability scanning (Trivy)

### Bug Bounty (Future)
- Scope: All services except internal tools
- Rewards: $100-$5000 based on severity
- Platform: HackerOne

## References

- OWASP Top 10: https://owasp.org/www-project-top-ten/
- STRIDE Threat Modeling: https://docs.microsoft.com/en-us/azure/security/develop/threat-modeling-tool
- CWE Top 25: https://cwe.mitre.org/top25/

## Changelog

- 2024-01-05: Initial threat model created
- TBD: Regular updates after security reviews

---

**Document Status**: Draft
**Last Updated**: 2024-01-05
**Owner**: Platform Security Team
