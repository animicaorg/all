# Security Council · Emergency Powers, Time-Bounds & Quorum

> This document defines the **scope**, **authority**, **quorum and voting rules**, and **time limits** for the Security Council (SC).  
> It complements: `governance/GOVERNANCE.md`, `governance/ROLES.md`, `docs/security/THREAT_MODEL.md`, and `docs/ops/RUNBOOKS.md`.

---

## 1) Purpose & Scope

The Security Council is an on-call, multi-party body empowered to take **rapid, minimally invasive, reversible** actions to protect network safety when an incident threatens:
- **Consensus integrity** (e.g., chain split, invalid block acceptance, Θ/retarget malfunction).
- **Critical security of funds or state** (e.g., exploit enabling arbitrary balance mutation).
- **Key infrastructure** (e.g., PQ key compromise, DA availability bug, P2P abuse leading to global stall).
- **Ecosystem safety** (e.g., malicious update pipeline, signing key leakage, widespread wallet exploit).

The SC **does not** decide policy or long-term upgrades; it stabilizes the system until regular governance can act.

---

## 2) Composition & Eligibility

- **Size (N):** 7 members.  
- **Diversity:** At least 3 independent organizations; no single org may hold a majority of seats.  
- **Eligibility:** Demonstrated security expertise (protocol/appsec/infra), 24×7 on-call capability, conflict-of-interest disclosures.
- **Term:** 12 months, staggered; renewable by governance per `GOVERNANCE.md`.
- **Recusal:** Members must recuse when materially conflicted (employment, financial stake, incident involvement).

---

## 3) Keys & Operational Security

- **Control:** Actions require **5-of-7** threshold signatures via hardware tokens/HSM-backed multisig (or equivalent).
- **Separation of duties:** At least one SecOps and one Core-Protocol maintainer must be part of any signing set.
- **Audit trail:** All emergency actions are logged to an append-only ledger (hash-chained) with timestamps and signers’ public keys.
- **Rotation:** Keys rotated at least annually and upon any suspicion of compromise (see `docs/security/SUPPLY_CHAIN.md`).

---

## 4) Quorum, Voting & Decision Rules

- **Quorum:** ≥ **5** members active in the decision (live, verifiable) constitute quorum.
- **Standard approval:** **Supermajority (≥5/7)** required to authorize any emergency action.
- **Expedited path (life-threatening, minutes matter):**
  - **4/7** may enact **Expedited Containment** limited to **6 hours** maximum, only if two conditions hold:
    1) A documented, reproducible critical impact (or active exploitation) is present, **and**
    2) A good-faith attempt to reach 5th signer failed within **30 minutes**.
  - Expedited measures auto-expire unless upgraded to full approval (≥5/7) within the 6-hour window.

---

## 5) Emergency Powers (Allowable Actions)

All actions must be **minimally sufficient**, **reversible**, and **time-bounded**. Examples:

1. **Rate & Surface Controls**
   - Raise/activate **mempool admission floors**, surge limits, or disable specific high-risk tx kinds.
   - Temporarily limit **RPC methods** (e.g., blob pinning, high-fanout endpoints) and tighten rate limits.
   - Enable **P2P safe-mode**: stricter gossip validators, peer allowlists, seed rotation, throttled fanout.

2. **Protocol Safeguards (Flag-gated)**
   - Toggle pre-built **safe-mode feature flags** (e.g., disable non-essential proof kinds, cap Γ for a class).
   - Activate **height-gated bypasses** for known-bad inputs (with allow-listed exceptions for remediation).

3. **Distribution Pipeline Controls**
   - Halt auto-updates for wallet/desktop artifacts; revoke compromised feeds; pin known-good versions.

4. **Key & Registry Hygiene**
   - Revoke/rotate **compromised keys** (seeds, update signing, PQ policy roots) where reversible and scoped.

5. **Information Controls**
   - Coordinate responsible disclosure windows with affected parties; issue notices to operators and users.

**Prohibited without broader governance:**
- Permanent protocol changes, slashing/confiscation, altering user balances, censorship beyond what is strictly necessary to contain the incident, or any irreversible state surgery.

---

## 6) Time-Bounds & Ratification

- **Default maximum window:** Any emergency action **auto-expires after 72 hours** unless ratified.
- **Pause/Degrade caps:** Hard **24-hour** cap for network-impacting degradations (e.g., block production throttles); beyond that requires public ratification.
- **Ratification path:**  
  1) **Maintainers’ Council** confirmation (simple majority of module owners) within **48 hours**, and  
  2) **Community governance** ratification (expedited vote or pre-approved rubric) within **7 days**.  
  If either fails, emergency measures must be **rolled back immediately**.

- **Extensions:** One additional **72-hour** extension permissible with fresh evidence and renewed **≥5/7** approval.

---

## 7) Activation, Communication & Transparency

**Activation checklist (condensed):**
1. Open incident channel; assign **Incident Commander (IC)** (not necessarily an SC signer).  
2. Draft **Initial Advisory** (TLP:AMBER/RED as appropriate) with impact, scope, provisional mitigations.  
3. Record decision: evidence, options considered, chosen controls, expected blast radius, rollback plan.  
4. Obtain signatures per quorum rules; deploy *least-privilege* measures; verify effect.  
5. Publish **Public Notice** (TLP:GREEN) ASAP when safe: summary, user/operator guidance, tracking ID.  
6. Start **Post-Incident Review (PIR)** template.

**Transparency:**
- Public timeline and PIR within **7 days** of stabilization, including diffs to config/flags, hashes of artifacts, and lessons learned.
- Sensitive details (0-days, keys) redacted until safe.

---

## 8) Guardrails & Accountability

- **Least privilege:** Prefer configuration toggles and admission filters over code hotfixes; prefer reversible gates over binaries.
- **Observability:** All toggles emit metrics/logs (who, when, what, commit/height/params).
- **Dual control:** Any step affecting consensus paths requires a second independent validation (separate org).
- **Appeals:** Stakeholders may appeal to the Governance Facilitators; the appeal is recorded alongside the PIR.

---

## 9) On-Call & Drills

- **Roster:** 24×7 rotation with primary + secondary; contact runbook kept encrypted, tested monthly.
- **Drills:** At least **quarterly** simulation of activation, including signing and rollback.
- **SLAs:** Acknowledge within **15 minutes**; containment within **2 hours** for Sev-1 when feasible.

---

## 10) Amendments

This charter may be amended via the process in `governance/GOVERNANCE.md`. Material changes (quorum, powers, time-bounds) require:
- Public RFC (≥7 days),  
- Maintainers’ Council approval, **and**  
- Community governance vote.

---

## 11) Quick Reference (Cheat-Sheet)

- **Quorum:** 5/7.  
- **Approval:** ≥5/7 (Expedited: 4/7 for ≤6h).  
- **Auto-expire:** 72h (24h max for heavy degradations).  
- **Extension:** One extra 72h with renewed ≥5/7.  
- **Ratify:** Maintainers (≤48h) + Community (≤7d).  
- **Principles:** Minimize, Reversible, Auditable, Time-boxed, Transparent.

---

**Version:** v1.0 — Adopted by <date>; next formal review in 6 months or after any Sev-1 incident.
