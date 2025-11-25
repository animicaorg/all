# Governance Overview
_Params governance, protocol upgrades, and the safeguards around both._

This document describes **what can change**, **who can change it**, and **how** those changes are proposed, validated, executed, and audited. The design favors **safety-first, reversible-by-fork**, with clear separation between **parameter governance** (tuning dials) and **protocol upgrades** (new behavior).

---

## 1) Scope of Governance

### 1.1 Parameter Governance (no hard fork)
Parameters live in canonical specs and module configs (see `spec/params.yaml`, `spec/poies_policy.yaml`, `aicf/config.py`, `da/config.py`, `randomness/config.py`, `execution/config.py`, etc.). They can be changed without altering serialization or state layout.

**Examples (non-exhaustive):**
- **Consensus / PoIES**
  - Θ target & EMA retarget schedule
  - Γ (total work) caps and per-proof-type caps
  - Escort/diversity weights `q`
  - Nullifier TTLs and reorg depth limits
- **AICF & Economics**
  - Base payout rates, epoch length, fee splits
  - SLA thresholds (latency, QoS, traps ratio)
  - Slashing magnitudes and cooldowns
- **Data Availability**
  - Max blob size, erasure (k, n), namespaces
  - DAS sample sizes and p_fail target
- **Randomness**
  - Round lengths, reveal grace, VDF params
- **Execution / VM(Py)**
  - Gas tables/costs, refund caps, feature flags
  - Access-list size bounds & IO limits
- **Networking / RPC**
  - DoS limits, rate limits, CORS allowlists

> _Parameter changes must not break format compatibility or historical validity._

### 1.2 Protocol Upgrades (hard / soft fork)
Changes that affect **encoding**, **state layout**, or **consensus-critical behavior** require a versioned upgrade (see `docs/spec/UPGRADES.md`). These include:
- New transaction/header fields or CDDL schemas
- New proof kinds or modified ψ mapping semantics
- VM instruction set changes that alter execution results
- Fork-choice rules or retarget algorithm changes that are not representable as parameter updates

---

## 2) Governance Actors & Roles

- **Proposers**: Anyone (community) drafts a proposal; only **Governance Keyset** can publish on-chain parameter-change transactions or set upgrade gates.
- **Reviewers**: Core maintainers + domain stewards (consensus/VM/DA/AICF) provide technical risk assessments.
- **Auditors**: Independent reviewers for security & economics when risk > predefined threshold.
- **Signers**: Threshold keyset (e.g., `m-of-n`) controlling the **Params Multisig** and **Upgrade Multisig**.
- **Operators**: Client implementers, exchanges, infra partners coordinating rollouts.

> Keys & rotation policy: see `installers/signing/policies.md`.

---

## 3) Proposal Lifecycle

### 3.1 Parameter Governance Proposal (PGP)
1. **Draft (Off-chain)**
   - Motivation, exact diffs to parameter files, backtests/sims, risk level.
   - Links: related test vectors and notebooks.
2. **Public Review Window**
   - Minimum review period (e.g., 7 days) with comment resolution.
   - CI runs sims: consensus stability, fee/reward impacts, DAS probability, SLA sensitivity.
3. **Testnet Trial**
   - Apply to testnet; run for ≥ _N_ epochs.
   - Collect metrics (fork rate, p_fail, gas usage, fee market dynamics).
4. **Sign-off & Timelock**
   - Governance Keyset signs a **ParamsChange** tx:
     - Includes semantic version bump for `spec/params.yaml` (patch/minor).
     - Enforces a **timelock** (e.g., 48–168h) before activation.
5. **Activation**
   - Nodes read the signed parameter bundle, verify signature + registry hashes, then schedule activation at height/time.
6. **Postmortem**
   - Publish effects vs. expectations; adjust playbooks if needed.

### 3.2 Protocol Upgrade Proposal (PUP)
1. **RIP (Request for Implementation)**
   - Spec PRs for new CDDL/JSON-Schema, migration steps, and rollout gates.
2. **Reference Implementations**
   - Feature-flagged code paths; dual-run & shadow-mode where possible.
3. **Audits**
   - Security & economics (mandatory for consensus/VM changes).
4. **Testnet Epochs**
   - Canary + soak. Collect telemetry; dry-run rollback plan.
5. **Governance Vote & Timelock**
   - Upgrade Multisig sets activation gate (height/epoch).
6. **Mainnet Activation**
   - Client releases cut; operators upgrade well ahead of gate.
7. **Finalization**
   - Announce success; update `docs/spec/UPGRADES.md`.

---

## 4) On-Chain Mechanics

### 4.1 Signed Parameter Bundles
- Canonical object: `ParamsBundle` with:
  - `version`, `effective_height`, `files = {path: sha256}` (e.g., `spec/params.yaml`, `spec/poies_policy.yaml`)
  - `signature` by **Params Multisig**
- Nodes:
  - Verify signatures & file digests.
  - Enforce **effective_height ≥ head + timelock_min**.
  - Pin the bundle hash in local DB and expose via `rpc/methods/chain.py` (e.g., `chain.getParams` shows current and pending).

### 4.2 Upgrade Gates
- Object: `UpgradeGate` with:
  - `feature_flag`, `activation_height`, `grace_window`
  - `signature` by **Upgrade Multisig**
- Clients ship code paths; the gate toggles canonical behavior at activation height.

---

## 5) Safety Rails

- **Timelocks** on all parameter and upgrade activations.
- **Rollback Plan** documented per proposal; for params, a revert bundle; for upgrades, emergency off-switch (feature flag) if behavior is backward compatible.
- **Invariant Checks in CI**:
  - Θ/Γ bounds, nullifier TTL ≥ minimum
  - DA p_fail ≤ target
  - Gas table monotonicity (no zero-cost hazards)
  - SLA/penalty magnitudes within policy bounds
- **Transparency**:
  - All proposals, votes, signatures, and artifacts (hashes, testnet metrics) are published.
- **Key Management**:
  - Short-lived tokens; rotation schedule; hardware-backed signers.

---

## 6) Versioning

- **Parameters**: Semantic versioning of the parameter set (e.g., `params v1.4.2`), separate from client versions.
- **Protocol**: Feature flags gated by heights; `chain.getParams` and `chain.getHead` expose `features_active` for a given height.

---

## 7) Example Templates

### 7.1 Parameter Change (excerpt)
```yaml
title: "Reduce Θ retarget aggressiveness (stability)"
changes:
  spec/params.yaml:
    difficulty:
      ema_alpha: 0.85 -> 0.80
      clamp_up: +10% -> +8%
rationale: |
  Reduces oscillations under bursty AI/Quantum submissions.
risk: LOW
testnet_results:
  fork_rate: -12%
  interval_jitter: -9%
activation:
  effective_height: 1_234_567
  timelock_hours: 72
signatures:
  - signer: animica-governance-1
    sig: 0x…

7.2 Protocol Upgrade Gate (excerpt)

feature_flag: "vm_py.ir_v2"
activation_height: 1_500_000
grace_window: 2048
audits:
  - org: ExampleSec
    report_sha256: 8f…ab
signatures:
  - signer: animica-upgrade-2
    sig: 0x…


⸻

8) Off-Chain Coordination
	•	Release Notes: Summarize expected effects; link to docs/CHANGELOG.md.
	•	Client Binaries: Signed installers (see installers/); publish checksums.
	•	Infra Readiness: Indexers, explorers, and wallets dry-run on testnet before activation.

⸻

9) Frequently Asked Questions

Q: Can parameter changes de facto behave like forks?
A: They must not. Any param change that alters consensus outcomes in a way not modeled by existing formulas is a PUP, not a PGP.

Q: Who can block an unsafe change?
A: Auditors can publicly tag a RED risk; signers are policy-bound not to sign until addressed. Node implementers may also refuse to ship unsafe changes.

⸻

10) References
	•	docs/spec/UPGRADES.md — versioning & fork process
	•	docs/economics/* — payout & fee math
	•	docs/security/* — supply chain, disclosures, dos defenses
	•	spec/* — canonical schemas & parameters

