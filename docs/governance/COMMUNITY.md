# Community Governance — Proposals, RFCs & Signaling

This document explains **how the Animica community proposes, debates, and signals** changes. It complements:
- `docs/governance/OVERVIEW.md` — formal decision model and on-chain enactment
- `docs/governance/PARAMS.md` — governable parameters and boundaries
- `docs/spec/UPGRADES.md` — protocol upgrade mechanics & gates

> TL;DR: Start with a short **Proposal Issue**, iterate an **RFC**, gather **off-chain signal**, and—when ready—advance to a **governance bundle** for on-chain activation.

---

## 1) Proposal Types

- **Parameter Change**: mempool limits, fee floors, Θ/Γ schedules, DAS sampling, etc.
- **Policy Update**: PQ algorithm policy, AICF/SLA, randomness windows.
- **Network Upgrade**: feature flags, hard/soft forks (requires code + releases).
- **Registry/Metadata**: chain entries, circuit VKs, address rules (non-consensus but impactful).
- **Process/Docs**: governance process tweaks, docs, security policies.

Each proposal must state its **classification** and **risk level** (low/medium/high).

---

## 2) Tracks & Identifiers

Use **AIP** (Animica Improvement Proposal) IDs:
- Format: `AIP-####` (monotonic), assigned when an RFC PR is opened.
- File path (if merged): `docs/aip/AIP-####.md` (created by the PR).
- Status lifecycle: `Draft → Review → Last Call → Accepted → Scheduled → Enacted`  
  Terminal states: `Rejected | Withdrawn | Superseded`.

AIP index is maintained in `docs/CHANGELOG.md` and the website changelog.

---

## 3) Roles

- **Authors**: community members proposing the change.
- **Reviewers** (rotating maintainers):
  - **Security** (threat model, PQ posture, replay/nullifiers)
  - **Economics** (fees, rewards, Γ caps, fairness)
  - **Operators** (devops, P2P limits, observability)
  - **Protocol** (compat, upgrade gates, light-client impact)
- **Editors**: shepherd process, assign IDs, track statuses.

Conflicts of interest must be disclosed in the RFC.

---

## 4) The Path: From Idea to Enactment

1. **Idea / Temperature Check**
   - Open a short **Proposal Issue** (GitHub Discussions or Issues) with:
     - Problem statement (3–5 sentences)
     - Type (Param/Policy/Upgrade/Registry/Process)
     - Risk level & expected blast radius
   - Collect initial feedback for ≥ **72 hours**.

2. **RFC (Request for Comments)**
   - Open a PR with `AIP-####` using the template (§6).
   - Include: rationale, alternatives, metrics of success, roll-back plan.
   - Attach draft configs (e.g., `spec/poies_policy.yaml` delta), test vectors, and if applicable a **governance bundle** sketch (pin hashes, activation window).

3. **Review Window**
   - Minimum **7 days** for low/med risk; **14 days** for high risk or upgrades.
   - Editors ensure **Security**, **Economics**, and **Operators** reviews are present.
   - CI must pass: schema validation, unit tests, reproducible hash checks.

4. **Last Call**
   - Editor marks RFC `Last Call` for **5 days**:
     - Summarize open questions, link to diffs.
     - Off-chain signaling (§5) occurs here.

5. **Decision & Scheduling**
   - If consensus is reached:
     - Merge RFC → status `Accepted`.
     - Produce signed **governance bundle** (policy root, VK hashes, params) with target height/ETA.
     - For upgrades: follow `docs/spec/UPGRADES.md` gates (feature flags, release tags).
   - Otherwise: mark `Rejected` (with reason) or `Rework` (remain `Draft`).

6. **Enactment**
   - Governance multi-sig (or DAO) signs the bundle.
   - Node releases include the pinned artifacts.
   - After the activation height, RFC becomes `Enacted`.

7. **Post-Enactment Review**
   - Within **14 days**, publish impact metrics and any roll-forward/back notes.

---

## 5) Signaling (Off-Chain)

Off-chain signals are **advisory** but crucial for legitimacy:

- **Snapshot-style poll** (1 address = 1 vote, or stake/role-weighted)—non-binding.
- **Validator Advisory Poll**: operators signal yes/no/abstain with rationale.
- **Community Sentiment**: GitHub reactions & comments (summarized by editors).
- **Risk Sign-off Checkboxes** in the RFC PR:
  - [ ] Security OK
  - [ ] Economics OK
  - [ ] Operators OK
  - [ ] Protocol/Light-Client OK
  - [ ] Docs & SDKs updated

Editors record links to polls and tallies in the RFC.

---

## 6) RFC Template

Copy this into your PR as `docs/aip/AIP-XXXX.md`:

```markdown
---
aip: AIP-XXXX
title: <Short title>
author: <@github or name>, <contact>
type: <Parameter | Policy | Upgrade | Registry | Process>
risk: <low | medium | high>
status: Draft
created: YYYY-MM-DD
requires: [AIP-0000?]
supersedes: []
---

## Summary
One paragraph summary and expected outcome.

## Motivation / Problem Statement
What problem are we solving, who is impacted, and why now?

## Specification
- Precise changes (configs, policy roots, wire, APIs).
- If parameters: include before/after table and bounds.
- If policy (e.g., PQ or AICF): include Merkle-root inputs and resulting root hash.
- If upgrade: feature flags, activation, migration steps.

## Rationale & Alternatives
Discuss rejected options and trade-offs.

## Security & Privacy Considerations
Threats, mitigations, monitoring hooks; PQ posture; nullifier/replay; DoS budgets.

## Economics / Fees / Rewards
Expected fee/tip dynamics, Γ caps, fairness indices, treasury effects.

## Compatibility / Deployment Plan
Backwards-compat, light-client impact, roll-out stages, rollback plan.

## Reference Implementation / Tests
Links to PRs, fixtures, reproducible build hashes, CI jobs.

## Off-Chain Signaling
Links to discussions, polls, validator advisory, editor summary.

## Changelog
- YYYY-MM-DD: Draft


⸻

7) Emergency RFCs

For time-sensitive security incidents (e.g., PQ break, critical DoS):
	•	Editors may compress the timeline to 6–24 hours of review and a 6–12 hour timelock for the governance bundle (see docs/governance/PQ_POLICY.md emergency levers).
	•	Post-mortem and full RFC refinement are mandatory within 7 days.

⸻

8) Transparency & Archives
	•	All decisions, votes, and signed bundles are archived in the repo with checksums.
	•	The website surfaces proposal statuses and bundles.
	•	Meeting notes (if any) are linked from the RFC.

⸻

9) Code & Docs Coupling
	•	Param/Policy-only changes: require no code merge if implementations already exist.
	•	Upgrades: must ship code, tests, migration guides; see docs/spec/UPGRADES.md.
	•	Docs, SDK snippets, and OpenRPC examples must be updated in the same milestone.

⸻

10) Conduct & Moderation

We follow a Code of Conduct: be constructive, disclose conflicts, avoid ad-hominem.
Moderators may redact sensitive PII and remove spam; decisions are logged.

⸻

11) FAQs

Q: Are Snapshot polls binding?
A: No. They inform editors and governors; the signed governance bundle is the binding artifact.

Q: Who assigns AIP numbers?
A: Editors on first RFC PR; numbers are sequential.

Q: Can external ecosystems propose?
A: Yes. Provide clear interop implications and references.

⸻

12) References
	•	docs/governance/OVERVIEW.md
	•	docs/governance/PARAMS.md
	•	docs/spec/UPGRADES.md
	•	docs/security/THREAT_MODEL.md
	•	docs/economics/OVERVIEW.md

