# SLASHING & RECOURSE POLICY
_Accountability for AICF / Quantum compute providers and related operators_

**Version:** 1.0  
**Status:** Active  
**Scope:** Applies to any provider that delivers AI/quantum/accelerated workloads into Animica’s AICF and/or consensus-adjacent pipelines (e.g., PoIES oracles, randomness beacons, DA provers, VM accelerators). Covers organizations and their delegated operators (collectively **“Providers”**).

---

## 1) Purpose & Principles

We align Provider incentives with network safety and user trust via **measurable obligations**, **verifiable evidence**, and **predictable penalties**, with a fair path to **recourse** when issues are non-malicious.

**Principles**
- **Safety first:** Protect liveness, integrity, and user funds over Provider convenience.  
- **Evidence-driven:** Slash based on cryptographic proofs, signed telemetry, or reproducible audits.  
- **Least surprise:** Penalties and grace periods are parameterized on-chain.  
- **Right to remedy:** Good-faith faults can be cured; repeat or malicious faults escalate.

---

## 2) Definitions

- **Task**: A unit of off-chain work (AI/quantum job, DA proof, randomness contribution) with an input commitment and required output/attestation.  
- **SLA Window**: Max wall-clock (or block) budget to submit valid output.  
- **Attestation**: Provider-signed payload binding task ID, input commitments, environment hash, and output commitment.  
- **Stake**: Collateral posted to the on-chain **Provider Registry**; subject to slashing.  
- **Strike**: A recorded fault with severity (S1–S4) and metadata.

---

## 3) Misbehavior Classes (S1–S4)

| Class | Description | Examples | Default Penalty* |
|---|---|---|---|
| **S1 (Minor)** | Transient SLA breach without impact to correctness | Sporadic latency spikes; 1 missed window per epoch | Warning + **0%** slash; **Strike** recorded |
| **S2 (Moderate)** | Repeated SLA breaches or availability below threshold | <95% availability over N epochs; 3 missed windows | **1–5%** of stake; cooling-off (1 epoch) |
| **S3 (Severe)** | Incorrect results or equivocation detected | Proofs failing verification; double-signing differing outputs; tampered env hash | **10–50%** of stake; quarantine (3–7 epochs) |
| **S4 (Critical / Malicious)** | Coordinated fraud, censorship, or safety violation | Oracle collusion; withheld outputs to manipulate Γ; forged signatures | **Up to 100%** slash; **eject** + long-term ban |

\* Exact ranges are parameterized; see §10.

---

## 4) Evidence & Verification

A misbehavior report must include one or more of:
1. **Cryptographic proof** (failed verification trace; mismatched commitments).  
2. **Signed telemetry** from Provider, cross-checked with network logs.  
3. **Independent replication** (third-party reruns yielding divergent outputs).  
4. **On-chain discrepancy** (equivocation, double-attest, or non-delivery within window).

Evidence is filed via a governance issue/PR referencing txids, logs, and artifacts. For S3+ a minimal public summary is posted within **24h** (see Transparency Policy).

---

## 5) Process

1. **Intake:** Steward opens a **Misbehavior Dossier** with evidence, severity recommendation, and proposed penalty.  
2. **Notice:** Provider receives written notice (email/keybase + on-chain event).  
3. **Response Window:**  
   - S1–S2: **72h** to respond & cure.  
   - S3: **48h** to respond; potential immediate quarantine.  
   - S4: Immediate containment; post-notice allowed within **7d**.  
4. **Decision:** Stewards vote (or emergency multisig for S4) per governance rules.  
5. **Execution:** Slash/eject applied on-chain; quarantine flags set; transparency report posted.  
6. **Appeal:** Provider may appeal once; see §8.

---

## 6) Penalties & Operational Actions

- **Slashing:** Burns portion of stake or redirects to **Network Remediation Fund**.  
- **Quarantine:** Provider’s tasks paused; cannot receive assignments.  
- **Ejection:** Removal from registry; re-entry requires fresh stake and review.  
- **Make-Good:** For S2–S3, Provider may fund user compensation or contribute compute credits to remediation jobs.  
- **Rate-Limit & Weighting:** Scheduling weight reduced for strike history.

---

## 7) Recourse & Cures

- **Automatic Cure (S1):** Provide post-mortem and pass two clean epochs → strike expires in 30 days.  
- **Conditional Cure (S2):** Submit RCA + corrective actions; complete **K** canary tasks with perfect attestation → slash reduced by up to 50% (policy key `gov.providers.s2.cure_reduction_max`).  
- **Probation (S3):** After quarantine, run under heightened telemetry; two random audits required. Successful probation upgrades strike to S2 and unlocks partial re-stake.  
- **No Cure (S4):** Only appealable on evidentiary or procedural grounds.

---

## 8) Appeals

- **Grounds:** New exculpatory evidence, verification error, or procedural defect.  
- **Panel:** Separate steward quorum not involved in initial decision.  
- **Deadline:** File within **14 days** of decision.  
- **Outcomes:** Uphold, reduce severity/penalty, or expunge. All outcomes published.

---

## 9) Safeguards & Vendor Neutrality

- **Diversity Caps:** No single Provider > X% of scheduled tasks (key `gov.providers.max_share_percent`).  
- **Transparent Bidding:** Task pricing and selection rules documented; logs retained.  
- **Attestation Standard:** Common schema including environment hash (hardware model, firmware, microcode, driver versions), RNG seed provenance, and signed outputs.  
- **Privacy:** Proprietary model weights/configs may be hashed; unredacted materials stored under steward-confidential appendix when necessary.

---

## 10) On-Chain Parameters (governance/registries)

Suggested keys (bounded in `params_bounds.json`; current in `params_current.json`):

gov.providers.sla.min_availability_percent # e.g., 95.0
gov.providers.sla.max_window_blocks # task time budget
gov.providers.slash.s2_min_percent # 1.0
gov.providers.slash.s2_max_percent # 5.0
gov.providers.slash.s3_min_percent # 10.0
gov.providers.slash.s3_max_percent # 50.0
gov.providers.slash.s4_max_percent # 100.0
gov.providers.max_share_percent # e.g., 33.0
gov.providers.quarantine.epochs_s3 # e.g., 7
gov.providers.probation.epochs # e.g., 14
gov.providers.cure.canary_tasks_k # e.g., 50
gov.providers.s2.cure_reduction_max # e.g., 50.0

yaml
Copy code

Changes require a **param change** proposal and must validate within bounds.

---

## 11) Incident Tiers & Communication

- **Sev-1 (S4 or widespread S3):** Immediate banner/status page; hourly updates until contained; full postmortem in 7 days.  
- **Sev-2 (S3 localized / repeated S2):** Daily updates during incident; postmortem in 7 days.  
- **Sev-3 (S2 spikes):** Weekly roll-up with metrics and actions.

All communications link to the dossier, affected versions, and mitigation status.

---

## 12) Examples

**Example A — Missed Windows (S2):** Provider misses 4/100 tasks; availability 92%. Slash 2%, cooling-off 1 epoch; probation required for cure.  
**Example B — Incorrect Proof (S3):** Output fails deterministic verifier; slash 25%, quarantine 5 epochs; probation & audits.  
**Example C — Equivocation (S4):** Double-signed conflicting outputs to two schedulers; slash 100%, eject, ban 12 months.

---

## 13) Tooling & Audits

- **Schedulers** embed task IDs, deadlines, and commitment roots; store minimal logs on-chain or DA.  
- **Auditors** can re-execute tasks deterministically in simulated environments; artifacts archived with checksums.  
- **CLI hooks:** Providers must support standardized `attest.json` outputs and signed envelopes.

---

## 14) Relationship to Other Policies

- Works with **TRANSPARENCY.md** for publication, **DEPOSIT_AND_REFUNDS.md** for economic recycling of slashed funds, and **PQ_POLICY.md** when cryptographic rotations are implicated in misbehavior.

---

## 15) Change Log

- **1.0 (2025-10-31):** Initial slashing classes, evidence rubric, recourse/appeals, on-chain parameterization.

