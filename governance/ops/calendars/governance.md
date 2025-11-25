# Governance Calendar — Cadence & Agenda

**Version:** 1.0  
**Status:** Active  
**Scope:** Stewards, working groups, maintainers, and community observers.

---

## Cadence (UTC unless noted)

- **Weekly Triage (30–45m):** Every Tue @ 17:00 UTC  
  _Purpose:_ Intake new proposals, check schema/bounds status, assign reviewers.  
- **Bi-Weekly Steward Sync (45–60m):** Every other Thu @ 17:00 UTC  
  _Purpose:_ Decision prep, risk reviews, scheduling votes/activations.  
- **Monthly Governance Call (60m):** First Mon @ 17:00 UTC  
  _Purpose:_ Roadmap, metrics, postmortems, PQ/VM roadmaps.  
- **Quarterly Retrospective (90m):** First Wed of Q @ 17:00 UTC  
  _Purpose:_ Process updates, policy changes, elections/rotations.
- **Ad-hoc / Emergency (≤24h notice):** As needed per incident policy.

> Localize as needed; mirror invites on community calendar. Summaries follow the templates in `governance/policies/TRANSPARENCY.md`.

---

## Publishing & Artifacts

- **Agenda Draft:** Open/updated 48h before any scheduled call.  
- **Minutes:** Committed within 48h after meeting (`/policies/notes/` or linked PR).  
- **Recording (optional):** Post link if public; otherwise note “steward-confidential”.

Required links per agenda:
- Proposal PR(s) and IDs
- Bounds snapshot (`governance/registries/params_bounds.json`, `params_current.json`)
- Risk checklist(s): `governance/risk/*`
- Tooling: `validate_proposal.py`, `check_registry.py`, `tally_votes.py`

---

## Standing Agenda Template

```markdown
# {{ Meeting Type }} — YYYY-MM-DD (UTC)
**Chair:** <name> • **Scribe:** <name> • **Attendees:** <list>  
**Links:** Proposals (IDs/PRs), Dashboards, Checklists

## 1) Intake & Triage (≤15m)
- New proposals since last triage:
  - [ ] GOV-{{id}} — <title> — owner <@user> — status: schema/bounds ✅/❌
- Actions:
  - [ ] Assign reviewer(s) & due date
  - [ ] Deposit proof check (see DEPOSIT_AND_REFUNDS.md)

## 2) Reviews & Decisions (≤25m)
- Proposal A — decision target: approve/reject/needs-work
- Proposal B — …

## 3) Scheduling (≤10m)
- Votes to open/close (window, snapshot)
- Planned activations (heights/dates), canaries, abort switches

## 4) Risk & Ops (≤10m)
- Open incidents, provider strikes (see SLASHING_AND_RECOURSE.md)
- Telemetry: Γ, mempool, DA, VM metrics highlights

## 5) Open Floor (≤5m)

## Decisions & Actions
- D1: <decision> — rationale — links (txids/commits)
- A1: <owner> → <due>

## Disclosures (if any)
- <brief text or "none">

## Redactions (if any)
- Token REDACT-YYYYMMDD-## per Transparency Policy §7
Roles & Rotations
Chair: rotates monthly (alphabetical of stewards).

Scribe: rotates weekly (paired with chair).

Timekeeper: volunteer, ensures section limits.

Moderator: enforces Community Guidelines.

Update this section when rotations change.

Labels & Statuses (shared vocabulary)
intake/accepted • intake/blocked (needs deposit / schema fix)

review/owner-assigned • review/needs-revision • review/ready

vote/scheduled • vote/open • vote/closed

activation/scheduled • activation/canary • activation/done

policy/update • risk/incident • pq/rotation • vm/upgrade

Proposal Lifecycle Dates (to record per proposal)
Proposal ID	Intake	Review Target	Vote Window	Snapshot (height/ts)	Activation (height/date)	Links
GOV-YYYY-MM-ABC	2025-MM-DD	+7d	2025-MM-DD → 2025-MM-DD	h=#######	2025-MM-DD / h=#######	PR / tally / risk

Keep this table short; detail lives in the proposal PR and minutes.

Emergency Meeting Protocol (summary)
Trigger: Sev-1 incident or governance blocking issue.

Convene stewards (quorum) within 24h; publish brief within 24h.

Use UPGRADE_FLOW.mmd & TRANSPARENCY.md for comms and gates.

All emergency decisions require follow-up ratification at next regular call.

Upcoming Skeleton (example month)
markdown
Copy code
### Week 1
- Tue Triage — draft agenda opened
- Thu Steward Sync — schedule GOV-… vote (open next Mon)

### Week 2
- Mon: Open vote window (7 days)
- Thu: Risk review for VM opcode activation (canary plan)

### Week 3
- Mon: Close vote, publish tally (48h)
- Wed: Stage canary at height H; abort switch pre-armed

### Week 4
- Mon: Full rollout if metrics stable
- Thu: Postmortem & minutes for month; prep retrospective
Quick Links
governance/diagrams/GOVERNANCE_FLOW.mmd

governance/diagrams/UPGRADE_FLOW.mmd

governance/policies/TRANSPARENCY.md

governance/policies/COMMUNITY_GUIDELINES.md

governance/policies/DEPOSIT_AND_REFUNDS.md

governance/policies/SLASHING_AND_RECOURSE.md

