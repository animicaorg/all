# Animica Governance · Roles & Powers

> Defines the **scope, authorities, selection, and accountability** of core governance actors:
> **Token Voters, Maintainers, Security Council, and Editors**.
> This document complements:
> - `governance/GOVERNANCE.md` (constitution; processes & thresholds)
> - `governance/CHARTER.md` (mission, values, Conflict-of-Interest policy)
> - `docs/security/RESPONSIBLE_DISCLOSURE.md` (vuln reporting)
> - `docs/spec/*` (technical sources of truth)

---

## 0) Quick Matrix (RACI)

| Decision / Action                                  | Token Voters | Maintainers | Security Council | Editors |
|---                                                  |:-----------:|:-----------:|:----------------:|:------:|
| **Protocol upgrade activation**                     | **A/R**     | **R/C**     | C (emergency rails) | I    |
| **Chain parameter change (Θ, Γ caps, fees)**        | **A/R**     | C           | C (emergency bounds) | I    |
| **PQ policy rotation / deprecation**                | **A/R**     | R (impl)    | C                 | I      |
| **ZK VK registry updates (add/replace)**            | **A/R***    | R (merkle/build) | C (emergency revoke) | I |
| **Emergency action (circuit breaker / hotfix gate)**| I           | C           | **R/A (time-boxed)** | I |
| **Tagged release / artifact signing**               | I           | **R/A**     | C (security hotfix) | I |
| **Docs/spec editorial (non-semantic)**              | I           | C           | I                 | **R/A** |
| **Seed list policy / rotation**                     | A/R         | R           | C                 | I      |

Legend: **R** = Responsible, **A** = Accountable, **C** = Consulted, **I** = Informed.  
\* For VK updates, voters ratify **defaults**; testnet/devnet may allow maintainer-only flow per `GOVERNANCE.md`.

---

## 1) Token Voters

**Who**: Holders of governance tokens (or delegated voting power) meeting Sybil-resistance and eligibility rules in `GOVERNANCE.md`.

**Authorities**
- Approve **protocol upgrades** and **parameter changes** (Θ schedule, Γ caps, fee rules, policy roots).
- Ratify **PQ rotations** and **ZK VK registry** default sets.
- Elect / recall **Security Council** members and confirm **Maintainer** promotions where required.
- Approve **treasury / programmatic payouts** policies (if applicable).

**Constraints**
- Cannot authorize arbitrary state edits or ex-post wealth transfers. See constitution §Safety.
- Quorum & thresholds defined per network (e.g., Q=20%, Supermajority=66%); time-locks per `UpgradePlan`.

**Obligations**
- Follow **CoI disclosure** in `CHARTER.md §3`.
- Avoid bribery / vote-buying arrangements that create undisclosed CoI.
- Participate in **open RFC** discussion prior to voting.

**Mechanics**
- Voting systems may include Single-choice, Approval, or Ranked options; delegation supported.
- Snapshot height and **artifact hashes** must be pinned in each proposal envelope.

---

## 2) Maintainers

**Who**: Technical stewards across repositories (core, consensus, proofs, DA, VM, RPC, wallet, SDK, docs).

**Authorities**
- Review & merge code, manage CI, **tag releases**, and **sign reproducible artifacts**.
- Implement **governance-approved changes** (spec → code), including parameterizations.
- Operate **VK/PQ registries** build pipelines (not policy decisions).
- Execute guarded **hotfix releases** under the emergency rails (with Security Council approval when invoked).

**Constraints**
- Two-person rule on protected branches; **reproducible builds** & signed SBOMs required.
- No unilateral mainnet semantic changes outside rails. All semantic diffs require proposal linkage.
- Keys stored in HSM or hardware wallets; rotation & break-glass documented.

**Obligations**
- **CoI compliance** (`CHARTER.md`), public rationale on contentious merges.
- Maintain **release notes**, vectors, and upgrade guides.
- Run postmortems for incidents involving maintainer actions.

**Selection & Recall**
- Merit-based nomination; confirmed by voters or existing maintainers per constitution.
- Recall via voter motion or security council motion with voter affirmation.

---

## 3) Security Council

**Who**: Odd-number set (e.g., 5/7/9) elected by Token Voters; acts as **Guardians** for time-boxed emergencies.

**Mandate (Emergency Rails)**
- Activate **circuit breakers** (e.g., pause new upgrades, raise floor fee bounds) within tight, pre-approved envelopes.
- Approve **hotfix releases** addressing critical vulns when delay would cause material harm.
- Temporarily **revoke VKs / PQ algs** proven unsafe; publish rationale and rollback plan.

**Constraints**
- Multi-sig threshold (e.g., M-of-N ≥ 2/3); **maximum duration** (e.g., 72h) before voter confirmation is required.
- Actions logged with **attested justification**, diff links, and expiration.
- **No** powers to alter balances, confiscate funds, or change chain history.

**Obligations**
- Highest **CoI standard** (mandatory recusal on material conflicts).
- Coordinate with maintainers; ensure independent verification before invoking rails.
- Publish **after-action reports**.

**Selection & Removal**
- Elected by Token Voters; staggered terms (e.g., 1–2 years).  
- Removal via vote; immediate suspension possible on breach (with rapid voter review).

---

## 4) Editors

**Who**: Documentation & specification editors (non-semantic maintainers).

**Authorities**
- Curate and maintain **docs/specs**: structure, terminology, examples, indexing.
- Approve **non-semantic** edits (clarity, wording, references, diagrams).
- Gate **semantic** spec changes behind maintainer + governance sign-off.

**Constraints**
- Must tag PRs that change semantics; cannot merge such PRs without maintainer ACK & proposal linkage.
- Enforce **style guide** (`docs/STYLE_GUIDE.md`) and glossary consistency.

**Obligations**
- Ensure docs reflect **activated** protocol versions; maintain deprecation notes.
- Coordinate with SDKs & website on outward-facing changes.

**Selection**
- Appointed by maintainers; optionally ratified by voters on mainnet.
- Removal for cause (pattern of semantic drift, policy violations).

---

## 5) Cross-cutting Rules

**CoI & Ethics**  
All roles comply with `governance/CHARTER.md` §3–§5. Disclose, recuse, abstain as applicable.

**Transparency & Records**  
- Proposal artifacts (hashes), ballots, approvals, emergency actions, and releases are **public** and indexed.
- Meeting notes for governance-affecting calls are published with redactions only for active vulnerabilities.

**Keys & Signatures**  
- Hardware-backed keys; short-lived tokens for CI; cosign/Sigstore or equivalent for artifacts.
- Rotation schedules and emergency revocation are documented & rehearsed.

**Termination & Appeals**  
- Due-process path in `GOVERNANCE.md`: warning → suspension → removal; appeals to neutral triad.

---

## 6) Role Interfaces (APIs & Repos)

- **Voters** → `proposals/*`, on-chain module / off-chain voting portal, OpenRPC methods for parameter query.
- **Maintainers** → repos under `core/`, `consensus/`, `proofs/`, `da/`, `vm_py/`, `rpc/`, `wallet-*`, `sdk/*`.
- **Security Council** → `governance/rails/*` configs; multisig keys; runbooks in `docs/ops/RUNBOOKS.md`.
- **Editors** → `docs/*`, `website/*`, spec subtrees; CI link checks and schema validation.

---

## 7) Example Thresholds (Normative per-network; see `GOVERNANCE.md`)

- **Protocol Upgrade**: Q ≥ 20%, Yes ≥ 66%, Timelock ≥ 7 days.  
- **Param Change**: Q ≥ 15%, Yes ≥ 60%, Timelock ≥ 3 days.  
- **PQ/ZK Default Set**: Q ≥ 15%, Yes ≥ 60%, staged rollout (testnet → mainnet).  
- **Emergency Rails (Council)**: ≥ 2/3 signatures, auto-expire ≤ 72h unless voter-confirmed.

---

## 8) Versioning

This document evolves via the normal proposal process. Hash of latest accepted version may be recorded on-chain or in release metadata for integrity.

