# Animica Governance · README

> **Purpose.** This document explains the **scope**, **principles**, and **boundaries** of governance in Animica: what parameters and processes the community can change, how changes flow from proposal → review → activation, and what is explicitly **not** governed.

See also:
- `docs/governance/OVERVIEW.md` — high-level model and actors
- `docs/governance/PARAMS.md` — governable parameters & bounds
- `docs/governance/PQ_POLICY.md` — post-quantum rotation policy
- `docs/governance/COMMUNITY.md` — proposals, RFCs, signaling

---

## 1) Scope — what governance *does* control

Governance can modify configuration and policy **that does not require breaking consensus history** and that is explicitly designed to be evolved by parameter or scheduled upgrade. Concretely:

### Consensus & economics (parameterized)
- **PoIES policy knobs**: per-proof ψ mapping weights, **caps**, escort rules, and total **Γ (Gamma)** cap (within safety bounds).
- **Θ (Theta) retarget schedule**: EMA windows and clamp bands (bounded; see `docs/spec/DIFFICULTY_RETARGET.md`).
- **Fork-choice limits**: reorg depth caps and weight tie-break rules (bounded).
- **Block/tx limits**: byte and gas ceilings, intrinsic gas tables for tx kinds (via table revisions).
- **Fee market policy**: min-fee floor dynamics, surge multipliers, refund caps (where modeled as params).
- **Issuance & splits**: treasury slice, AICF allocation caps, epoch accounting parameters (within published rails).

### Cryptography & security posture
- **PQ algorithm policy**: allowed signature/KEM suites, deprecations, thresholds; **alg-policy Merkle root** pinning and rotation cadence.
- **ZK circuit registry**: allowlist of circuit IDs, verifying keys (VK) pinning, cost caps.
- **Randomness beacon**: VDF parameters, round lengths, QRNG mixing toggle (safety-bounded).

### Networking & operations (policy)
- **P2P handshake features**: required transcript bindings (e.g., alg-policy root), rate-limit defaults.
- **Seed lists & discovery**: seed set, rotation cadence, liveness requirements.
- **RPC rate limits**: per-method/default budgets and CORS allowlists for public endpoints.

### Upgrades & scheduling
- **Feature flags**: on/off and staged rollouts (testnet → mainnet).
- **Network upgrades**: version gates with activation heights/epochs, using time-locked plans.

> All governable items are enumerated with **min/max rails** in `docs/governance/PARAMS.md`. Changes outside rails require a **major upgrade** with additional review.

---

## 2) Principles — how governance makes decisions

1. **Safety first** — Never trade safety for speed. Mandatory security review, test vectors, and replay-protection analysis precede activation.
2. **Minimalism** — Prefer narrow parameter changes over broad rewrites. Ship deltas with explicit, documented blast radius.
3. **Transparency** — Proposals, rationales, and test artifacts are public. Reproducible builds and hashes are required.
4. **Backward empathy** — Deprecate with windows, feature flags, and tooling to migrate (SDK/wallet/releases).
5. **Determinism & verifiability** — Changes must be objectively testable (unit/integration/fuzz/bench). Gate on CI green.
6. **User sovereignty** — No governance action can seize keys, reassign balances, or censor specific users.
7. **Neutrality** — Protocol does not pick application winners. No app-specific exceptions.
8. **Least privilege** — Grant only the parameters strictly necessary; keep cryptographic roots and circuit VKs pinned with signatures.
9. **Upgrade abstinence** — If in doubt, do not change. Prefer testnet experiments to mainnet parameter swings.

---

## 3) What is **not** governed (hard boundaries)

- **Private keys, accounts, balances** — Cannot be altered by governance.
- **Transaction validity/execution semantics** outside the published deterministic VM and specs — No ad hoc exceptions.
- **Per-transaction inclusion or ordering** — Miners/validators retain autonomy within protocol rules; governance cannot force inclusion of specific txs.
- **Retroactive state edits** — No state rewrites except in documented, emergency consensus fixes requiring super-majority and explicit social consensus (last-resort).
- **Off-chain services** (explorers, third-party wallets, exchanges) — Out of scope beyond published APIs.
- **Application-level business logic** — Smart contract policies are governed by their own on-chain mechanisms, not by protocol governance.

---

## 4) Proposal lifecycle (summary)

1. **Idea & RFC (off-chain)**  
   Open a governance RFC (template in `docs/governance/COMMUNITY.md`) with: motivation, spec deltas, parameter rails, safety analysis, migration plan, and test plan.

2. **Temperature check (off-chain signal)**  
   Community discussion, rough consensus, and maintainers’ feasibility review. Draft PRs to specs and code behind **feature flags**.

3. **Testnet trial (on-chain flag)**  
   Roll out to testnet with metrics, benchmarks, and failure thresholds. Iterate until stability criteria pass.

4. **Formal proposal (on-chain)**  
   Submit a governance proposal object (e.g., `ParamChange`, `UpgradePlan`, `PQPolicyUpdate`, `ZkVkPin`) with:
   - New values (within rails) and effective **epoch/height**.
   - **Hashes** of referenced artifacts (spec docs, VKs, alg-policy root).
   - **Timelock** (minimum delay) and **quorum/threshold** requirements.

5. **Voting window**  
   Votes tallied per governance rules (quorum, super-majority for security-sensitive items). Off-chain mirrors for transparency.

6. **Timelock & activation**  
   After a successful vote and timelock, the change **activates at a predetermined height/epoch**. CI enforces version gates; nodes warn early.

7. **Post-activation review**  
   Monitor metrics and revert-guard rails. Publish postmortem if anything deviates.

> Exact data models and thresholds live in `docs/governance/OVERVIEW.md` and `docs/governance/PARAMS.md`.

---

## 5) Security gates & artifacts

Every accepted change must include:

- **Spec PRs** with clear diffs (e.g., `docs/spec/*`, `spec/*`).
- **Test vectors** updated (`spec/test_vectors/*`, module tests).
- **Reproducible builds** (toolchain pins, lockfiles, artifact hashes).
- **Rollout plan** (testnet → mainnet), with abort criteria.
- **Communication plan** (release notes, SDK/wallet bumps, ops runbooks).

For cryptographic roots (PQ alg-policy, ZK VKs), governance pins **hashes and signatures**. See `zk/registry/*` and `docs/security/SUPPLY_CHAIN.md`.

---

## 6) Emergency procedures (last resort)

- **Scope**: Critical consensus bugs, key cryptography breaks, or chain-halt conditions.
- **Flow**: Coordinated disclosure → Hotfix behind flag → Testnet validation → Short timelock → Activation.  
  Any retroactive actions require extraordinary thresholds and full disclosure.

---

## 7) Roles (illustrative; exact roles defined in OVERVIEW)

- **Proposers**: Any community member or designated working group.
- **Reviewers**: Maintainers and domain leads (consensus, VM, DA, PQ, ZK, P2P).
- **Voters**: Defined by network governance (token-based, rep-based, or hybrid — see OVERVIEW).
- **Operators**: Node runners and service maintainers executing rollouts.

---

## 8) Quick links

- Parameters & bounds: `docs/governance/PARAMS.md`  
- PQ rotations: `docs/governance/PQ_POLICY.md`  
- Proposal workflow: `docs/governance/COMMUNITY.md`  
- Release process: `docs/dev/RELEASES.md`  
- Security & supply-chain: `docs/security/SUPPLY_CHAIN.md`

---

*This README is a living document. Changes to scope or boundaries themselves must go through the governance process described above.*
