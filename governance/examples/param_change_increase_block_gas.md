---
title: "Param Change — Increase Max Block Gas"
status: "Draft"
proposal_type: "ParamChange"
author: "Proposer Name <proposer@example.com>"
date: "2025-10-31"
targets:
  network: "testnet"   # devnet → testnet → mainnet rollout (see Rollout)
  params:
    - id: "exec.max_block_gas"
      from: 12500000
      to:   15000000
links:
  process: governance/PROCESS.md
  template: governance/templates/PARAM_CHANGE.md
  registry: governance/registries/params_registry.yaml
  thresholds: governance/THRESHOLDS.md
  chain_params: docs/spec/CHAIN_PARAMS.md
---

# Summary

Increase `exec.max_block_gas` (maximum gas per block) from **12,500,000** to **15,000,000** to alleviate periodic block fullness, reduce tail latency for contracts, and accommodate expected traffic during the upcoming milestones.

This proposal **does not change** gas pricing or opcode metering; it only adjusts the block-level cap.

---

## Motivation

- **Sustained >90% block gas utilization** at peaks causes mempool queuing and elevated fees for latency-sensitive transactions.
- Recent optimization in the execution engine (parallelizable sections + precompile batching) decreased average execution time per unit gas by ~12–15% in test profiles, allowing a safe raise to the cap without jeopardizing slot timing.

---

## Current State

- **Parameter ID:** `exec.max_block_gas` (module: `exec`)
- **Registry entry:** lives in `governance/registries/params_registry.yaml`
- **Default (testnet/mainnet):** `12_500_000`
- **Observed metrics (last 7d, testnet):**
  - Block gas used p50/p90/p99: 8.2M / 11.7M / 12.4M
  - Mean block execution time: 820 ms (budget: 1500 ms)
  - Reorg depth p99: ≤ 1
  - Mempool wait p90: 1.6 blocks during peaks

---

## Proposed Change

- Set `exec.max_block_gas := 15_000_000` on **testnet** first.
- No other parameter changes.

---

## Bounds & Safety

From the **Params Registry** (informative excerpt):

```yaml
- id: exec.max_block_gas
  module: exec
  summary: Maximum gas permitted per block
  type: u64
  unit: gas
  default: 12500000         # current
  min: 8000000
  max: 30000000
  stability_tier: critical
  owners: ["execution","consensus"]
  docs_ref: docs/spec/CHAIN_PARAMS.md
The requested change remains within bounds [8,000,000, 30,000,000].

Tier: critical ⇒ elevated voting threshold (see Voting).

Risk Analysis


Consensus / Liveness

Higher cap increases worst-case execution time; however, headroom remains:

Current 12.5M → 15M (+20%). With 12–15% engine speedup, net worst-case wall time remains under the 1.5s budget per block on reference hardware.



State Growth & Archival

+20% ceiling may increase daily state growth in bursty periods. Mitigation:

Continue pruning policy, snapshot cadence unchanged.

Indexer operators notified (see Coordination).



Fee Dynamics / MEV

Slight fee relief during peaks; MEV surface unchanged (ordering rules unaffected).



Operational

Nodes below reference spec may experience higher CPU; publish minimum sizing note.

Telemetry & SLOs (Acceptance Criteria)


During testnet soak (≥ 72h, ≥ 100k blocks):

block_exec_time_ms_p99 ≤ 1300 ms

reorg_depth_p99 ≤ 1

full_block_rate (≥ 95% gas) ≤ 40% over 1h windows

mempool_wait_blocks_p90 ≤ 1.0

Reference validator CPU p95 ≤ 75%, RSS p95 ≤ +5% vs baseline



Failing any SLO auto-aborts the rollout (back to 12.5M) and opens an incident.

Rollout Plan
Devnet (instant):

Flip cap to 15M.

Run synthetic and mixed workloads (bench scenarios in docs/benchmarks/SCENARIOS.md).

Testnet (this proposal):

Governance vote to apply new default.

Soak for ≥ 72h under real load.

Publish metrics report & run hash.

Mainnet (follow-up proposal):

Requires Security Council ACK + community sign-off.

Schedule at off-peak UTC window; notify infra partners T-48h.

Backout Plan
Parameter is runtime-read; if SLOs regress, submit emergency ParamChange to revert to 12.5M (fast-track per EMERGENCY_PROCEDURES.md).

Node operators receive alert via status page & RSS.

On-Chain Proposal Payload (per 
governance/schemas/param_change.schema.json
)
{
  "type": "ParamChange",
  "network": "testnet",
  "changes": [
    {
      "id": "exec.max_block_gas",
      "new_value": 15000000
    }
  ],
  "justification": "Alleviate persistent block fullness; aligns with recent execution speedups.",
  "rollout": {
    "soak_blocks_min": 100000,
    "abort_on_slo_violation": true
  },
  "metadata": {
    "baseline_run": "testnet-2025-10-28T00:00Z",
    "bench_refs": ["docs/benchmarks/RESULTS.md#2025-10-28"]
  }
}
Registry Diff (must be in the PR)
-  default: 12500000
+  default: 15000000
Note: CI will ensure bounds, owners’ ACKs, and regenerate docs tables in docs/spec/CHAIN_PARAMS.md.


Run locally:

make check-params   # schema + bounds + owners ACK
make params         # regenerate typed views & docs tables
Coordination
Node operators: Mailing list & status page T-48h.

Indexers/Explorers: Confirm ingestion throughput & DB sizing headroom.

Wallets/SDKs: N/A (read-only parameter; no API change).

Voting
Proposal Type: ParamChange (critical tier)

Quorum: ≥ 25% voting power

Supermajority to pass: ≥ 66% for

Voting window: 5 days

Enact delay (testnet): ~1 hour after finalization



(See governance/THRESHOLDS.md and governance/VOTING.md.)

Appendix: Reference Hardware Budget
Reference validator: 8 vCPU (Zen 3 or Apple M-class equiv), 32 GB RAM, NVMe SSD.

Execution budget per block: ≤ 1.5s wall time p99.

With 15M cap and recent engine optimizations, modeled p99 = ~1.1–1.2s.

