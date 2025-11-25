# TRANSPARENCY POLICY
_Public artifacts, meeting notes, and disclosures for Animica governance_

**Version:** 1.0  
**Status:** Active  
**Applies to:** Core protocol governance, parameters, PQ policy, VM upgrades, DA settings, AICF/Quantum providers, stewards/multisigs, working groups.

---

## 1) Purpose & Principles

We publish the context, process, and outcomes of governance so stakeholders can **verify**, **reproduce**, and **audit** decisions.

**Principles**
- **Open by default:** Artifacts and deliberations are public unless a specific privacy exception applies.
- **Reproducibility:** Every decision references machine-readable inputs (schemas, registries, commits, checksums).
- **Timeliness:** Publish before votes; summarize promptly after outcomes.
- **Minimize redaction:** Redact only what is necessary (security-sensitive, PII, contractual NDAs).
- **Traceability:** Every artifact links to proposal IDs, commit SHAs, and on-chain txids.

---

## 2) Artifact Map (What we publish & where)

| Category | Artifact | Location | Notes |
|---|---|---|---|
| Proposals | Markdown + YAML header | `governance/examples/*` and PRs | Validate with `validate_proposal.py` |
| Registries | `params_current.json`, `params_bounds.json`, `contracts.json`, `upgrade_paths.json` | `governance/registries/` | Linted by `check_registry.py` |
| Schemas | `*.schema.json` | `governance/schemas/` | Draft 2020-12; tested in CI |
| Ballots/Tallies (examples) | `examples/ballots/*.json`, `examples/tallies/*.json` | `governance/examples/` | Real ballots are on-chain; examples aid tooling |
| Minutes/Notes | Meeting notes, agendas, attendance | `governance/ops/calendars/`, `governance/policies/notes/` (if created) | Use the template below |
| Risk/Reviews | Checklists, threat notes | `governance/risk/*` | Must be linked from proposals |
| PQ Policy | Policies & rotations | `governance/PQ_POLICY.md` | Changes require proposals |
| Diagrams | Mermaid flows | `governance/diagrams/*` | Source-of-truth for process |
| ADRs | Architecture decision records | `governance/adrs/*` | Link from proposals & notes |
| Releases | Changelogs, hashes | `chains/CHANGELOG.md`, `chains/checksums.txt` | Signed when practical |

---

## 3) Publication Cadence

- **Before vote opens:** Proposal MD + YAML header, links to schemas/registries, risk checklist draft.
- **While voting:** Running telemetry dashboards (if applicable), Q&A thread, disclosures updates.
- **After vote closes:** Tally JSON + narrative summary within **48 hours**.
- **Before activation:** Final pre-flight checklist, rollout plan, abort switches documented.
- **After activation:** Postmortem + lessons learned within **7 days** (or earlier for minor items).

---

## 4) Meeting Notes (Template)

> Use this template for all stewardship, working group, and incident review meetings. Commit to the repo and link in the agenda.

```markdown
# Meeting Notes — {{ Working Group / Stewards }}
**Date:** YYYY-MM-DD • **Duration:** HH:MM–HH:MM UTC  
**Chair:** <name> • **Scribe:** <name> • **Attendees:** <list>  
**Related Proposals:** GOV-YYYY-MM-XXX (links)  
**Recording:** <link if public> • **Artifacts:** <PRs, commits, dashboards>

## 1. Agenda & Objectives
- Item 1
- Item 2

## 2. Context & Materials
- Proposal ref(s), schema versions, bounds snapshots
- Risk checklist links

## 3. Discussion Summary
- Key arguments for/against
- Open questions / requested data

## 4. Decisions & Actions
- D#1 Decision: <short text>  
  - Rationale: <why>  
  - Links: <commits / issues / txids>  
- A#1 Action: <owner> → <due date>

## 5. Disclosures (Declared at meeting)
- Conflicts of interest:
- External affiliations / grants:

## 6. Follow-ups
- <owner> → <due date>

## 7. Redactions (if any)
- Section(s) and justification per §7 of Transparency Policy
5) Disclosures
All stewards, maintainers, and voting delegates must disclose:

Conflicts of interest: financial stakes, employment/contracting, advisory roles, token holdings that create material influence.

Funding sources: grants, sponsorships, in-kind compute/credits (AICF/Quantum providers).

Prior relationships with vendors/providers relevant to proposals.

Operational incidents under investigation that could bias decisions.

Format: Add/update an entry in governance/policies/DISCLOSURES.md (create if absent) per person/org with date-stamped changes.

6) Privacy & Security Boundaries
Redactable classes:

Zero-day / unpatched vulnerabilities and exploit paths.
Publish after remediation with timelines and CVE/CWE refs when applicable.

Secrets & private keys (never committed).

Personal data (PII) beyond public handles.

Third-party confidential terms covered by NDA.

Partial disclosure: Where full detail is unsafe, publish an abstracted risk description, severity, affected versions, and remediation status.

7) Redaction Rules
Redactions must cite: who requested, who approved, justification, sunset date (when full text can be released), and scope (specific lines/sections).

Keep a non-public sealed appendix accessible to the governance multisig for audit.

Record a change log entry referencing the redaction token (e.g., REDACT-YYYYMMDD-##).

8) Access Levels
Level	Audience	Examples
Public	Everyone	Proposals, schemas, bounds, tallies, minutes
Time-delayed	Everyone after T+N days	Post-mortem with exploit details
Steward-confidential	Stewards/multisig until mitigated	Active incident technicals, vendor quotes
Private (never published)	Key custodians only	Secrets, raw personal data

9) Publication Workflow (PR Checklist)
Validate proposal/examples:

bash
Copy code
python governance/scripts/validate_proposal.py path/to/proposal.md --strict
pytest -q governance/tests
Lint registries (if edited):

bash
Copy code
python governance/scripts/check_registry.py --strict --pretty
Link all artifacts in the PR description (proposal ID, schemas, bounds snapshot, risk checklist).

Add/Update meeting notes using the template.

Add/Update disclosures as needed.

Tag reviewers: stewards + domain owners.

After merge, announce with links (site, forum, social as appropriate).

10) Incident Transparency
Initial notice: within 24 hours of confirmation — scope, versions, user impact, mitigations underway.

Interim updates: daily for Sev-1, every 3 days for Sev-2 until resolution.

Final postmortem: within 7 days after resolution, including:

Timeline; root cause; detection; blast radius; user impact; metrics

What went well/poorly; corrective actions; follow-up owners/dates

Links to commits, releases, and any governance changes required

11) Attribution & Licensing
Documentation is CC BY 4.0 unless stated otherwise.

Diagrams are source-available with the repo’s license.

External assets must include licenses/attribution in contrib/LICENSING.md.

12) Change Log
1.0 (2025-10-31): Initial policy added. Templates and workflows aligned with governance tooling.

Appendix A — Quick Links
governance/scripts/validate_proposal.py

governance/scripts/check_registry.py

governance/scripts/generate_ballot.py

governance/scripts/tally_votes.py

governance/tests/test_schemas.py

governance/tests/test_validate_examples.py

governance/risk/*

governance/adrs/*

governance/diagrams/*
