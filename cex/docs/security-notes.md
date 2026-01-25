# Security Baseline

- **Never log secrets** (API keys, private keys, BitGo tokens).
- API keys must be hashed (e.g., Argon2/bcrypt) before storage.
- All withdrawals require approvals (risk + admin), even if automation is added later.
- Principle of least privilege per service DB user (separate DB roles with minimal grants).
- Audit logs are immutable append-only (journal entries and admin actions).
- Rate-limit all external-facing APIs (Redis-backed token buckets).
- Enforce TLS termination and strict headers at the edge (later in gateway).
