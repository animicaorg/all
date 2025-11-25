# Governance Process Lifecycle
**Draft → RFC → Vote → Enact → Review**

This document describes the canonical lifecycle for policy and protocol changes
across the Animica project. It complements:
- `governance/GOVERNANCE.md` (roles, powers, checks & balances)
- `governance/ROLES.md` (token voters, maintainers, editors, Security Council)
- `governance/CONFLICTS.md` (disclosure & recusal)
- `governance/SECURITY_COUNCIL.md` (emergency powers)
- `governance/CHANGELOG.md` (history of governance changes)

> **Principles:** transparent, time-bounded, evidence-based, security-first, low coordination cost.

---

## 0) Overview

We use a five-state pipeline:

1. **Draft** — idea formation and early feedback.
2. **RFC** — stable proposal; community review window.
3. **Vote** — binding decision using the configured voting mechanism.
4. **Enact** — implement, tag, publish, and roll out.
5. **Review** — post-enactment evaluation and potential follow-ups.

A proposal can move backward (e.g., Review → Draft) if material issues arise.

### State machine (Mermaid)

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> RFC: Author declares readiness\nEditors ACK scope & template
    RFC --> Vote: Review window closes\nEditors freeze text
    Vote --> Enact: Quorum met & threshold passed
    Vote --> Draft: Failed vote or material defect
    Enact --> Review: Effective date reached
    Review --> [*]
    Review --> Draft: Revision needed

    %% Emergency lane
    state "Emergency Track" as E
    E: Invoked by Security Council\nper SECURITY_COUNCIL.md
    Draft --> E: Security-critical
    E --> Enact: Time-bounded activation
    Enact --> Vote: Retroactive ratification within 14 days


⸻

1) Roles & Responsibilities (RACI)

Stage	Responsible	Accountable	Consulted	Informed
Draft	Author(s)	Editors	Maintainers, Domain experts	Community
RFC	Editors	Editors	Maintainers, Security Council (if relevant)	Community
Vote	Token voters / Delegates	Governance Admins	Editors, Security Council	Community
Enact	Maintainers / Release Eng	Maintainers Lead	Editors, Security Council	Community
Review	Editors	Editors	Maintainers, Product & Ops	Community

See ROLES.md and MAINTAINERS.md for names and scopes.

⸻

2) Timelines & SLAs

Default time bounds (unless overridden in the proposal and accepted by Editors):
	•	Draft: open-ended.
	•	RFC window: ≥ 7 days public comment (business days preferred).
	•	Vote window: 72–120 hours (aligned with time zones).
	•	Grace/adoption: 14 days after Enact for operational changes (see CHANGELOG.md).
	•	Emergency track: continuous activation ≤ 72 hours per SECURITY_COUNCIL.md, followed by a 14-day retroactive vote.

Market-sensitive votes: enforce trading blackout T−48h…T+24h (see CHANGELOG.md).

⸻

3) Stage Definitions & Checklists

A. Draft

Goal: Shape the problem and plausible solutions.

Entry: Any contributor opens a Draft (issue or PR) with:
	•	Problem statement, motivations, risks.
	•	Alternatives considered.
	•	Rough impact on: consensus, security, user UX, ops.
	•	Decision class (see §6).

Exit criteria (to RFC):
	•	Editors confirm scope & correct template.
	•	Security impact preliminarily assessed.
	•	COI disclosures attached (see CONFLICTS.md).

⸻

B. RFC

Goal: Stabilize text for a binding vote.

Requirements:
	•	Proposal lives under governance/proposals/<YYYY>-<slug>/.
	•	Includes RFC.md (canonical text) and IMPACT.md (risks, migration).
	•	Link to reference PRs across repos (specs, code, docs).
	•	If consensus-affecting: provide test vectors or simulations.

Process:
	•	Public comment window ≥ 7 days.
	•	Editors may request changes; material changes restart the 7-day window.

Exit criteria (to Vote):
	•	Editors freeze RFC (hash the text; record rfcHash in ballot).
	•	Voting details finalized (snapshot height, quorum, threshold).

⸻

C. Vote

Goal: Reach a binding decision.

Ballot fields (example YAML):

id: GOV-2025-04
title: "Enable Θ retarget clamp update"
rfcHash: "sha3-256:abcd…"
snapshotHeight: 123456
class: "Consensus-Param"
quorum: "15% voting power"
threshold: "simple-majority"
window: "2025-10-18T12:00Z..2025-10-22T12:00Z"
blackout: "T-48h..T+24h"
options: ["YES","NO","ABSTAIN"]

Mechanics:
	•	Snapshot: voting power at first block ≥ snapshotHeight after RFC freeze.
	•	Quorum: default ≥ 15% of eligible voting power.
	•	Thresholds: see Decision Classes (§6).
	•	Delegation: per DELEGATION.md; sub-delegations allowed if disclosed.
	•	COI: recusal per CONFLICTS.md. Violations can nullify ballots.

Outcomes:
	•	Pass: proceed to Enact.
	•	Fail / No Quorum: return to Draft or schedule Revision.

⸻

D. Enact

Goal: Implement, tag, communicate, and roll out.

Checklist:
	•	Merge code/spec PRs; tag releases; update docs/ and website.
	•	Update params/feature flags behind gates where applicable.
	•	Publish migration notes & ops runbooks; schedule maintenance windows.
	•	Append entry to governance/CHANGELOG.md.
	•	Set effective date and, if needed, sunset or review date.

⸻

E. Review

Goal: Evaluate outcomes; correct course if needed.

Inputs:
	•	Metrics & telemetry (see docs/dev/METRICS.md).
	•	Incident reports, user feedback, economic indicators.

Outputs:
	•	Keep / Amend / Revert recommendation.
	•	Follow-up RFCs if material changes are proposed.

⸻

4) Artifacts & Layout

governance/
  proposals/
    2025-enable-theta-clamp/
      RFC.md
      IMPACT.md
      DIFFS.md            # optional: spec/code diffs summary
      BALLOT.yaml         # finalized ballot for on-chain/off-chain vote tool
      REFERENCES.md       # links to PRs, issues, simulations

Content rules:
	•	All normative text in RFC.md; external links are non-normative.
	•	Hash the frozen RFC (sha3-256) and record in BALLOT.yaml.

⸻

5) Evidence, Risk & Security

Each RFC must include:
	•	Threat model delta and mitigations (link docs/security/THREAT_MODEL.md).
	•	Operational risks (rollback plan, feature flags).
	•	User impact (wallets, SDKs, exchanges).
	•	Economics (fees, rewards, issuance) when relevant (link docs/economics/*).

⸻

6) Decision Classes & Voting Thresholds

Class	Examples	Quorum	Threshold	Notice
Informational	Doc structure, tooling only	—	— (no vote)	—
Process/Policy	Governance templates, COI updates	15%	Simple majority	7 days
Params (Consensus)	Θ/Γ changes, cap rules (no hard fork)	15%	Simple majority	7 days
Feature (Soft fork)	Optional protocol features, gated	20%	≥ 60%	14 days
Feature (Hard fork)	Consensus-breaking, chain format	25%	≥ 66.7%	21 days
Emergency	Critical vulns; Security Council activation	—	Council quorum (see SECURITY_COUNCIL.md)	≤72h activation + 14d ratification

Editors may raise class severity; lowering requires an RFC itself.

⸻

7) Communications & Transparency
	•	Single source of truth: proposal directory in governance/proposals/....
	•	Announcements: website blog, mailing list, forum, and relevant repos.
	•	Meeting minutes: publish within 72 hours (see CHANGELOG.md policy).
	•	All ballots, tallies, and artifacts are archived and hash-addressed.

⸻

8) Compliance: COI, Recusal, Blackouts
	•	All participants maintain up-to-date COI profiles (CONFLICTS.md).
	•	Recusal declarations included in the RFC thread and ballot record.
	•	Trading blackouts for market-sensitive votes: T−48h…T+24h.
	•	Violations can trigger vote challenges or Security Council review.

⸻

9) Versioning & Backports
	•	Document set uses SemVer (see CHANGELOG.md for releases).
	•	Security-critical clarifications may be backported as PATCH updates.
	•	Proposals include effective, sunset, and review dates where applicable.

⸻

10) Metrics & Post-Hoc Audits
	•	Track: vote participation, time-to-decision, incident rate post-enactment,
and reversal frequency.
	•	Annual meta-review of the process with suggested improvements.

⸻

11) Templates

RFC front-matter (YAML)

id: RFC-2025-ThetaClamp
title: "Retarget Clamp Adjustment for Θ"
author: ["alice", "bob"]
sponsors: ["maintainers-core", "economics-wg"]
class: "Consensus-Param"
created: "2025-10-11"
status: "RFC"
review_window_days: 7
effective_date: "2025-11-01"
sunset_date: null
security_review: "required"
coi_disclosures: ["alice: no conflicts", "bob: validator operator"]

Ballot (YAML)

id: GOV-2025-04
rfc: RFC-2025-ThetaClamp
rfcHash: "sha3-256:abcd…"
snapshotHeight: 123456
quorum: "15%"
threshold: "simple-majority"
window: "2025-10-18T12:00Z..2025-10-22T12:00Z"
options: ["YES","NO","ABSTAIN"]
blackout: "T-48h..T+24h"


⸻

12) FAQs
	•	Can Editors veto? Editors can bounce proposals on process/quality grounds,
not on policy preference. Substantive veto requires a vote or Security Council action.
	•	What if quorum is repeatedly missed? Editors may reclassify the decision or
schedule a broader outreach; the author can withdraw or revise.
	•	How are tie votes handled? Fails by default; authors may re-RFC with changes.

⸻

13) Canonical Checklists

Author (before RFC):
	•	Problem and alternatives
	•	Security & ops impacts
	•	COI disclosure
	•	Draft artifacts complete

Editors (before Vote):
	•	Template compliance
	•	Review window ≥ 7 days completed
	•	RFC frozen & hashed
	•	Ballot prepared (snapshot/quorum/threshold)

Maintainers (Enact):
	•	Code/spec merged & tagged
	•	Docs & website updated
	•	Migration/runbooks published
	•	CHANGELOG entry added

Editors (Review):
	•	Metrics collected
	•	Outcomes assessed
	•	Follow-ups (if any) filed

⸻

This process may itself be amended only via an RFC + Vote in the “Process/Policy” class.
