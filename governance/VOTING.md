# Voting Rules
**Windows · Snapshot · Tally (support / against / abstain)**

This document defines how proposals are voted on and how results are tallied.
It applies to all proposal classes in `governance/PROPOSAL_TYPES.md`. Class-
specific quorum and threshold defaults are defined there; this document
specifies the *mechanics* shared by every class.

---

## 1) Eligibility & Snapshot

- **Who may vote:** accounts holding voting power (VP) at the **snapshot
  height** for the proposal.
- **Snapshot height:** the block height when a proposal moves from *Draft/RFC*
  to *Vote Open*. Voting power is *checkpointed* and remains constant for this
  vote—later transfers/delegations do not affect this proposal.
- **Delegation:** voters may delegate VP to another account *before* the
  snapshot. Delegation changes after the snapshot do not apply to this vote.
- **One-subject rule:** proposals must contain a single decision subject.
  Multi-subject items should be split; otherwise editors may send them back.

---

## 2) Timeline & Windows

Each proposal progresses:

1. **Draft → RFC** (discussion & revisions)  
   - Recommended: 5–14 days (class-dependent).
2. **Vote Open** (binding voting window)  
   - **Default duration:**  
     - ParamChange/Policy: **7 days**  
     - PQRotation/Soft-fork Upgrade/Treasury: **14 days**  
     - Hard-fork Upgrade: **21 days**
3. **Quiet-ending extension (anti-sniping):**  
   If in the **last 24h** the outcome flips or the margin to threshold is
   **< 1.0% of total VP**, the window auto-extends by **24h**, up to **72h**
   maximum extension.
4. **Finalization:** results sealed; *Review* period begins (see class docs).

Editors may lengthen windows; shortening requires a Process/Policy vote or
Security Council emergency procedure (see `SECURITY_COUNCIL.md`).

---

## 3) Ballot Options & Semantics

Voters submit **exactly one** of:

- **Support** — counts toward approval.
- **Against** — counts toward rejection.
- **Abstain** — **counts for quorum** but is **excluded** from threshold ratio.
- *(Optional, gated)* **Veto** — Security Council only, with constraints defined
  in `SECURITY_COUNCIL.md`. A valid Veto immediately fails the proposal.

Voters may **change** their ballot any time while the vote is open; only the
latest ballot at close is tallied. Not voting ≠ Abstaining.

---

## 4) Quorum & Threshold (Class-aware)

Let:
- `VP_total` = total eligible voting power at snapshot.
- `VP_sup`, `VP_agn`, `VP_abs` = sum of Support/Against/Abstain ballots.

**Quorum check:**  
`(VP_sup + VP_agn + VP_abs) / VP_total  >=  quorum(class)`

**Approval threshold (supermajority by class):**  
`ApprovalRatio = VP_sup / (VP_sup + VP_agn)`  
A proposal **passes** iff `Quorum` is met **and** `ApprovalRatio >= threshold(class)`.

> **Abstain** helps reach quorum but **does not** dilute the supermajority
> denominator.

**Defaults (see PROPOSAL_TYPES.md):**

| Class            | Quorum | Threshold  |
|------------------|:------:|:----------:|
| ParamChange      | 15%    | > 50%      |
| Policy           | 15%    | > 50%      |
| PQRotation       | 20%    | ≥ 60%      |
| Upgrade (soft)   | 20%    | ≥ 60%      |
| Upgrade (hard)   | 25%    | ≥ 66.7%    |
| Treasury         | 20%    | ≥ 60%      |

**Tie rule:** If `VP_sup == VP_agn`, the proposal **fails** (threshold unmet).

---

## 5) Formal Tally (Reference)

```text
Inputs:
  VP_total > 0
  ballots = { addr -> {choice ∈ {SUPPORT, AGAINST, ABSTAIN}, weight} }

Aggregate:
  VP_sup = Σ weight where choice=SUPPORT
  VP_agn = Σ weight where choice=AGAINST
  VP_abs = Σ weight where choice=ABSTAIN

Checks:
  reached_quorum = (VP_sup + VP_agn + VP_abs) / VP_total >= quorum(class)
  denom = VP_sup + VP_agn
  approval_ratio = (denom == 0) ? 0 : VP_sup / denom
  passed = reached_quorum AND (approval_ratio >= threshold(class))

Outputs:
  { reached_quorum, approval_ratio, passed,
    tallies: {support: VP_sup, against: VP_agn, abstain: VP_abs},
    participation: (VP_sup + VP_agn + VP_abs) / VP_total }


⸻

6) Examples

A) ParamChange
	•	Quorum = 15%, Threshold = >50%
	•	Snapshot VP_total = 100M
	•	Ballots: Support 30M, Against 20M, Abstain 5M → Participation = 55% ✅
	•	ApprovalRatio = 30 / (30+20) = 60% → Pass

B) Hard-fork Upgrade
	•	Quorum = 25%, Threshold = ≥66.7%
	•	VP_total = 300M
	•	Ballots: Support 120M, Against 50M, Abstain 10M → Participation = 60% ✅
	•	ApprovalRatio = 120 / 170 ≈ 70.6% → Pass

C) Fails quorum
	•	VP_total = 200M
	•	Ballots: Support 20M, Against 5M, Abstain 3M → Participation = 14% ❌ → Fail

⸻

7) Operational Notes
	•	Ballot integrity: ballots are authenticated and auditable. A commit-reveal
mode MAY be offered for whale privacy; if used, reveal must occur ≥2h before
window close (missed reveals count as no vote).
	•	Sybil/identity: voting power derives from chain records at snapshot.
	•	Conflicts of interest: sponsors must disclose COI in RFC front-matter.
	•	Multiple competing proposals: Editors may sequence or require a
preferential follow-up if mutually exclusive changes are both approved.

⸻

8) Disputes & Review
	•	Result challenge window: 48h after close for proof-of-error claims
(tally, snapshot set, or vote accounting). Proven errors → administrative
recount; otherwise results stand.
	•	Post-enact review: as specified by class (typically 14–30 days). A
narrowly passing high-risk change may schedule an automatic follow-up review.

⸻

9) Interfaces & Data
	•	Events: ProposalCreated, VoteCast(addr, choice, weight),
VoteFinalized(result, tallies, approval_ratio).
	•	APIs: read-only endpoints must expose snapshot height, class, windows,
tallies (live), and final JSON receipts.

⸻

10) Backward Compatibility

Changes to quorum/thresholds or windows require a Policy or Process
proposal and apply only to proposals opened after activation.

⸻

Edits to this document require a Policy-class proposal.
