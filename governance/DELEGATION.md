# Delegation · How to Delegate Voting Power & Required Disclosures

> This document defines **how token holders and representatives delegate voting power**, what **scope** a delegation covers, how to **revoke/renew**, and the **disclosure standards** for delegates.  
> Complements: `governance/GOVERNANCE.md`, `governance/ROLES.md`, `governance/SECURITY_COUNCIL.md`, and `docs/governance/*`.

---

## 1) Purpose

Delegation enables informed, high-participation governance while respecting differing time and expertise. Holders may delegate **all or part** of their voting power to one or more delegates, **per domain** (e.g., protocol upgrades, parameters, PQ policy), with **clear disclosures and revocation**.

---

## 2) Definitions

- **Delegator** — an address that grants voting power.  
- **Delegate** — an address that receives voting power and votes on proposals.  
- **Domain** — scope of decisions (e.g., `upgrades`, `params`, `pq_policy`, `treasury`, `aicf`, `docs`, `process`).  
- **Epoch** — accounting window for snapshots of voting power (e.g., per block height or proposal start).  
- **Statement** — signed message proving intent to delegate; on-chain tx when supported, off-chain signed record otherwise.

---

## 3) Delegation Models

We support two models (both can coexist):

1) **On-chain Delegation (preferred where available)**  
   - A canonical governance contract records delegations, weights, domain filters, and expiry.
   - Voting power is snapshotted at proposal start height.

2) **Signed Statement Registry (bootstrap / off-chain index)**  
   - A signed JSON/YAML statement is submitted to the governance registry.  
   - The registry verifies signatures and publishes a Merkle root of active delegations for transparency/audit.  
   - Voting systems (Snapshot-style) consume the Merkle root + proofs.

> Notes  
> - Delegations are **non-custodial**; delegates cannot move funds.  
> - Delegations **do not** override the Security Council process; see `governance/SECURITY_COUNCIL.md`.

---

## 4) Domains & Granularity

Delegations may be **domain-scoped** and **fractional**.

**Supported domains (initial):**
- `upgrades` — feature flags, hard/soft forks, version gates.  
- `params` — economic/consensus parameters (Γ caps, Θ schedule)—see `docs/spec/CHAIN_PARAMS.md`.  
- `pq_policy` — PQ algorithm rotations and deprecations—see `docs/governance/PQ_POLICY.md`.  
- `treasury` — treasury spend, emissions, AICF share—see `docs/economics/*`.  
- `aicf` — provider policy updates (SLA, slashing)—see `docs/aicf/*`.  
- `process` — governance procedure changes.  
- `docs` — editorial acceptance for canonical docs.

You may delegate **100%** or **split** across delegates per domain (weights sum ≤ 1.0 per domain).

---

## 5) Lifecycle

### 5.1 Create → Sign → Submit
- Choose delegate(s); confirm their disclosure profile (Section 7).
- Prepare a **Delegation Statement** (JSON) and **sign** it with the delegator key.
- Submit:  
  - **On-chain:** call `delegate(address, domain, weight, expiry)`.  
  - **Registry:** POST signed statement to the governance registry (or PR to the public listing).

### 5.2 Snapshot & Voting Power
- Voting power is snapshotted **at proposal start** (`start_height`) or **block-time cutoff** (off-chain).
- Any changes after the snapshot do not affect the active proposal.

### 5.3 Renewal & Expiry
- Statements include `expiry` (block height or timestamp). Expired delegations are ignored.
- Recommended max duration: **180 days**.

### 5.4 Revocation & Override
- **Immediate revocation** on-chain: `revoke(domain[, delegate])`.
- **Registry revocation:** submit a signed **Revoke Statement** (Section 6.3).
- **Self-vote override:** A delegator may directly vote; **direct votes override** delegated power for that proposal.

---

## 6) Statement Formats

### 6.1 Delegation Statement (JSON)
```jsonc
{
  "type": "animica/delegation@v1",
  "chain_id": 1,
  "delegator": "anim1qxy...abc",
  "delegate": "anim1del...xyz",
  "domains": [
    {"name": "upgrades", "weight": 1.0},
    {"name": "params", "weight": 0.5},
    {"name": "pq_policy", "weight": 0.5}
  ],
  "nonce": 42,
  "issued_at": "2025-10-11T00:00:00Z",
  "expiry": "2026-04-09T00:00:00Z",
  "disclosures_ack": true,
  "meta": {
    "purpose": "Long-term representation",
    "contact": "mailto:holder@example.org"
  }
}

Signing domain (EIP-191-like, PQ-safe):

"Animica Governance Delegation v1 | chain:1 | nonce:42 | hash(statement_body)"

Sign with the account’s PQ signature (Dilithium3/SPHINCS+). Include sig_alg and signature alongside the statement on submission.

6.2 Composite (Multiple Delegates)

Provide multiple domains entries with different delegate statements; weights per domain must sum ≤ 1.0.

6.3 Revocation Statement

{
  "type": "animica/delegation-revoke@v1",
  "chain_id": 1,
  "delegator": "anim1qxy...abc",
  "domains": ["params","pq_policy"],     // omit to revoke all
  "delegate": "anim1del...xyz",          // optional: specific delegate
  "nonce": 43,
  "issued_at": "2025-12-01T00:00:00Z",
  "reason": "Conflict of interest",
  "sig_alg": "dilithium3",
  "signature": "0x..."
}


⸻

7) Delegate Disclosures (Required)

Delegates must publish and maintain a Disclosure Profile:
	•	Identity: legal name or persistent pseudonym; verification method (PGP/DID/site).
	•	Affiliations: employment, grants, clients, board roles.
	•	Holdings: material positions in Animica or directly affected projects (ranges OK).
	•	Compensation: any payment/consideration for governance activity (source, terms).
	•	Conflicts: relationships that could bias decisions; plan to manage recusal.
	•	Voting policy: principles, procedures for consultations, and reporting cadence.
	•	Security: key custody practices; rotation cadence (see docs/security/SUPPLY_CHAIN.md).

Updates required within 7 days of material changes. Missing/false disclosures are grounds for removal from the recommended delegate directory.

⸻

8) Ethics, Conduct & Reporting
	•	Rationale reports for significant proposals (upgrades/params/treasury): a short public note before or immediately after voting.
	•	Office hours or contact channel for delegators.
	•	Gifts & pay: disclose any value received in relation to a vote.
	•	Recusal: when conflicted, state recusal publicly.

⸻

9) Security & Abuse Resistance
	•	Domain-separated signatures; include nonce and expiry.
	•	One-hop delegation (no transitive compounding) to prevent opaque power structures.
	•	Sybil resistance via optional identity attestations for “recommended” listing.
	•	Rate limits on registry updates; Merkleized snapshots with public roots.

⸻

10) CLI Examples (illustrative)

10.1 Create & Sign a Statement (Python)

omni-sdk gov delegate \
  --chain 1 \
  --delegator anim1qxy...abc \
  --delegate anim1del...xyz \
  --domains upgrades=1.0 params=0.5 pq_policy=0.5 \
  --expiry 2026-04-09 \
  --out delegation.json

omni-sdk sign --in delegation.json --alg dilithium3 --key keystore.json --out delegation.signed.json

10.2 Revoke

omni-sdk gov revoke \
  --chain 1 \
  --delegator anim1qxy...abc \
  --domains params pq_policy \
  --out revoke.json

omni-sdk sign --in revoke.json --alg dilithium3 --key keystore.json --out revoke.signed.json


⸻

11) FAQs

Q: Can I delegate to multiple delegates for the same domain?
A: Yes, by splitting weights; the sum per domain must be ≤ 1.0.

Q: Do direct votes override delegation?
A: Yes—your explicit vote supersedes delegation for that proposal.

Q: Is pseudonymous delegation allowed?
A: Yes; however “recommended” status requires disclosures and a verified identity method (DID/website).

Q: How are snapshots chosen?
A: At proposal start_height to prevent last-minute manipulation.

⸻

12) Compliance Checklist (for Delegates)
	•	Publish & maintain disclosure profile.
	•	Sign statements with PQ keys; rotate per policy.
	•	Provide rationales for major votes.
	•	Recuse when conflicted; disclose compensation.
	•	Respond to incident/expedited consultations when contacted.

⸻

Version: v1.0 — Effective immediately. Proposed amendments follow governance/GOVERNANCE.md RFC flow.
Contact: governance@animica.example (PGP/DNSSEC/DID supported)
