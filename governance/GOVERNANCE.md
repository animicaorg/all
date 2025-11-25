# Animica Governance · Constitution

> This Constitution defines **roles, powers, checks, and balances** for changing Animica’s protocol parameters and activating upgrades. It complements:
> - `governance/README.md` (scope & principles)
> - `docs/governance/OVERVIEW.md` (actors & flows)
> - `docs/governance/PARAMS.md` (governable parameters & bounds)
> - `docs/security/SUPPLY_CHAIN.md` (build/signing requirements)

---

## 0. Preamble

Animica governance exists to safely evolve protocol parameters and version-gated features while protecting user sovereignty, chain safety, and reproducibility. Changes are executed through transparent proposals, objective tests, and cryptographically pinned artifacts.

---

## 1. Definitions

- **Network** — A running Animica chain (e.g., mainnet, testnet).
- **Proposal** — A structured governance object submitted on-chain to request a change.
- **Rails** — Min/max bounds and invariants that constrain governable parameters.
- **Activation Height/Epoch** — Canonical block height or epoch when an accepted proposal takes effect.
- **Voting Module** — Pluggable mechanism that determines voter set and voting power (token-weighted, reputation, 1p1v, or hybrid), and enforces quorum/threshold rules.
- **Guardians** — Security council with strictly limited, time-boxed emergency powers.
- **Maintainers** — Domain leads who review specs and code, manage releases and feature flags.
- **Operators** — Node and infra operators who deploy releases and enforce rollout plans.
- **Custodians** — Key holders for registries pinned by hash (e.g., PQ alg-policy, ZK VKs).

---

## 2. First Principles

1. **Safety first** — No change that measurably increases consensus risk.
2. **Minimalism** — Prefer narrow parameter moves over semantic rewrites.
3. **Transparency** — Public artifacts, hashes, CI runs, and rationale.
4. **Determinism** — Spec + vectors + reproducible builds precede activation.
5. **Neutrality** — No app- or entity-specific exceptions.
6. **User sovereignty** — Keys, balances, and arbitrary state edits are out of scope.
7. **Least privilege** — Roles have narrowly scoped authorities and time-limited actions.

---

## 3. Roles & Powers

### 3.1 Proposers
- **Who**: Any community member meeting minimal submission criteria.
- **Powers**: Submit proposals of allowed types with required artifacts.
- **Limits**: Must reference rails, include activation delay, and pass validation.

### 3.2 Reviewers (Maintainers & Domain Leads)
- **Who**: Consensus/VM/DA/PQ/ZK/P2P maintainers.
- **Powers**: Technical review, spec/code PR approval, test/vectors sign-off.
- **Limits**: Cannot force activation; only certify readiness.

### 3.3 Voting Module (Voters)
- **Who**: Defined per network (token-weighted, reputation, delegated, hybrid).
- **Powers**: Accept or reject proposals; choose options where applicable.
- **Limits**: Bound by quorum/thresholds; cannot exceed rails.

### 3.4 Guardians (Security Council)
- **Who**: 2–7 independent signers, publicly disclosed.
- **Powers** *(emergency only)*: Delay or veto activation **within** the timelock window if a critical, well-substantiated safety issue arises.
- **Limits**: One-time delay (e.g., ≤ 14 days) or veto requiring supermajority (e.g., ≥ 2/3). Must publish incident report. No power to activate changes.

### 3.5 Custodians (Registry Signers)
- **Who**: PQ policy & ZK VK registry maintainers.
- **Powers**: Co-sign registry updates (alg-policy root, VK pins) matching the proposal content.
- **Limits**: Signature binds to hashes already embedded in accepted proposals.

### 3.6 Operators
- **Who**: Node runners, service maintainers.
- **Powers**: Execute rollout plans; refuse unsafe binaries.
- **Limits**: Must follow activation schedule and publish deviations.

---

## 4. Proposal Types & Default Thresholds

> Exact numeric defaults may be overridden per network genesis; bounds live in `docs/governance/PARAMS.md`.

| Type                 | Scope Examples                                                                 | Quorum | Threshold | Min Timelock | Extra Guards                                   |
|----------------------|----------------------------------------------------------------------------------|:------:|:--------:|:------------:|-----------------------------------------------|
| **ParamChange**      | PoIES weights/caps Γ, Θ EMA/clamps, fee policies, mempool limits                | 20%    | >50%     | ≥ 7 days     | Within rails; CI green; updated vectors        |
| **UpgradePlan**      | Version gate enabling new semantics/features                                     | 33%    | ≥66%     | ≥ 14 days    | Release sigs; roll-back plan; operators ack    |
| **PQPolicyUpdate**   | PQ alg allowlist/deprecations; alg-policy Merkle root                           | 33%    | ≥66%     | ≥ 14 days    | Custodian co-sigs on root; wallet/sdk pinned   |
| **ZkVkPin**          | Add/replace verifying keys (VK); circuit allowlist                              | 20%    | >60%     | ≥ 7 days     | VK hashes & provenance; cost caps; tests       |
| **SeedListUpdate**   | P2P seed rotation                                                                | 10%    | >50%     | ≥ 3 days     | Liveness checks published                      |
| **Treasury/AICF**    | Epoch splits within bounds; payout params                                        | 25%    | ≥60%     | ≥ 10 days    | Economics report; runway & risk assessment     |
| **EmergencyHotfix**  | Critical consensus/Crypto fix behind flag (see §8)                               | 33%    | ≥66%     | ≥ 24–72 h    | Guardians may delay once; formal postmortem    |

*Quorum/thresholds are evaluated at a head snapshot defined in the proposal.*

---

## 5. Checks & Balances

- **Dual control**: Acceptance by voters **and** readiness certification by maintainers (CI + vectors) is required.
- **Timelock**: All non-emergency changes wait a minimum delay before activation.
- **Guardian brake**: One-time delay or veto (with supermajority) inside timelock, limited scope and duration.
- **Rails enforcement**: Param changes are machine-validated against bounds; out-of-rails proposals are rejected at submission.
- **Reproducibility**: Binaries released for activation must match recorded hashes; supply-chain signatures are verified by operators.
- **Post-activation audit**: Metrics & regressions tracked; revert rails defined for fast disable of feature flags if needed.

---

## 6. Lifecycle

1. **RFC (off-chain)** — Problem statement, rationale, spec diffs, rails mapping, migration/rollout and test plans.
2. **Draft PRs** — Specs (`docs/spec/*`) and code behind feature flags; unit/integration/bench tests; vectors updated.
3. **Testnet Trial** — Stabilize under load; publish reports and sign-off checklist.
4. **On-chain Proposal** — Submit canonical envelope (see §10) with hashes of all artifacts and an activation schedule.
5. **Vote** — Voting module enforces quorum/thresholds; off-chain mirrors for transparency.
6. **Timelock** — Minimum delay allows review; guardians can intervene (limited).
7. **Activation** — At scheduled height/epoch; operators roll out signed binaries; CI verifies runtime invariants where applicable.
8. **Postmortem** — Publish outcomes, performance, and any deviations.

---

## 7. Voting Module Requirements

- **Pluggable**: Token-weighted, reputation, delegated, 1p1v, or hybrid; module is declared per network.
- **Snapshotting**: Voting power snapshot at proposal start (or defined block).
- **Sybil resistance**: Documented mechanism per module.
- **Delegation**: Optional; transparent and revocable.
- **Abstain**: Counted toward quorum but not toward passage where configured.
- **Dispute window**: Short window to challenge tally errors before timelock elapses.

---

## 8. Emergencies

- **Scope**: Critical consensus bugs, cryptographic breaks, or chain halts.
- **Process**:
  1. Coordinated disclosure to guardians & maintainers.
  2. Hotfix prepared behind feature flag; testnet proof.
  3. **EmergencyHotfix** proposal with shortened timelock (≥ 24–72 h).
  4. Guardians may delay once (≤ 14 days) if risk persists; must publish reasons.
  5. Full postmortem, including remediation and prevention actions.

*Emergencies cannot change balances or arbitrary state.*

---

## 9. Conflicts of Interest & Conduct

- **Disclosure**: Reviewers/guardians must disclose material conflicts (employment, grants, investments) related to a proposal.
- **Recusal**: Conflicted members abstain from certifications or guardian actions on that item.
- **Civility & records**: Discussions remain public, archived; harassment is not tolerated.

---

## 10. Proposal Envelope (Canonical Schema)

All proposals use a canonical envelope (CBOR/JSON) with deterministic field ordering:

```jsonc
{
  "proposal_id": "hex32",
  "type": "ParamChange|UpgradePlan|PQPolicyUpdate|ZkVkPin|SeedListUpdate|Treasury|EmergencyHotfix",
  "title": "string",
  "summary": "string",
  "network": "animica:1",
  "activation": { "height": 123456, "timelock_blocks": 20160 },
  "rails_ref": "docs/governance/PARAMS.md#section",
  "changes": { /* type-specific payload; param deltas or version gates */ },
  "artifacts": [
    { "kind": "spec", "uri": "ipfs://...", "sha3_512": "0x..." },
    { "kind": "vectors", "uri": "ipfs://...", "sha3_512": "0x..." },
    { "kind": "release", "uri": "https://...", "sha3_512": "0x..." }
  ],
  "registry": {
    "pq_alg_policy_root": "0x...",   // when applicable
    "zk_vk_hashes": ["0x...", "0x..."] // when applicable
  },
  "voting": { "quorum_bps": 3300, "threshold_bps": 6600, "snapshot_height": 120000 },
  "signatures": [
    { "role": "proposer", "alg": "dilithium3", "sig": "0x..." },
    { "role": "maintainer_ack", "domain": "consensus", "sig": "0x..." }
  ],
  "metadata": { "discussion": "https://forum...", "rationale": "..." }
}

Hashing and domain-separation tags are specified in docs/spec/ENCODING.md. Envelope instances are stored on-chain and mirrored off-chain.

⸻

11. Transparency & Records
	•	Public ledger: Proposals, votes, outcomes, and activation receipts are indexable via Explorer and APIs.
	•	Artifact pinning: URIs + hashes are immutable; mirrors encouraged.
	•	Releases: Reproducible; SBOM + signatures published per docs/security/SUPPLY_CHAIN.md.

⸻

12. Amendments to this Constitution
	•	Standard amendment: UpgradePlan with quorum ≥ 33% and threshold ≥ 66%, timelock ≥ 14 days.
	•	Safety clauses (sections §1–§3, §5, §8, §12): require quorum ≥ 50% and threshold ≥ 75%.
	•	Amendments must include a diff and clear redlines.

⸻

13. Ratification & Bootstrapping
	•	Ratified by the genesis authority (or previous governance) via an UpgradePlan that pins this document’s hash.
	•	Initial roles (guardians, custodians, maintainers) and Voting Module are declared in genesis parameters and can only be updated via proposal.

⸻

14. Non-Governable Boundaries (for avoidance of doubt)

Governance cannot:
	•	Edit historical state (except through formal emergency consensus fix with extraordinary thresholds and public postmortem).
	•	Seize or reassign user funds/keys.
	•	Force inclusion/exclusion of specific transactions beyond protocol rules.

⸻

15. Enforcement
	•	Nodes enforce rails and activation gates at runtime.
	•	RPC/mempool respects governance-driven limits once active.
	•	Violations (e.g., binaries not matching pinned hashes) should be rejected by operators; Explorer/CI mark divergence.

⸻

Appendix A — Type-Specific Change Payloads (Illustrative)
	•	ParamChange: { "poies": { "weights": {...}, "caps": {...}, "gamma_cap": 12345 }, "theta": { "ema": 0.2, "clamp": [0.5, 2.0] }, "fees": {...} }
	•	UpgradePlan: { "version": "vX.Y.Z", "features": ["vm_py_v2","da_erasure_v1"], "gates": {"min_node": "sha3_512:0x..."} }
	•	PQPolicyUpdate: { "alg_root": "0x...", "deprecations": ["sphincs_shake_128s@date"], "effective_after": 120960 }
	•	ZkVkPin: { "circuits": [{"id": "embedding/poseidon@1", "vk_hash": "0x...", "max_cost": 500000}], "allowlist_delta": ["+embedding/poseidon@1"] }

⸻

Hash of this file at ratification is recorded in the chain’s governance metadata.
