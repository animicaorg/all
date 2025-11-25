---
title: "Params Registry: Canonical Source-of-Truth & CI Checks"
status: "Accepted"
date: "2025-10-13"
owners:
  - Governance Editors
  - Core Maintainers Council
reviewers:
  - Security Council (bounds only)
component: "Governance • Configuration"
scope: "Chain/execution/mempool/DA/randomness parameterization"
links:
  primary_registry: governance/registries/params_registry.yaml
  related:
    - governance/PROCESS.md
    - governance/PARAMS_GOVERNANCE.md
    - governance/PROPOSAL_TYPES.md
    - governance/THRESHOLDS.md
    - governance/templates/PARAM_CHANGE.md
    - docs/spec/CHAIN_PARAMS.md
    - docs/spec/DIFFICULTY_RETARGET.md
    - docs/spec/MEMPOOL.md
---

# ADR-0002 — Params Registry as Source-of-Truth & CI Checks

## Summary

Establish `governance/registries/params_registry.yaml` as the **single canonical source** for all tunable protocol parameters. The registry defines **type-safe entries, units, safe bounds, defaults, owners, and network overrides**. CI enforces schema, bounds, and ownership ACKs for any change. All modules **consume** parameters **only** via generated artifacts derived from this registry.

---

## Motivation

Parameters (e.g., Θ target, Γ caps, mempool limits, DA blob sizes, VDF iterations) appear across multiple modules. Drift between docs, code, and releases introduces risk. We need:
- **One file** that models *what can change* and *within what safety envelope*.
- **Deterministic generation** of runtime configs and docs.
- **Automated guardrails** in CI to prevent foot-guns.

---

## Decision

1. **Canonical Registry**
   - File: `governance/registries/params_registry.yaml`
   - Defines per-parameter metadata: `id`, `module`, `summary`, `type`, `unit`, `default`, **bounded `min`/`max`**, stability tier, owner(s), and **network overrides** (`mainnet`/`testnet`/`devnet`).
   - Optional fields: `depends_on`, `rollout`, `risk_notes`, `telemetry`, `proof_of_safety` (invariant), and `docs_ref`.

2. **Consumption Pipeline (Build-Time)**
   - A small codegen step (make target) converts the registry into:
     - `spec/params.yaml` (machine-consumable superset for the node)
     - Language-specific typed views (e.g., `core/types/params.py`)
     - Rendered docs tables in `docs/spec/CHAIN_PARAMS.md`

3. **CI Enforcement (Blocking)**
   - **Schema validation** (YAML → JSON Schema) and **static typing** per `type`.
   - **Bounds checks**: Proposed value deltas **must** remain within `[min,max]` and, if crossing tier thresholds, require the appropriate **proposal type** and **supermajority** (per `THRESHOLDS.md`).
   - **Ownership ACK**: `module_owners.yaml` must ACK changes touching their module.
   - **Diff classification**: change is labeled **safe / moderate / risky** by rules below; risky requires Security Council sign-off for *testnet* trial before mainnet.
   - **Deterministic serialization**: registry is normalized (key order, floats as strings where needed) and a **content hash** is recorded in the release notes.

4. **Governance Integration**
   - Any `ParamChange` proposal **must** include an excerpt of affected registry entries and pass CI.
   - Runtime release artifacts embed the **registry hash**; explorers and docs display it.

---

## Registry Entry Schema (informative)

```yaml
# governance/registries/params_registry.yaml (excerpt)
- id: consensus.theta_target_micro
  module: consensus
  summary: Target acceptance threshold Θ (µ-nats)
  type: u64
  unit: micro-nats
  default: 138629436  # ≈ ln(1e6) * 1e6; example only
  min: 50000000
  max: 300000000
  stability_tier: critical
  owners: ["consensus"]
  network_overrides:
    testnet: { default: 110000000 }
    devnet:  { default: 80000000 }
  docs_ref: docs/spec/DIFFICULTY_RETARGET.md
  proof_of_safety:
    invariants:
      - "Θ_min ensures target block interval ≥ 0.25× nominal"
      - "EMA clamps maintain stability with λ_obs jitter"
    telemetry:
      - "block_interval_mean"
      - "reorg_depth_p99"

- id: mempool.min_base_fee
  module: mempool
  summary: Minimum base fee floor (attoANM)
  type: u128
  unit: attoANM
  default: "100000000000"   # 1e11
  min: "0"
  max: "1000000000000000"
  stability_tier: medium
  owners: ["mempool"]

- id: da.max_blob_size
  module: da
  summary: Maximum blob size accepted by DA POST (bytes)
  type: u64
  unit: bytes
  default: 262144   # 256 KiB
  min: 65536
  max: 4194304
  stability_tier: medium
  owners: ["da"]

- id: randomness.vdf_iterations
  module: randomness
  summary: Wesolowski VDF iteration count per round
  type: u64
  unit: iterations
  default: 20000000
  min: 1000000
  max: 100000000
  stability_tier: critical
  owners: ["randomness"]
  rollout:
    strategy: "testnet-first"
    baked_in_check: "verifier_cpu_time_ms <= budget_ms"

Types: bool | u32 | u64 | u128 | i64 | f64 | string (with unit hint).
Stability tiers: critical | medium | low (drives thresholds & rollout).

⸻

Change Classification Rules

CI labels each param change:
	•	Safe: within [min,max] and absolute delta ≤ safe_delta for tier (e.g., ≤5% medium, ≤1% critical).
	•	Moderate: within bounds but > safe_delta; requires ParamChange + standard quorum.
	•	Risky: approaches boundaries (e.g., >90th percentile of range) or touches critical tier; requires elevated threshold and testnet soak (min N epochs) with telemetry gates.

These thresholds live alongside the registry in small policy constants (reviewed like code).

⸻

Invariants & Proof-of-Safety

For parameters with known system-theory constraints (e.g., Θ retarget stability), registry entries may carry machine-checkable predicates that CI runs against simulation fixtures:
	•	consensus.theta_target_micro → EMA stability bound holds for observed jitter distributions.
	•	da.max_blob_size → DAS sampling probability p_fail ≤ configured bound at min sampler count.
	•	randomness.vdf_iterations → verifier time on reference hardware ≤ budget.

Failing predicates block the merge.

⸻

Ownership & ACK Flow
	•	Each entry names owners (keys in module_owners.yaml).
	•	CI requires signed-off-by (GitHub approval) from one owner; for critical also from Security Council on mainnet-touching changes.
	•	Editors may format/normalize but cannot change semantics without owners’ ACK.

⸻

Network Overrides

network_overrides.{mainnet,testnet,devnet} may set per-network defaults within bounds.
CI ensures overrides don’t escape [min,max] and that mainnet changes require a successful testnet run (artifacts linked in the proposal).

⸻

Tooling
	•	make params:
	•	Validate YAML schema & types.
	•	Emit spec/params.yaml and typed views for consumers.
	•	Regenerate docs tables in docs/spec/CHAIN_PARAMS.md.
	•	make check-params (CI): run schema, bounds, invariants, owners ACK, delta classification.

⸻

Rollout
	1.	Land the initial registry with conservative bounds & defaults.
	2.	Wire all modules to consume generated params (no hand-written constants for governed values).
	3.	Add dashboards to surface params & effects (ex: Θ, λ_obs, base fee watermark).

⸻

Security Considerations
	•	Misconfigured bounds can block urgent safety changes; keep emergency room via EMERGENCY_PROCEDURES.md, but still within registry min/max unless Security Council triggers time-boxed override on testnet only.
	•	Registry hash is embedded in releases; explorers verify and show mismatches.

⸻

Backwards Compatibility
	•	Modules keep fallbacks for devnet while transitioning; warnings if runtime params do not match registry hash.
	•	SDKs expose read-only views (no client override) to avoid fragmentation.

⸻

Open Questions
	•	Should we add curve-bound types (e.g., percent, basis points) to reduce mistakes?
	•	Introduce time-based bound rules (e.g., “change ≤ X% per week”) enforced by CI?

⸻

Readiness Checklist
	•	Registry created with initial entries and owners.
	•	CI validators implemented and blocking.
	•	Codegen emits spec/params.yaml & typed views.
	•	Docs tables render from registry.
	•	Release pipeline includes registry hash.

⸻

Amendments
	•	None yet.
