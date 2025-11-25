---
title: "Governance Boot: Initial Model Adopted"
status: "Accepted"
date: "2025-10-13"
owners:
  - Governance Editors
  - Core Maintainers Council
reviewers:
  - Security Council (interim)
component: "Governance"
scope: "Project-wide (technical & economic parameters)"
links:
  relates_to:
    - governance/GOVERNANCE.md
    - governance/PROCESS.md
    - governance/ROLES.md
    - governance/THRESHOLDS.md
    - governance/VOTING.md
    - governance/UPGRADE_PROCESS.md
    - governance/PARAMS_GOVERNANCE.md
    - governance/PQ_POLICY.md
  registries:
    - governance/registries/params_registry.yaml
    - governance/registries/pq_alg_policy.json
    - governance/registries/module_owners.yaml
    - governance/registries/voter_roles.yaml
    - governance/registries/contracts.json
    - governance/registries/upgrade_paths.json
---

# ADR-0001 — Governance Boot: Initial Model Adopted

## Summary

Adopt a **minimal-but-complete governance framework** that cleanly separates **token voters**, **maintainers**, **security council**, and **editors**, with scoped proposal types, quorum/threshold rules, documented processes, and emergency powers bounded by time and scope. This ADR establishes the baseline rules of change for protocol parameters, upgrades, PQ rotations, and policy documents.

---

## Context

Animica spans multiple subsystems (consensus, proofs, DA, VM, wallet, AICF). Changes must be:
- **Legible** (clear ownership, thresholds, audit trails)
- **Safe** (bounded risk for security-sensitive changes)
- **Actionable** (predictable lifecycle from draft → enactment)
- **Evolvable** (ability to refine without governance deadlocks)

We need a **starting governance model** before mainnet activity increases and before third-party integrations depend on stable processes.

---

## Decision

We adopt the following **initial governance model** (details live in linked docs; this ADR is normative):

1. **Roles & Separation of Powers**
   - **Token Voters**: Holders participate in binding votes on proposal types in scope.
   - **Maintainers**: Module owners with merge/release authority; draft and implement approved changes.
   - **Security Council (interim)**: Small multisig with **narrow emergency powers**, time-limited, subject to post-facto review.
   - **Governance Editors**: Curate process docs, registries, and ADR index; no veto power.

2. **Proposal Types (see `PROPOSAL_TYPES.md`)**
   - **ParamChange** (e.g., Θ/Γ caps, fee market bounds)
   - **Upgrade** (feature flags, consensus changes)
   - **PQRotation** (alg enable/disable, grace windows)
   - **Policy** (mempool, P2P DoS, DA sampling)
   - **Treasury** (AICF splits, program funding)

3. **Quorum & Thresholds (see `THRESHOLDS.md`)**
   - Defaults (subject to registry tuning):
     - **Quorum:** 15% circulating voting power
     - **Pass:** Simple majority of For vs Against (excluding Abstain) for ParamChange/Policy
     - **Supermajorities:** 2/3 for Upgrade; 3/4 for PQRotation
   - Snapshot & vote windows defined in `VOTING.md`.

4. **Lifecycle (see `PROCESS.md`)**
   - **Draft → RFC → Vote → Enact → Review**
   - Each proposal must include: risk analysis, rollback switches, migration plan, observability additions, and test coverage plan.

5. **Emergency Procedures (see `EMERGENCY_PROCEDURES.md`)**
   - Security Council may **temporarily disable** a feature flag or set **circuit breakers** for ≤ *N* days (default 14).
   - Requires public incident note, mandatory postmortem, and ratification vote if extending beyond the window.

6. **Upgrades & Versioning (see `UPGRADE_PROCESS.md`)**
   - SemVer across modules; **feature gates** for consensus changes.
   - `upgrade_paths.json` defines allowed version migrations and rollback paths.

7. **Registries as Sources of Truth**
   - `params_registry.yaml`, `pq_alg_policy.json`, `module_owners.yaml`, etc., gate CI checks and explorer/docs generation.

8. **Transparency & Auditability**
   - Every enacted proposal produces:
     - On-chain / signed artifacts references (where applicable)
     - Commit hash & release tag
     - Docs and ADR updates
     - Observatory dashboards/alerts updates

9. **Sunset & Review**
   - Model is reviewed **quarterly**; this ADR may be amended or superseded.

---

## Rationale

- **Safety first** for consensus and PQ rotations; higher thresholds where the blast radius is large.
- **Operational clarity** with owners/registries ensures changes map to code & releases.
- **Emergency powers with guardrails** balance rapid mitigation and community legitimacy.
- **Composable docs & registries** allow automation in CI, website, and explorer.

---

## Alternatives Considered

| Alternative | Pros | Cons | Decision |
|---|---|---|---|
| Maintainers-only governance (informal) | Fast | Low legitimacy, risk of centralization, unclear thresholds | Rejected |
| Token-weighted DAO from day 1 (all changes) | Maximal legitimacy | Operational drag, security risk for urgent fixes | Rejected |
| Multisig Council only | Simple | Single point of failure, weak social contract | Rejected |
| On-chain governance only (mandatory) | Cryptographic audit trail | Requires mature infra; bootstrapping friction | Deferred (hybrid off→on-chain path) |

---

## Impact

- **Process:** Clear gate checks for releases and parameter changes.
- **Security:** Bounded emergency powers reduce exploit windows.
- **Docs/Website:** Governance pages render from registries; ADR index displays status.
- **Tooling/CI:** Lints and schema checks run on registries and proposals.

---

## Rollout Plan

1. Publish this ADR and linked governance docs.
2. Initialize **Security Council (interim)** multisig; publish keys and policy.
3. Seed registries with maintained defaults and owners.
4. Wire CI:
   - Validate schema of all governance registries.
   - Block merges if thresholds or owner ACKs missing.
5. Announce process; open first **ParamChange** dry run (non-binding) to exercise pipeline.

---

## Security Considerations

- Emergency actions must be **narrow**, **logged**, and **time-bounded**.
- PQ rotations require **deprecation windows** and **compat testing**.
- Parameter deltas must remain within **safe ranges** in `params_registry.yaml`; CI enforces bounds.
- Responsible disclosure path documented in `docs/security/RESPONSIBLE_DISCLOSURE.md`.

---

## Operations & Tooling

- `governance/registries/*` are machine-validated; explorers/docs ingest them.
- Release tooling signs artifacts and links to proposal IDs.
- Website renders thresholds and roles from sources to avoid drift.

---

## Backwards Compatibility

- Existing devnet/testnet flows continue; new proposals add metadata but do not break clients.
- Feature-flagged upgrades allow opt-in during transition windows.

---

## Open Questions

- When to migrate voting fully on-chain and which contract suite to adopt?
- Whether to add **quadratic** or **delegated** voting in later phases?
- Threshold tuning after mainnet data accrues.

---

## Readiness Checklist

- [x] Roles and responsibilities documented
- [x] Thresholds & quorums defined per proposal type
- [x] Emergency scope/time bounds codified
- [x] Registries created with schemas & CI checks
- [x] Website/explorer integration plan noted
- [x] Review cadence defined

---

## Amendments

- *None yet.*

