# DEPOSIT & REFUNDS POLICY
_Spam resistance via proposal deposits_

**Version:** 1.0  
**Status:** Active  
**Scope:** Applies to all proposals that enter the governance queue (upgrade, param change, PQ rotation, treasury actions, process changes).

---

## 1) Purpose

Deposits deter spam while keeping governance open to good-faith participants. Deposits are **escrowed**, **refundable on valid participation**, and **slashable** for abuse.

**Goals**
- Filter low-effort or malicious submissions.
- Align incentives: proposers engage through discussion, revisions, and post-vote follow-ups.
- Keep small, legitimate contributors included via grants/waivers.

---

## 2) Terminology

- **Deposit**: ANM placed in escrow when opening a proposal PR for consideration.  
- **Valid Participation**: Proposal meets formatting & schema/bounds checks, attends triage, responds in review, and proceeds to a vote (pass or fail).  
- **Spam/Abuse**: Duplicate/near-duplicate proposals, off-topic content, known-bad binaries, or ignoring requested fixes after two review cycles.  
- **Stewards**: Governance intake multisig who triage, apply this policy, and hold escrow.

---

## 3) Amounts & Tiers

> **Base unit**: `D_base = 1,000 ANM` (subject to bounds in `params_bounds.json`)

| Tier | Proposal Type | Complexity Signals | Deposit |
|---|---|---|---|
| T0 | Editorial / Docs only | No code/param changes | **0** |
| T1 | Param change (within bounds) | Single domain (e.g., VM gas cap) | **D_base** |
| T2 | Upgrade (feature-flagged) | Contracts/VM gated rollout | **2 × D_base** |
| T3 | PQ rotation / cryptography | Key material, ecosystem impact | **3 × D_base** |
| T4 | Cross-domain / multi-activation | VM + DA + params + contracts | **4 × D_base** |

**Dynamic scaling (optional):**

\`\`\`text
D_effective = TierMultiplier × D_base × (1 + N_active/10)
# N_active = count of concurrently open proposals in "Ready for Vote"
# caps: 1.0 ≤ (1 + N_active/10) ≤ 2.0
\`\`\`

---

## 4) When the Deposit Is Due

- **Due at Intake**: Proposal enters “Intake & Triage” (see GOVERNANCE_FLOW.mmd).  
- **Proof**: Include on-chain txid or multisig receipt in the PR description under **Deposit**.
- **Window**: 7 days from steward “Intake Accepted” comment; otherwise proposal is closed (can be reopened upon deposit).

**Escrow**
- Address: **governance escrow multisig** (see `governance/ops/addresses/*.json`).  
- Funds are not spent; they are held until refund or slash decision is finalized.

---

## 5) Refunds

**Full Refund (100%)** when any of the following occur:

1. Proposal **reaches a vote** (pass or fail) and proposer complied with process (schemas/bounds OK, responded to review).  
2. Proposal is **withdrawn** by proposer **before** stewards begin formal review (no material costs incurred).  
3. Proposal is **rejected at intake** for reasons **not attributable** to the proposer (e.g., duplicate ID collision caused by registry mistake).

**Partial Refund (50%)**:

- Proposal closed during review due to **inactivity** (no response for ≥ 14 days) after at least one steward review pass.  
- Proposal requires **significant steward effort** (security review, CI shepherding) but is later withdrawn.

**No Refund (0%)** / **Slash**:

- Clear spam or trolling; recycled content with minimal changes after prior closure.  
- Submitting binaries or payloads that fail basic safety checks (malware, known-bad signatures).  
- Ignoring schema/bounds errors after **two** explicit, dated review requests.  
- Coordinated brigading or procedural abuse (e.g., ballot stuffing attempts surfaced during the PR).

**Timeline**: Refund or slash decision posted within **7 days** of proposal closure or tally publication. Execution on-chain within **5 days** after decision.

---

## 6) Waivers, Grants, & Community Support

We maintain inclusivity by offering:

- **Hardship Waiver**: 0 deposit for first-time proposers with strong rationale. Requires 2 steward approvals.  
- **Grants**: Governance can allocate a recurring **Proposal Support Fund**. A steward may nominate a proposal to be funded (deposit paid on proposer’s behalf).  
- **Fee Recycle**: Slashed deposits are earmarked to the Proposal Support Fund and/or dev tooling/CI credits.

Record waivers and grants in `TRANSPARENCY.md` meeting notes.

---

## 7) Appeals

- Proposer may appeal a **No Refund/Slash** within **14 days**.  
- Appeals are decided by a **separate steward quorum** than the original reviewers.  
- Outcome and rationale are recorded publicly with links to artifacts (PR, commits, tally, checks).

---

## 8) Ops Checklist (for Stewards)

1. Verify deposit tx → match PR proposer wallet (or documented sponsor).  
2. Confirm proposal passes local \`validate_proposal.py --strict\`.  
3. Add intake label + tier label (T1–T4).  
4. On closure/tally, post refund decision with the rubric section cited.  
5. Execute refund/slash multisig tx; link txid in PR and weekly minutes.  
6. Update monthly report: totals deposited / refunded / slashed; fund balance.

---

## 9) Examples

**Example A — Param Change (T1), reaches vote, fails:**  
Refund **100%**. Participation achieved; the process created value.

**Example B — Upgrade (T2), withdrawn after 1 review pass, no response for 21 days:**  
Refund **50%**. Steward time was consumed; inactivity caused closure.

**Example C — PQ Rotation (T3), spammy duplicates across weeks:**  
Refund **0%** and **slash**. Document pattern with links and timestamps.

**Example D — First-time proposer with waiver approved:**  
Deposit **0**. Mark waiver in minutes; otherwise treat normally.

---

## 10) Parameterization & Bounds

- Deposit knobs (**D_base**, **Tier multipliers**, **Scaling**) live in
  \`governance/registries/params_current.json\` under the namespace:
  \`gov.deposit.*\` (see also \`params_bounds.json\` for hard limits).  
- Changes to these values require a **param change** proposal and must validate within bounds (see `PARAMS_REGISTRY.mmd` diagram).

**Suggested keys**
- \`gov.deposit.base\` (int, ANM)  
- \`gov.deposit.multiplier.param_change\` (float)  
- \`gov.deposit.multiplier.upgrade\` (float)  
- \`gov.deposit.multiplier.pq_rotation\` (float)  
- \`gov.deposit.multiplier.cross_domain\` (float)  
- \`gov.deposit.scale.active_open_factor\` (float; 1.0–2.0)  
- \`gov.deposit.refund.partial_percent\` (0–100)

---

## 11) CLI Snippets

Validate locally (strict):

```bash
python governance/scripts/validate_proposal.py path/to/proposal.md --strict --pretty
Annotate your PR with a deposit proof:

markdown
Copy code
**Deposit:** 2000 ANM (T2)  
**Tx:** <txid> • **Escrow:** governance multisig (see ops/addresses)
Steward summary for refunds (template):

markdown
Copy code
**Refund Decision:** 100%  
**Reason:** Reached on-chain vote; proposer fully participated.  
**Tx:** <refund_txid> • **Date:** YYYY-MM-DD
12) Change Log
1.0 (2025-10-31): Initial deposit tiers, scaling, refunds/waivers, appeals, ops checklist.
