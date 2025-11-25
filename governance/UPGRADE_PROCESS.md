# Upgrade Process (Semantic Versions, Feature Gates, Rollbacks)

**Status:** Adopted  
**Applies to:** Node (core/consensus/p2p/da/exec), RPC, SDKs, Wallets, Services  
**Related:** `docs/spec/UPGRADES.md`, `governance/PROCESS.md`, `governance/THRESHOLDS.md`, `governance/EMERGENCY_PROCEDURES.md`, `docs/dev/RELEASES.md`

This document defines how protocol and software upgrades are introduced, activated, and—if necessary—rolled back. It formalizes versioning, feature gating, compatibility rules, and operator runbooks.

---

## 1) Semantic Versioning

We use **SemVer** for all components: `MAJOR.MINOR.PATCH(+build)`.

- **MAJOR**: May include **consensus changes** (hard/soft forks), object format changes, or breaking RPC/ABI. Requires a governance **Upgrade** proposal (see `governance/PROCESS.md`) and thresholds in `governance/THRESHOLDS.md`.
- **MINOR**: New features behind **feature gates**; backward-compatible RPC/SDK additions; performance work; *no* consensus rule changes unless disabled by default behind a gate. Requires **Policy/Upgrade** depending on scope.
- **PATCH**: Bug fixes, DoS hardening, perf or logging; no externally visible breakage.

> All consensus-affecting deltas **must** be guarded by a feature gate (off-by-default until activation is ratified).

### Version Negotiation (P2P/RPC)
- **P2P**: advertise `{proto_version, features}` during HELLO (see `docs/spec/P2P.md`); reject peers outside min-supported.
- **RPC/OpenRPC**: include `semver` and `features` in `/openrpc.json` and `/rpc.version` endpoints; clients adapt.

---

## 2) Release Channels & Branching

- **main** → nightly builds (`-dev` pre-release tag).
- **release/x.y** → candidates `x.y.0-rcN`, then `x.y.0`.
- **hotfix/x.y.z** → targeted patches for critical fixes.

Signed artifacts & SBOMs are required (see `docs/security/SUPPLY_CHAIN.md`).

---

## 3) Feature Gates

All new behavior ships **disabled by default** behind a declarative gate:

```yaml
# core/config.yaml (excerpt)
feature_gates:
  consensus:
    poies_v2: { enabled: false, activation: { method: "height", value: 1_234_567 } }
    zk_verify_v1: { enabled: false, activation: { method: "vote", proposal_id: "UP-2024-09-12" } }
  mempool:
    rbf_percent_threshold: { enabled: true, value: 12 }
  rpc:
    receipts_v2: { enabled: false }

Gate Classes
	•	Consensus gates: change block/header validity, scoring, retarget, state transition rules.
	•	Mempool/DoS gates: admission rules, rate limits; non-consensus but network-critical.
	•	RPC/ABI gates: new methods/fields; must remain backward compatible until deprecation window expires.
	•	Capability gates: syscalls (AI/Quantum/DA/zk/random); can be toggled per-network.

Activation Methods
	•	Height-based (activation.method=height): deterministic on-chain switch at block N.
	•	Time-based (method=time): UTC timestamp; requires liveness checks and sufficient node majority.
	•	Vote-based (method=vote): governance proposal passes → council signs activation record.
	•	Canary/Testnet-first: must precede mainnet activations by ≥2 weeks unless emergency.

⸻

4) Compatibility & Deprecation
	•	Wire & Storage Compatibility
	•	Minor/patch releases must support reading prior on-disk formats (or provide auto-migration).
	•	P2P must accept previous minor within two minor versions when safe.
	•	RPC/SDK
	•	New fields are additive. Removal requires a two-minor deprecation window and docs changelog.
	•	Proof/DA/ABI Schemas
	•	Changes are version-tagged; verifiers must continue to accept old schema until sunset date.

Deprecation notices are tracked in docs/CHANGELOG.md and docs/rpc/EXAMPLES.md.

⸻

5) Upgrade Workflow
	1.	Proposal Draft (R&D / Maintainers)
	•	Design doc, risks, gate class, activation method, rollout & rollback plan.
	2.	Review & Testing
	•	Unit/integration/property tests; cross-impl vectors; testnet soak; reproducible builds.
	3.	Governance Vote
	•	Proposal type: Upgrade, ParamChange, or Policy (see governance/PROPOSAL_TYPES.md).
	•	Thresholds per governance/THRESHOLDS.md.
	4.	Release Candidate(s)
	•	x.y.0-rcN, signed artifacts, operator dry-run guides.
	5.	Staged Activation
	•	Canary nets → public testnet → mainnet height/time once adoption ≥ 2/3 of stake or nodes.
	6.	Post-Activation Monitoring
	•	Metrics & dashboards (docs/dev/METRICS.md), error budgets, rollback guardrails.
	7.	Close-Out
	•	Merge postmortem notes; update docs/specs; declare GA.

⸻

6) Operator Runbook (Safe Path)
	•	Preflight
	•	Backup DB (snapshot), verify chainId/network, read release notes.
	•	Validate signatures and checksums.
	•	Install
	•	Stop node; install binaries or containers; apply config changes.
	•	Restart; verify chain.getHead and version endpoints.
	•	Activation Awareness
	•	If height/time activation is scheduled, ensure clocks are sane and peers updated.
	•	Observability
	•	Watch fork rate, block time λ, mempool Q, P2P peer health, consensus errors.

⸻

7) Rollback Policy

Rollback safety depends on whether a new consensus rule was already activated and blocks produced.

7.1 Pre-Activation Rollback (Safe)
	•	If the gate is not yet active (future height/time), operators may revert to previous binary/config.
	•	Publish a signed advisory and hotfix disabling the gate.

7.2 Post-Activation Rollback (Consensus Impact)
	•	If new rules have already finalized blocks:
	•	Soft mitigation: ship hotfix that rejects new-rule paths going forward while accepting existing chain—no reorg.
	•	Hard rollback: requires a governance Emergency followed by normal Upgrade vote to coordinate a bounded reorg or flag-day revert. See governance/EMERGENCY_PROCEDURES.md.
	•	Any state schema migration must provide:
	•	Down-migration path or compat shim; otherwise, rollback may be limited.

7.3 Abort Window
	•	For height activations, we maintain an abort window of K blocks (default: 720) where nodes accept an abort flag signed by Council that defers activation by ΔH.

⸻

8) Feature Gate Design Rules
	•	Idempotent & Reversible: toggling on/off should not corrupt state. If not possible, document why.
	•	Deterministic: all nodes derive the same activation condition (height/time/vote).
	•	Config Namespacing: feature_gates.consensus.* vs non-consensus keys to avoid operator error.
	•	Metrics: each gate emits an activation metric with {gate, network, height} labels.

⸻

9) Data & Param Migrations
	•	Params: chain params updates live in spec/params.yaml → versioned; changes require ParamChange.
	•	State: schema migrations must be:
	•	Online (incremental) or Offline (one-shot).
	•	Include time/IO estimates and recovery steps.
	•	Proof/Policy Roots (PQ/zk/DA): rotate via Policy proposal; pin VK roots & alg-policy hashes.

⸻

10) Documentation & SBOM
	•	Every release: update docs/CHANGELOG.md, OpenRPC schema, and any affected specs.
	•	Produce SBOM & reproducible build recipe hashes; store with artifacts.

⸻

11) Examples

11.1 Height-Based Consensus Change
	•	Gate: consensus.poies_v2
	•	RC deployed on testnet; governance passes; mainnet activation at height 1_500_000.
	•	Operators update to 2.0.0 before height; chain flips deterministically.

11.2 Non-Consensus RPC Addition
	•	Gate: rpc.receipts_v2
	•	Enabled by default on testnet for two weeks; minor release 1.7.0; deprecate old field after +2 minors.

⸻

12) Checklists

Author Checklist
	•	Gate added (default off), tests, vectors
	•	Activation method & parameters documented
	•	Rollback/abort plan documented
	•	Metrics & alerts in place
	•	Docs/CHANGELOG updated

Release Manager
	•	Signed artifacts & checksums
	•	SBOM & reproducible build logs
	•	Testnet soak report
	•	Operator guide published

Operator
	•	Backups taken
	•	Signatures verified
	•	Config merged
	•	Monitoring dashboards checked

⸻

13) Governance Mapping
	•	Upgrade (Major / Consensus) → Requires proposal, thresholds (see THRESHOLDS.md).
	•	ParamChange → For θ/Γ/fees/limits adjustments—document bounds and rationale.
	•	Policy → For PQ rotation, zk VK pinning, DA policy tweaks.

⸻

14) Appendix: Config Snippets

# activation by vote (hash of passed proposal + council attestation)
feature_gates:
  consensus:
    zk_verify_v1:
      enabled: true
      activation:
        method: vote
        proposal_hash: "0x8f7a…"
        council_multisig: "anim1…"

# time-based activation with guard
feature_gates:
  consensus:
    retarget_v3:
      enabled: true
      activation:
        method: time
        utc: "2025-01-15T12:00:00Z"
        min_node_fraction: 0.67   # refuse activate if <67% peers on ≥ version


⸻

15) Versioning Policy Summary
	•	MAJOR = may change consensus; gates off-by-default until activation.
	•	MINOR = features behind gates; no immediate consensus changes.
	•	PATCH = fixes/security; no format or consensus rule changes.

