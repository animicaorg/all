# Governance Changelog

A human-readable log of **material changes to governance documents** and processes.
Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) style and
[SemVer](https://semver.org/) for the docs set as a whole.

**Scope:** `governance/*` plus cross-references in `docs/security/*` and `docs/ops/*`
when they materially affect governance procedures.

---

## [Unreleased]

### Added
- Template & rubric for **Request-for-Comments (RFC)** on governance changes.
- Quarterly review checklist for **Conflict Review Panel (CRP)** activity logs.

### Changed
- Clarified when **parameter changes** (Θ/Γ/PQ policy) require on-chain signaling vs. off-chain rough consensus.
- Tightened SLA for public posting of **meeting minutes** to 72h.

### Deprecated
- Legacy “informal poll” thread format (migrate to RFC + structured vote).

### Security
- Require PGP-signed publication for **emergency advisories** by Security Council.

---

## [1.1.0] — 2025-10-11

### Added
- **Conflict Review Panel (CRP)** process formalized in `governance/CONFLICTS.md` (§10),
  including waiver flow and appeal timelines.
- Public **COI profile YAML** template for delegates/maintainers (see `CONFLICTS.md` §11).
- **Maintainers table** cross-link anchors in `MAINTAINERS.md` (module → owners).

### Changed
- **Trading blackout** policy expanded to *T−48h…T+24h* for market-sensitive votes (was T−24h…T+12h).
- **Materiality thresholds** clarified (1% or \$25k, whichever lower) and aligned across docs.
- **Emergency powers** guardrails in `SECURITY_COUNCIL.md`:
  - Max continuous activation window (72h) unless reauthorized.
  - Post-incident public report within 7 days.

### Fixed
- Broken cross-references between `GOVERNANCE.md` roles and `ROLES.md` enumerations.
- Consistent terminology: “delegate” vs “recommended delegate”.

---

## [1.0.0] — 2025-09-15

### Added
- Initial governance corpus:
  - `GOVERNANCE.md` — constitution (roles, powers, checks & balances).
  - `CHARTER.md` — mission, values, conflicts-of-interest principles.
  - `ROLES.md` — token voters, maintainers, security council, editors.
  - `MAINTAINERS.md` — module ownership & escalation paths.
  - `SECURITY_COUNCIL.md` — emergency powers, quorum, publication duties.
  - `DELEGATION.md` — delegation mechanics & disclosure expectations.
  - `CONFLICTS.md` — disclosure, recusal, waiver framework.

---

## Migration & Compliance Notes

- **Grace periods:** Unless marked *urgent*, governance changes include a **7-day comment window**
  and **14-day adoption window** for operational procedures.
- **Records:** Update the public **COI registry** and maintainers table within **7 days** of any role/holding change.
- **Backports:** Security-critical governance clarifications may be backported as `x.y.z` *patch* releases.

---

## Versioning Policy

- **MAJOR**: structural changes to roles/powers or creation/retirement of a governing body.
- **MINOR**: policy adjustments (thresholds, timelines), new processes, added roles.
- **PATCH**: clarifications, typos, non-substantive cross-link fixes.

---

## How to Propose a Change

1. Open an **RFC issue** tagged `gov-change` with motivation, alternatives, and impact.
2. Link a PR touching the relevant file(s) under `governance/…`.
3. Obtain at least:
   - 2 maintainer approvals for affected modules, and
   - 1 Security Council acknowledgment for security-adjacent items.
4. On merge, **append** an entry to this changelog in the **Unreleased** section.

