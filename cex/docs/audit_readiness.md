# Audit Readiness Guide - Animica CEX Platform

**Version:** 1.0 | **Last Updated:** January 2025 | **Owner:** Compliance Team

---

## Executive Summary

This document provides comprehensive guidance for maintaining audit readiness and regulatory compliance for the Animica CEX platform. It defines requirements for audit logs, evidence collection, access reviews, and record-keeping to support SOC 2, ISO 27001, FinCEN, and other regulatory audits.

**Target Audits:**
- SOC 2 Type II (Security, Availability, Confidentiality)
- ISO 27001 (Information Security Management)
- FinCEN/AML Compliance (KYC/Anti-Money Laundering)
- State Money Transmitter Licenses
- Internal Security Audits (Quarterly)

---

## 1. Audit Log Requirements

### 1.1 Immutability & Tamper-Evidence

**REQUIREMENTS:**
- All audit logs MUST be append-only (no modification or deletion)
- Implement tamper-evident mechanisms (hash chaining, Merkle trees, or write-once storage)
- Store audit logs separately from operational databases
- Cryptographically sign log batches for integrity verification

**IMPLEMENTATION APPROACHES:**

**Option 1: Hash Chaining**
```typescript
// Each log entry includes hash of previous entry
interface AuditLogEntry {
  id: string;
  timestamp: string;
  event_type: string;
  data: object;
  previous_hash: string; // SHA-256 of previous entry
  current_hash: string;  // SHA-256 of this entry
}

// Verification: Recompute all hashes and compare with stored values
```

**Option 2: Merkle Tree (for periodic verification)**
```typescript
// Group logs into blocks, build Merkle tree per block
// Root hash stored immutably, allows efficient verification
```

**Option 3: Write-Once Storage**
- AWS S3 with Object Lock (Governance/Compliance mode)
- PostgreSQL with triggers preventing UPDATE/DELETE on audit tables
- Dedicated audit database with read-only replica for queries

**VERIFICATION PROCEDURE:**
1. Daily: Verify hash chain integrity (automated script)
2. Weekly: Export audit logs to write-once storage (S3, tape)
3. Monthly: Generate attestation report of log integrity
4. Quarterly: Third-party verification of audit log completeness

### 1.2 Comprehensive Event Coverage

**AUTHENTICATION & AUTHORIZATION:**

| Event | Fields | Example |
|-------|--------|---------|
| `auth.login.success` | user_id, ip, user_agent, 2fa_method, session_id | User login with TOTP |
| `auth.login.failure` | user_id/username, ip, reason (bad_password, bad_2fa) | Failed login attempt |
| `auth.logout` | user_id, session_id, reason (user, timeout, admin) | User logout or timeout |
| `auth.2fa.enrolled` | user_id, method (totp, backup_codes) | User enables 2FA |
| `auth.2fa.disabled` | user_id, admin_id (if admin action) | 2FA disabled |
| `auth.password.changed` | user_id, method (self, admin_reset) | Password change |
| `auth.session.invalidated` | user_id, session_id, reason | Session terminated |
| `authz.access.denied` | user_id, resource, action, reason | Authorization failure |

**FINANCIAL OPERATIONS:**

| Event | Fields | Example |
|-------|--------|---------|
| `ledger.deposit.confirmed` | user_id, asset, amount, tx_hash, confirmations | Blockchain deposit confirmed |
| `ledger.withdrawal.requested` | withdrawal_id, user_id, asset, amount, address | User withdrawal request |
| `ledger.withdrawal.approved` | withdrawal_id, approver_id, risk_score, approval_method | Admin/auto approval |
| `ledger.withdrawal.rejected` | withdrawal_id, reason | Withdrawal rejected |
| `ledger.withdrawal.completed` | withdrawal_id, tx_hash, fee, net_amount | On-chain completion |
| `ledger.balance.updated` | user_id, asset, balance_before, balance_after, reason | Any balance change |
| `ledger.journal.entry` | entry_id, debit_account, credit_account, amount, tx_type | Double-entry journal |
| `trade.executed` | trade_id, user_id, pair, side, quantity, price, fee | Trade execution |
| `order.placed` | order_id, user_id, pair, type, price, quantity | Order placed |
| `order.cancelled` | order_id, user_id, reason | Order cancelled |

**ADMINISTRATIVE ACTIONS:**

| Event | Fields | Example |
|-------|--------|---------|
| `admin.user.kyc_status_changed` | admin_id, user_id, old_status, new_status, reason | KYC approval/rejection |
| `admin.user.suspended` | admin_id, user_id, reason, duration | User account suspension |
| `admin.user.unsuspended` | admin_id, user_id, reason | Account reactivation |
| `admin.withdrawal.manually_approved` | admin_id, withdrawal_id, reason | Override approval |
| `admin.config.changed` | admin_id, parameter, old_value, new_value | System config change |
| `admin.wallet.rebalance` | admin_id, asset, from_wallet, to_wallet, amount | Hot/cold rebalance |
| `admin.role.assigned` | admin_id, target_user, role, permissions | Role assignment |

**SECURITY EVENTS:**

| Event | Fields | Example |
|-------|--------|---------|
| `security.api_key.created` | user_id, api_key_id, permissions, ip_whitelist | New API key |
| `security.api_key.revoked` | user_id, api_key_id, reason | Key revocation |
| `security.account.locked` | user_id, reason (failed_2fa, admin, fraud) | Account lock |
| `security.suspicious_activity` | user_id, activity_type, risk_score, details | Anomaly detection |
| `security.rate_limit.exceeded` | user_id/ip, endpoint, limit, actual | Rate limit hit |
| `security.secret.rotated` | secret_type, rotated_by, timestamp | Secret rotation |

### 1.3 Log Retention Policy

**REGULATORY REQUIREMENTS:**

| Jurisdiction | Requirement | Animica Policy |
|--------------|-------------|----------------|
| **FinCEN (USA)** | 5 years for financial transactions | 7 years (all financial) |
| **GDPR (EU)** | As long as necessary, deletion on request | 7 years (with user consent), 90 days for non-financial on deletion request |
| **SEC (USA)** | 6 years for securities (if applicable) | N/A (crypto assets, not securities) |
| **General** | Varies by state (typically 3-7 years) | 7 years (safe harbor) |

**ANIMICA RETENTION SCHEDULE:**

| Log Category | Retention Period | Storage Location |
|--------------|-----------------|------------------|
| Financial Transactions | 7 years | Primary DB + AWS S3 Glacier (after 1 year) |
| Authentication Logs | 7 years | Primary DB + S3 Glacier (after 1 year) |
| Authorization Failures | 7 years | Primary DB + S3 Glacier (after 1 year) |
| Admin Actions | 7 years | Primary DB + S3 Glacier (after 1 year) |
| API Access Logs | 90 days | Primary DB only |
| System Logs (non-audit) | 30 days | CloudWatch / ELK |
| Security Incidents | 7 years | Primary DB + S3 Glacier (immediate) |
| KYC/AML Records | 7 years post account closure | Encrypted S3 with compliance lock |

**DATA LIFECYCLE:**
1. **Active (0-90 days):** PostgreSQL primary, indexed, fast query
2. **Warm (91-365 days):** PostgreSQL compressed partitions, slower query
3. **Cold (1-7 years):** AWS S3 Glacier, query via Athena, 12-hour retrieval
4. **Archival (>7 years):** S3 Glacier Deep Archive, legal hold only, 48-hour retrieval
5. **Destruction (>7 years + no legal hold):** Cryptographic erasure of encryption keys, then physical deletion

### 1.4 Log Access Controls

**WHO CAN ACCESS AUDIT LOGS:**

| Role | Access Level | Justification Required |
|------|-------------|------------------------|
| **Compliance Officer** | Full read access (all logs) | No (part of duties) |
| **Security Team** | Full read access (all logs) | No (incident response) |
| **Internal Auditor** | Full read access (audit scope only) | Yes (audit request ticket) |
| **External Auditor** | Read access (time-bound, specific logs) | Yes (audit engagement contract) |
| **On-call Engineer** | Read access (service-specific logs) | Yes (active incident ticket) |
| **Regulators** | Read access (as requested) | Yes (official regulatory request) |
| **All Others** | No access | N/A |

**ACCESS PROCEDURE:**
1. Submit access request via ticketing system (Jira, ServiceNow)
2. Include: Purpose, scope (time range, log types), duration needed
3. Approval by: Compliance Officer or CISO
4. Access granted: Time-limited credentials (max 72 hours)
5. All access logged: Access timestamp, user, queries run, records viewed
6. Post-access review: Compliance team reviews access logs

**TECHNICAL CONTROLS:**
- Separate read-only database user for audit queries
- No UPDATE/DELETE permissions on audit tables
- Query audit trail (PostgreSQL logging, CloudTrail)
- Sensitive data redacted in standard views (PII masked unless explicit need)

---

## 2. Evidence Collection Procedures

### 2.1 Periodic Evidence Generation

**DAILY EVIDENCE:**
- [ ] System uptime report (availability SLA compliance)
- [ ] Backup verification report (successful backups, integrity checks)
- [ ] Failed login attempts summary (potential attacks)
- [ ] API error rate summary (service health)
- [ ] Certificate expiration check (30/7 day warnings)

**WEEKLY EVIDENCE:**
- [ ] User access review (new accounts, permission changes)
- [ ] Withdrawal approval audit (manual approvals, reasons)
- [ ] Database query performance (slow queries, anomalies)
- [ ] Vulnerability scan results (Nessus, Qualys)
- [ ] Secret rotation status (upcoming expirations)

**MONTHLY EVIDENCE:**
- [ ] Reconciliation report (customer balances vs blockchain holdings)
- [ ] Financial transaction summary (volume, value, anomalies)
- [ ] Security incident summary (incidents, resolutions, MTTR)
- [ ] Access control review (orphaned accounts, excessive permissions)
- [ ] Change management summary (deployments, rollbacks, config changes)
- [ ] Training completion status (security awareness, compliance)

**QUARTERLY EVIDENCE:**
- [ ] Full access review (all user accounts, permissions, last login)
- [ ] Vendor security assessment (BitGo, AWS, third-party services)
- [ ] Disaster recovery test (backup restoration, failover)
- [ ] Penetration test results (findings, remediation status)
- [ ] Policy review (updated policies, acknowledged by team)
- [ ] KYC/AML effectiveness review (suspicious activity reports, escalations)

**ANNUAL EVIDENCE:**
- [ ] SOC 2 Type II audit report
- [ ] ISO 27001 certification renewal
- [ ] Business continuity plan test
- [ ] Full security architecture review
- [ ] Incident response tabletop exercise
- [ ] Regulatory compliance attestations (state licenses, FinCEN)

### 2.2 Evidence Storage & Organization

**DIRECTORY STRUCTURE:**
```
/audit_evidence/
├── 2025/
│   ├── Q1/
│   │   ├── access_reviews/
│   │   │   ├── 2025-01_user_access_review.pdf
│   │   │   ├── 2025-01_admin_access_review.pdf
│   │   │   └── 2025-01_api_key_review.pdf
│   │   ├── reconciliation/
│   │   │   ├── 2025-01-15_balance_reconciliation.xlsx
│   │   │   └── 2025-01-15_blockchain_balances.csv
│   │   ├── security/
│   │   │   ├── 2025-01_vuln_scan_report.pdf
│   │   │   ├── 2025-01_failed_logins.csv
│   │   │   └── 2025-01_security_incidents.pdf
│   │   ├── change_management/
│   │   │   ├── 2025-01_deployments.csv
│   │   │   ├── 2025-01_config_changes.json
│   │   │   └── 2025-01_rollback_log.txt
│   │   └── compliance/
│   │       ├── 2025-01_sar_filings.pdf (Suspicious Activity Reports)
│   │       └── 2025-01_kyc_metrics.xlsx
│   ├── Q2/ ...
│   └── annual/
│       ├── 2025_SOC2_Type_II_Report.pdf
│       ├── 2025_ISO27001_Certificate.pdf
│       └── 2025_Penetration_Test_Full_Report.pdf
```

**METADATA REQUIREMENTS:**
- **File naming:** `YYYY-MM-DD_description_version.ext`
- **Retention tag:** "retain_until: 2032-01-15" (7 years from creation)
- **Classification:** "Confidential - Audit Evidence"
- **Owner:** Role (not individual name, e.g., "Compliance Officer")
- **Approval:** Digital signature or audit trail of generation

**STORAGE LOCATION:**
- Primary: Encrypted S3 bucket with versioning enabled
- Backup: Separate AWS region (cross-region replication)
- Integrity: S3 Object Lock in Compliance mode (prevent deletion)
- Access: IAM roles for compliance/audit team only

### 2.3 Evidence Automation

**AUTOMATED REPORT GENERATION:**

```typescript
// Example: Daily backup verification report
async function generateBackupReport() {
  const backups = await getLastBackups(24); // 24 hours
  const verified = await verifyBackupIntegrity(backups);
  
  const report = {
    date: new Date().toISOString().split('T')[0],
    total_backups: backups.length,
    successful: verified.filter(v => v.status === 'ok').length,
    failed: verified.filter(v => v.status === 'failed'),
    size_gb: verified.reduce((sum, v) => sum + v.size_gb, 0),
    attestation: await signReport(report) // Digital signature
  };
  
  await uploadToS3('audit_evidence', `${report.date}_backup_report.json`, report);
  if (report.failed.length > 0) {
    await alertCompliance('Backup failures detected', report.failed);
  }
}

// Schedule: Every day at 02:00 UTC
cron.schedule('0 2 * * *', generateBackupReport);
```

**CONTINUOUS EVIDENCE COLLECTION:**
- Git commits (all code changes with author, reviewer, timestamp)
- Deployment logs (what, when, who, rollback capability)
- Configuration changes (versioned, with approval trail)
- Database schema changes (migrations with review)

---

## 3. Access Review Processes

### 3.1 User Access Review (Monthly)

**OBJECTIVE:** Ensure all users have appropriate access levels, remove orphaned accounts, detect privilege creep

**PROCEDURE:**

**STEP 1: Generate User Access Report**
```sql
-- All users with roles and last login
SELECT 
  u.user_id,
  u.email,
  u.kyc_tier,
  u.created_at,
  u.last_login,
  u.status,
  ARRAY_AGG(r.role_name) AS roles,
  CASE WHEN u.last_login < NOW() - INTERVAL '90 days' THEN true ELSE false END AS inactive
FROM users u
LEFT JOIN user_roles ur ON u.user_id = ur.user_id
LEFT JOIN roles r ON ur.role_id = r.role_id
GROUP BY u.user_id
ORDER BY u.last_login DESC NULLS LAST;
```

**STEP 2: Review Criteria**
- [ ] **Inactive accounts (>90 days):** Flag for suspension or deletion (after 180 days)
- [ ] **Orphaned accounts:** No recent activity, no balance, no pending orders → Delete after notification
- [ ] **Excessive permissions:** Users with admin roles but no admin activity → Downgrade
- [ ] **New accounts:** Verify KYC compliance, appropriate tier assignment
- [ ] **Suspicious accounts:** Multiple failed logins, velocity violations → Security review

**STEP 3: Approval**
- Compliance Officer reviews report
- Security team investigates flagged accounts
- Approved changes applied via admin panel (logged)
- Users notified of account status changes (email)

**STEP 4: Documentation**
- Save report: `/audit_evidence/{year}/Q{quarter}/access_reviews/{YYYY-MM}_user_access_review.pdf`
- Include: Total users, new users, deactivated users, issues found, actions taken
- Sign-off: Compliance Officer digital signature

### 3.2 Administrator Access Review (Quarterly)

**OBJECTIVE:** Ensure admin accounts have minimum necessary privileges, no excessive access

**PROCEDURE:**

**STEP 1: Admin Role Inventory**
```sql
SELECT 
  a.admin_id,
  a.email,
  a.role,
  a.created_at,
  a.last_login,
  COUNT(DISTINCT aa.action_type) AS action_types_used,
  MAX(aa.timestamp) AS last_action
FROM admins a
LEFT JOIN admin_actions aa ON a.admin_id = aa.admin_id AND aa.timestamp > NOW() - INTERVAL '90 days'
GROUP BY a.admin_id
ORDER BY a.role, a.last_login DESC;
```

**STEP 2: Review Criteria**
- [ ] **Super admins:** Verify business justification for full access (should be <3 accounts)
- [ ] **Inactive admins (>60 days):** Disable account, require re-activation if needed
- [ ] **Role appropriateness:** Customer support shouldn't have withdrawal approval access
- [ ] **Separation of duties:** Same admin shouldn't approve their own changes
- [ ] **Least privilege:** If admin only uses 1-2 permissions, create narrower role

**STEP 3: Approval & Actions**
- CISO reviews and approves all admin access
- Adjust roles/permissions as needed
- Document rationale for any super admin accounts
- Revoke MFA and reset passwords for inactive accounts

**STEP 4: Documentation**
- `/audit_evidence/{year}/Q{quarter}/access_reviews/{YYYY-QQ}_admin_access_review.pdf`
- List all admins, roles, activity summary, changes made

### 3.3 API Key Review (Monthly)

**OBJECTIVE:** Ensure API keys are active, properly permissioned, and securely used

**PROCEDURE:**

**STEP 1: API Key Report**
```sql
SELECT 
  ak.api_key_id,
  ak.user_id,
  u.email,
  ak.permissions,
  ak.ip_whitelist,
  ak.created_at,
  ak.last_used,
  COUNT(al.request_id) AS requests_last_30d
FROM api_keys ak
JOIN users u ON ak.user_id = u.user_id
LEFT JOIN api_logs al ON ak.api_key_id = al.api_key_id AND al.timestamp > NOW() - INTERVAL '30 days'
WHERE ak.status = 'active'
GROUP BY ak.api_key_id, u.email
ORDER BY ak.last_used DESC NULLS LAST;
```

**STEP 2: Review Criteria**
- [ ] **Unused keys (>180 days):** Auto-revoke per policy
- [ ] **Overly permissive:** Keys with `transfer` permission but never used for withdrawals → Downgrade to `trade`
- [ ] **No IP whitelist:** High-privilege keys should have IP restrictions
- [ ] **High request volume:** Verify legitimate use, not abuse
- [ ] **Suspicious patterns:** Same key from different IPs in short time → Investigate compromise

**STEP 3: Actions**
- Auto-revoke keys >180 days unused (notify user 30 days prior)
- Email users with recommendations for tighter permissions
- Investigate flagged keys for potential compromise

**STEP 4: Documentation**
- `/audit_evidence/{year}/Q{quarter}/access_reviews/{YYYY-MM}_api_key_review.pdf`

### 3.4 Database Access Review (Quarterly)

**OBJECTIVE:** Ensure database users/roles have minimum necessary grants

**PROCEDURE:**

**STEP 1: List All Database Roles**
```sql
SELECT 
  rolname,
  rolsuper,
  rolcreatedb,
  rolcreaterole,
  rolcanlogin,
  (SELECT array_agg(datname) FROM pg_database WHERE datname IN (
    SELECT datname FROM pg_database d
    WHERE has_database_privilege(r.oid, d.oid, 'CONNECT')
  )) AS accessible_databases
FROM pg_roles r
WHERE rolname NOT LIKE 'pg_%' AND rolname != 'postgres'
ORDER BY rolname;
```

**STEP 2: Check Table-Level Grants**
```sql
SELECT 
  grantee,
  table_schema,
  table_name,
  string_agg(privilege_type, ', ') AS privileges
FROM information_schema.table_privileges
WHERE grantee NOT IN ('postgres', 'PUBLIC')
GROUP BY grantee, table_schema, table_name
ORDER BY grantee, table_schema, table_name;
```

**STEP 3: Review Criteria**
- [ ] **No superuser roles** except `postgres` (emergency only)
- [ ] **Service accounts:** Verify each service has correct schema access only
- [ ] **Least privilege:** No unnecessary GRANT ALL, DELETE, or TRUNCATE
- [ ] **Ledger service:** Only account that can write to `balances` table
- [ ] **Human accounts:** None should exist (use bastion + temporary roles)

**STEP 4: Remediation**
- Revoke excessive grants
- Create new roles if existing ones are too broad
- Update service configurations with new credentials
- Document exceptions (with CISO approval)

---

## 4. Compliance Artifacts

### 4.1 SOC 2 Type II Evidence

**REQUIRED ARTIFACTS:**

| Control Domain | Evidence | Frequency | Location |
|----------------|----------|-----------|----------|
| **Security** | Access control policy | Annual update | `/policies/access_control.pdf` |
| **Security** | User access reviews | Monthly | `/audit_evidence/{year}/Q{q}/access_reviews/` |
| **Security** | Vulnerability scan reports | Weekly | `/audit_evidence/{year}/Q{q}/security/` |
| **Security** | Penetration test results | Quarterly | `/audit_evidence/{year}/Q{q}/security/` |
| **Security** | Incident response plan | Annual update | `/policies/incident_response.pdf` |
| **Security** | Incident log & resolutions | Continuous | Audit logs + `/audit_evidence/{year}/Q{q}/security/` |
| **Availability** | Uptime reports | Daily | `/audit_evidence/{year}/Q{q}/uptime/` |
| **Availability** | Backup & recovery tests | Weekly backups, quarterly restores | `/audit_evidence/{year}/Q{q}/backups/` |
| **Availability** | Disaster recovery plan | Annual update + test | `/policies/dr_plan.pdf` |
| **Confidentiality** | Encryption at rest/transit evidence | Quarterly verification | `/audit_evidence/{year}/Q{q}/encryption/` |
| **Confidentiality** | Data classification policy | Annual update | `/policies/data_classification.pdf` |
| **Confidentiality** | Secret management audit | Quarterly | `/audit_evidence/{year}/Q{q}/secrets/` |
| **Processing Integrity** | Code review records | Per deployment | Git commits + PR approvals |
| **Processing Integrity** | Change management log | Continuous | `/audit_evidence/{year}/Q{q}/change_management/` |
| **Processing Integrity** | Reconciliation reports | Monthly | `/audit_evidence/{year}/Q{q}/reconciliation/` |

### 4.2 FinCEN / AML Compliance

**REQUIRED RECORDS:**

| Requirement | Evidence | Retention | Regulator Access |
|-------------|----------|-----------|------------------|
| **KYC Records** | Government ID, proof of address, selfie | 7 years post-closure | On request |
| **Transaction Monitoring** | Suspicious activity detection logs | 7 years | On request |
| **SARs Filed** | Suspicious Activity Reports + supporting docs | 7 years | Automatically reported |
| **CTRs Filed** | Currency Transaction Reports (>$10k) | 7 years | Automatically reported |
| **Travel Rule Compliance** | Beneficiary info for transfers >$3k | 7 years | On request |
| **Sanctions Screening** | OFAC check logs for all users & transactions | 7 years | On request |
| **AML Program Documentation** | Risk assessment, policies, training records | 7 years | On request |

**SUSPICIOUS ACTIVITY INDICATORS (auto-flag for review):**
- Structuring: Multiple deposits/withdrawals just below reporting thresholds
- Rapid movement: Deposit → immediate withdrawal to external address
- High-risk jurisdictions: Activity from sanctioned countries (blocked at network level)
- Unusual patterns: Dormant account suddenly active with large transactions
- Round numbers: Transactions in suspicious round amounts (e.g., exactly $10,000)
- Smurfing: Multiple small deposits from different sources to same account

**SAR FILING PROCEDURE:**
1. Automated system flags suspicious activity → case created
2. Compliance analyst reviews case → gathers additional evidence
3. If suspicious: Prepare SAR (FinCEN Form 111) within 30 days of detection
4. File electronically via FinCEN BSA E-Filing System
5. Maintain all supporting documentation (transaction logs, user profile, analysis notes)
6. **DO NOT** notify user (Tipping Off is illegal)

### 4.3 Data Privacy Compliance (GDPR / CCPA)

**DATA SUBJECT RIGHTS (must fulfill within regulatory timelines):**

| Right | Timeline | Process | Evidence |
|-------|----------|---------|----------|
| **Right to Access** (GDPR Art. 15) | 30 days | Export all user data (personal info, transactions, logs) | Export logs, delivery confirmation |
| **Right to Rectification** (GDPR Art. 16) | 30 days | Update incorrect data upon verification | Audit log of changes |
| **Right to Erasure** ("Right to be Forgotten") | 30 days | Delete non-essential data (retain financial for 7 years per FinCEN) | Deletion log, retention notice |
| **Right to Portability** (GDPR Art. 20) | 30 days | Provide data in machine-readable format (JSON/CSV) | Export + delivery log |
| **Right to Object** (GDPR Art. 21) | Immediate | Stop non-essential processing (e.g., marketing) | Preference update log |
| **Right to Restrict Processing** (GDPR Art. 18) | Immediate | Flag account, process only essential operations | Status update log |

**DATA BREACH NOTIFICATION:**
- **Internal detection → Compliance team:** Immediate (within 1 hour)
- **Compliance assessment:** Within 24 hours (is it reportable?)
- **Supervisory authority (GDPR):** Within 72 hours of detection (if high risk to rights)
- **Affected individuals:** Without undue delay (if high risk)
- **Documentation:** Breach details, impact, containment, remediation → retain 7 years

---

## 5. Data Retention Policies

### 5.1 Retention Matrix

| Data Type | Regulatory Requirement | Animica Policy | Destruction Method |
|-----------|------------------------|----------------|-------------------|
| **KYC Documents** | 7 years (FinCEN) | 7 years post-closure | Cryptographic key deletion → secure delete |
| **Financial Transactions** | 5-7 years (varies) | 7 years | Cryptographic key deletion → secure delete |
| **Audit Logs** | Varies (3-7 years) | 7 years | Cryptographic key deletion → secure delete |
| **User Account Data** | N/A (GDPR allows deletion) | Active + 90 days post-closure (unless financial activity) | GDPR erasure (except mandated retention) |
| **Marketing Consent** | Until withdrawn | Until withdrawn or inactivity >2 years | Immediate deletion on request |
| **Session Data** | N/A | 90 days (audit), 24 hours (operational) | Automated TTL expiration |
| **API Logs** | N/A | 90 days | Automated deletion |
| **System Logs** | N/A | 30 days | Automated deletion |
| **Backup Tapes/Snapshots** | N/A | 30 days (hot), 1 year (archival) | Cryptographic erasure → physical destruction |

### 5.2 Data Destruction Procedure

**STEP 1: Identify Data for Destruction**
- Automated job runs monthly to identify expired data
- Query: `SELECT * FROM retention_schedule WHERE destroy_date < NOW()`

**STEP 2: Legal Hold Check**
- Verify no active legal holds, litigation, or regulatory investigations
- Compliance Officer approval required

**STEP 3: Cryptographic Erasure (Preferred Method)**
- Destroy encryption keys used for data-at-rest encryption
- Render data unreadable without key recovery (which is also destroyed)
- Document: Key ID, destruction timestamp, method

**STEP 4: Physical Deletion (If No Crypto-Shredding)**
- For unencrypted or semi-structured data:
  - PostgreSQL: `DELETE FROM table WHERE ...` + `VACUUM FULL`
  - S3: Delete object versions + delete markers
  - Backups: Overwrite with random data 3x (DoD 5220.22-M standard)

**STEP 5: Verification**
- Attempt to retrieve destroyed data (should fail)
- Generate certificate of destruction
- Log destruction event in audit log

**STEP 6: Documentation**
- Create destruction certificate with:
  - Data type, volume, retention reason, legal basis
  - Destruction date, method, approver
  - Verification results
- Store in `/audit_evidence/{year}/data_destruction/`

---

## 6. Record-Keeping for Regulatory Compliance

### 6.1 FinCEN Recordkeeping Requirements

**CUSTOMER IDENTIFICATION PROGRAM (CIP) - 31 CFR 1022.100:**

Must collect and retain:
- Full legal name
- Date of birth
- Physical address (no P.O. boxes)
- Government ID number (SSN, passport, etc.)
- Copy of identification document

**Verification:**
- Document verification (ID is authentic, not expired)
- Non-documentary verification (public records, third-party services)
- Retain records of verification methods used

**FUNDS TRANSFER RECORDKEEPING - 31 CFR 1010.410:**

For transfers >=$3,000:
- Name and address of sender
- Name and address of recipient
- Amount and date of transfer
- Payment instructions
- Identity of sender's financial institution
- Identity of recipient's financial institution

**TRANSACTION REPORTING:**
- **CTR (Currency Transaction Report):** >$10,000 in 24 hours (aggregated)
- **SAR (Suspicious Activity Report):** Any suspicious activity >=$5,000 (or unknown amount)
- **FBAR:** Foreign bank accounts >$10,000 (if applicable)

### 6.2 State Money Transmitter Requirements

**LICENSE DOCUMENTATION:**
- Copy of active licenses for each state (vary by state)
- Proof of surety bond or net worth (typically $100k-$1M)
- Annual financial audits (CPA-prepared balance sheets)
- Transaction volume reports (quarterly or annual)

**PERMISSIBLE INVESTMENTS:**
- Some states regulate where customer funds can be held
- Documentation of compliant asset allocation
- Quarterly attestation of compliance

**AGENT AGREEMENTS:**
- If using third-party agents (not applicable for direct-to-consumer CEX)
- Agent vetting records, contracts, monitoring logs

### 6.3 Tax Reporting (IRS)

**FORM 1099-B (Proceeds from Broker Transactions):**
- Required if exchange acts as broker (facilitates trades)
- Report annually for each US customer
- Include: Cost basis, proceeds, wash sales

**FORM 1099-MISC:**
- If paying >$600 to contractors, service providers

**FORM 1099-K (Payment Card and Third Party Network Transactions):**
- If facilitating >$20,000 AND >200 transactions per user per year
- (Threshold may change; verify current IRS rules)

**FOREIGN ACCOUNT TAX COMPLIANCE ACT (FATCA):**
- Report foreign accounts if US-facing exchange
- Form 8938 for specified foreign financial assets

**RECORD RETENTION:** 7 years for all tax-related documents

---

## 7. Audit Preparation Checklist

### 7.1 Pre-Audit Preparation (4-6 Weeks Before)

**WEEK 1-2: Evidence Gathering**
- [ ] Generate all monthly/quarterly reports for audit period
- [ ] Organize evidence in standardized directory structure
- [ ] Verify all required policies are up-to-date and approved
- [ ] Create audit evidence index (master list of all evidence files)
- [ ] Review prior audit findings and remediation status

**WEEK 3-4: Internal Review**
- [ ] Conduct mock audit with internal team
- [ ] Identify gaps in evidence or processes
- [ ] Remediate any critical issues before auditor arrival
- [ ] Prepare narratives/documentation for complex controls
- [ ] Schedule interviews with key personnel (admins, engineers, support)

**WEEK 5-6: Final Preparation**
- [ ] Set up secure auditor access (read-only, time-limited)
- [ ] Prepare list of system endpoints for auditor testing
- [ ] Coordinate with IT to provision test accounts if needed
- [ ] Brief team on audit process and expectations
- [ ] Finalize audit schedule and room/logistics

### 7.2 During Audit

**DAILY STAND-UPS:**
- Review previous day's findings
- Prioritize auditor requests
- Address any questions or blockers

**EVIDENCE REQUESTS:**
- Maintain log of all evidence provided to auditor
- Track: Request date, description, file provided, auditor name
- Use ticketing system (Jira) for traceability

**ISSUE MANAGEMENT:**
- Document all findings as raised
- Clarify severity (critical, high, medium, low)
- Begin remediation planning immediately for critical/high issues

**POINT OF CONTACT:**
- Designate single POC (Compliance Officer) for auditor communication
- Avoid ad-hoc responses from team; route through POC

### 7.3 Post-Audit

**REPORT REVIEW:**
- Receive draft audit report (typically 2-4 weeks post-audit)
- Review for factual accuracy
- Provide management responses to findings (include remediation plan, timeline, owner)

**REMEDIATION:**
- Create remediation project plan (Jira epics/stories)
- Assign owners and deadlines for each finding
- Track progress weekly in management review meeting
- Provide evidence of remediation to auditor for final report

**FINAL REPORT:**
- Receive final audit report (4-6 weeks post-audit)
- Distribute to stakeholders (board, investors, regulators if required)
- Store in `/audit_evidence/{year}/audit_reports/`

**CONTINUOUS IMPROVEMENT:**
- Conduct post-audit retrospective
- Update processes based on lessons learned
- Implement additional automation to reduce manual evidence collection
- Schedule next audit (typically annual for SOC 2)

---

## 8. Automation & Tools

### 8.1 Automated Evidence Collection

**TOOLS:**
- **Vanta / Drata / Secureframe:** SOC 2 compliance automation (integrates with AWS, GitHub, HR systems)
- **AWS Config / Azure Policy:** Infrastructure compliance monitoring
- **Custom Scripts:** Evidence generation (see Section 2.3)

**INTEGRATIONS:**
- AWS CloudTrail → S3 → Athena (query infrastructure logs)
- GitHub → Compliance dashboard (track code reviews, approvals)
- Jira → Compliance dashboard (track incidents, change tickets)
- Datadog/New Relic → Evidence bucket (export uptime/performance data)

### 8.2 Continuous Compliance Monitoring

**REAL-TIME DASHBOARDS:**
- Compliance score (% of controls passing automated tests)
- Open audit findings (count by severity, aging)
- Upcoming audits (timeline, preparation status)
- Evidence collection status (daily/weekly/monthly evidence generated?)

**ALERTING:**
- Failed backup: Immediate alert to on-call + compliance
- Certificate expiring <7 days: Alert to DevOps + compliance
- Audit log integrity failure: Critical alert to CISO + compliance
- High-privilege access without justification: Alert to security team

---

## 9. Regulatory Interaction Guidelines

### 9.1 Regulatory Requests

**TYPES OF REQUESTS:**
- Examination (on-site or remote review of operations)
- Information request (specific documents or data)
- Subpoena (legal requirement for records)
- Informal inquiry (clarification on policies or procedures)

**RESPONSE PROCEDURE:**

**STEP 1: Receive Request**
- All regulatory correspondence must go through Compliance Officer
- Log in regulatory request tracker: Date, regulator, type, scope, deadline

**STEP 2: Internal Review**
- Compliance Officer reviews request with Legal Counsel
- Identify responsive documents/data
- Check for any privileged or sensitive information

**STEP 3: Data Collection**
- Gather responsive records from audit logs, databases, etc.
- Redact PII of non-relevant users (unless specifically requested)
- Organize in clear, indexed format

**STEP 4: Legal Review**
- Legal Counsel reviews all materials before submission
- Ensure no inadvertent waiver of privilege
- Verify completeness of response

**STEP 5: Submission**
- Submit via required method (portal, email, mail)
- Retain copy of all submissions
- Document: Submission date, materials provided, recipient

**STEP 6: Follow-Up**
- Log any follow-up questions or requests
- Maintain open communication with regulator
- Update internal processes if request reveals gaps

### 9.2 Examination Preparation

**TYPES OF EXAMS:**
- **Scheduled:** 30-90 days notice (standard)
- **Unannounced:** No notice (rare, typically for cause)

**PREPARATION (Scheduled Exam):**
- Designate examination coordinator (Compliance Officer)
- Set up secure data room (physical or virtual)
- Prepare executive briefing for examiners (company overview, key personnel, systems)
- Notify relevant team members (expect interviews)
- Review prior examination reports (address old findings)

**DURING EXAMINATION:**
- Daily meetings with examiners (discuss progress, requests)
- Track all document requests (similar to audit evidence log)
- Provide workspace and access as needed
- Be responsive but concise (don't over-volunteer information)

**POST-EXAMINATION:**
- Exit meeting with preliminary findings
- Written report (typically 30-60 days later)
- Respond to findings with corrective action plan
- Implement remediations and provide status updates

---

## 10. Continuous Improvement

### 10.1 Metrics & KPIs

**AUDIT READINESS METRICS:**
- Evidence collection completeness: 100% target (all daily/weekly/monthly/quarterly evidence generated on time)
- Time to produce evidence: <24 hours for ad-hoc requests (target)
- Audit findings per audit: Track trend (decreasing is good)
- Remediation time: <90 days for HIGH, <180 days for MEDIUM
- Automation coverage: % of evidence auto-generated (target: >80%)

**COMPLIANCE HEALTH METRICS:**
- User access review timeliness: 100% on schedule
- Orphaned accounts: 0 (all inactive accounts reviewed)
- Secret rotation compliance: 100% (no expired secrets)
- Security training completion: 100% within 30 days of hire
- Policy acknowledgment: 100% of employees annually

### 10.2 Annual Compliance Review

**SCHEDULE:** Q4 of each year (prepare for next year's audits)

**AGENDA:**
- Review all policies and procedures (update for regulatory changes)
- Assess new regulatory requirements (state licenses, federal rules)
- Evaluate third-party vendors (security, compliance certifications)
- Update retention schedules (new data types, changed requirements)
- Plan audit schedule (SOC 2, penetration tests, internal audits)
- Budget for compliance initiatives (tools, training, consulting)

**OUTPUT:**
- Updated compliance roadmap
- Board-level compliance report
- Next year's audit calendar
- Training plan for new regulations

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **SAR** | Suspicious Activity Report - FinCEN form for reporting suspicious transactions |
| **CTR** | Currency Transaction Report - FinCEN form for transactions >$10,000 |
| **KYC** | Know Your Customer - identity verification processes |
| **AML** | Anti-Money Laundering - compliance program to detect/prevent money laundering |
| **GDPR** | General Data Protection Regulation - EU data privacy law |
| **CCPA** | California Consumer Privacy Act - California data privacy law |
| **SOC 2** | Service Organization Control 2 - audit of service provider controls |
| **Audit Log** | Immutable record of system events for compliance purposes |
| **Tamper-Evident** | System that shows evidence if altered (e.g., hash chaining) |
| **Write-Once Storage** | Storage that prevents modification/deletion after write (e.g., S3 Object Lock) |

---

## Appendix B: Contacts

- **Compliance Officer:** compliance@animica.exchange
- **External Auditor:** [Audit Firm Name] - partner@auditfirm.com
- **Legal Counsel:** legal@animica.exchange
- **Regulatory Liaison:** regulatory@animica.exchange

---

## Appendix C: Evidence Index Template

```csv
Evidence Type,File Name,Date Generated,Retention Until,Owner,Location,Audit Reference
User Access Review,2025-01_user_access_review.pdf,2025-01-15,2032-01-15,Compliance Officer,s3://audit-evidence/2025/Q1/access_reviews/,SOC2-AC-01
Vulnerability Scan,2025-01-20_nessus_scan.pdf,2025-01-20,2032-01-20,Security Engineer,s3://audit-evidence/2025/Q1/security/,SOC2-SEC-03
Backup Verification,2025-01-15_backup_report.json,2025-01-15,2032-01-15,DevOps Lead,s3://audit-evidence/2025/Q1/backups/,SOC2-AV-02
```

---

**Document Classification:** Internal - Compliance Sensitive  
**Distribution:** Compliance Team, Audit Team, Executive Leadership  
**Review Frequency:** Quarterly  
**Next Review:** April 2025
