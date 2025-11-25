# PARAMETER BOUNDARIES (NON-NEGOTIABLE)
_Hard safety limits and rationale for Animica governance_

**Version:** 1.0  
**Status:** Active  
**Scope:** Applies to all proposals that modify protocol parameters (VM, DA, PoIES, networking, governance). These limits are enforced in code, CI, and policy. Proposals **outside** these bounds are **invalid** and must be rejected at intake.

This document is the human rationale companion to the machine-readable rails in `governance/registries/params_bounds.json`. Current values live in `governance/registries/params_current.json`. Tooling: `validate_proposal.py`, `check_registry.py`, CI tests.

---

## 1) Philosophy

- **Safety > Liveness > Throughput.** We would rather slow down than risk consensus failure or fund loss.  
- **Predictability.** Changes must be gradual and capped to prevent cliff-edge dynamics.  
- **Defense in depth.** Bounds are duplicated across schema validation, CI, and runtime guards where applicable.  
- **Explained constraints.** Every hard bound has a short rationale and failure mode description.

---

## 2) Governance Voting Bounds

| Key | Min | Max | Rationale / Failure Mode |
|---|---:|---:|---|
| `gov.vote.quorum_percent` | 5.0 | 40.0 | Avoid trivially small quorums (capture) and impossible quorums (gridlock). |
| `gov.vote.approval_threshold_percent` | 50.0 | 80.0 | Supermajority range for safety-critical changes without requiring unanimity. |
| `gov.vote.window_days` | 3 | 14 | Prevent flash votes; avoid long attack surfaces. |
| `gov.activate.timelock_days` | 1 | 14 | Ensure review/rollback window; limit stagnation. |
| `gov.activate.abort_window_blocks` | 0 | 10_000 | Abort must be near-term; unbounded windows weaken finality. |

---

## 3) VM & Execution Bounds

| Key | Min | Max | Rationale |
|---|---:|---:|---|
| `vm.gas.max_block_gas` | 1_000_000 | 200_000_000 | Prevent DoS via over-permissive blocks; keep verification time bounded. |
| `vm.gas.max_tx_gas` | 50_000 | 100_000_000 | Single-tx upper guardrail; prevents starvation of other txs. |
| `vm.gas.price_min_nano` | 0 | 10_000_000 | Lower bound can be 0 for emergencies; upper bound prevents fee misconfig. |
| `vm.opcodes.add_allowlist_percent` | 0 | 10 | Max % of new opcodes per major upgrade to limit complexity spikes. |
| `vm.version.activation_delay_blocks` | 0 | 20_000 | Enough time for node upgrades; avoid indefinite delays. |

---

## 4) PoIES / Economics Bounds

| Key | Min | Max | Rationale |
|---|---:|---:|---|
| `poies.gamma.target_range_min` | 0.50 | 0.95 | Keep Γ (useful-work share) within stable envelope. |
| `poies.gamma.target_range_max` | 0.55 | 0.99 | Cap to avoid starving classic hashing and destabilizing incentives. |
| `poies.retarget_interval_blocks` | 60 | 10_000 | Avoid feedback oscillations (too fast) and sluggish correction (too slow). |
| `fees.min_relay_fee_nano` | 0 | 50_000 | Relay DoS protection while allowing emergency relief. |
| `emissions.halving_interval_blocks` | 100_000 | 20_000_000 | Prevent pathological issuance schedules. |

---

## 5) Data Availability (DA) Bounds

| Key | Min | Max | Rationale |
|---|---:|---:|---|
| `da.max_blob_size_bytes` | 64_000 | 8_388_608 | Ensure blobs fit in typical bandwidth and verifier RAM. |
| `da.nmt_namespace_bytes` | 8 | 32 | Interop vs. overhead trade-off; below 8 weakens separation. |
| `da.rs_rate` | 0.10 | 0.66 | Erasure coding redundancy must be meaningful but not wasteful. |
| `da.prove_timeout_blocks` | 10 | 10_000 | Proof liveness without indefinite lockups. |

---

## 6) Networking & Mempool Bounds

| Key | Min | Max | Rationale |
|---|---:|---:|---|
| `p2p.max_peers` | 16 | 512 | Maintain graph connectivity without resource exhaustion. |
| `p2p.gossip_batch_bytes` | 4_096 | 1_048_576 | Prevent giant gossip frames; improve fairness. |
| `mempool.max_txs` | 1_000 | 5_000_000 | RAM guardrail; avoids swap-death. |
| `mempool.replace_by_fee_increment_percent` | 5.0 | 100.0 | RBF needs meaningful bump; 100% cap prevents griefing loops. |

---

## 7) Randomness & Beacons

| Key | Min | Max | Rationale |
|---|---:|---:|---|
| `randomness.vdf.difficulty` | 1 | 1_000_000 | Too low → biasable; too high → starvation on commodity nodes. |
| `randomness.commit_reveal.window_blocks` | 5 | 10_000 | Ensure participation without indefinite withholding. |
| `randomness.beacon.participant_max_share_percent` | 5.0 | 50.0 | Diversity cap to resist single-party control. |

---

## 8) PQ (Post-Quantum) Policy Bounds

| Key | Allowed Values | Rationale |
|---|---|---|
| `pq.sigDefault` | `["ed25519","secp256k1","dilithium3","sphincs+"]` | Constrained menu; additions require formal evaluation. |
| `pq.kex` | `["kyber-768","ntru-hps-509"]` | Limit to widely reviewed KEMs and sizes. |
| `pq.rotation.min_notice_days` | `7–90` | Time for wallets/exchanges to upgrade. |
| `pq.rotation.parallel_enabled` | `true/false` | Parallel phase bounded to avoid extended dual-stack risk (≤ 180 days). |

---

## 9) Change-Rate (Delta) Guards

Even **within** absolute bounds, proposals must respect max step sizes to avoid shocks.

| Namespace | Key (example) | Max Δ per proposal | Max Δ per 30d | Notes |
|---|---|---:|---:|---|
| VM Gas | `vm.gas.max_block_gas` | ±20% | ±40% | Require perf evidence for >10%. |
| Fees | `fees.min_relay_fee_nano` | ±50% | ±100% | Emergency flag can override once. |
| PoIES | `poies.gamma.target_*` | ±5% abs | ±10% abs | Prevent incentive whiplash. |
| DA | `da.max_blob_size_bytes` | +25% / −10% | +50% / −25% | Avoid sudden bandwidth spikes. |
| Governance | `gov.vote.*` | ±5 abs pts | ±10 abs pts | Maintain legitimacy continuity. |

These delta checks are implemented in `validate_proposal.py --strict`.

---

## 10) Invalid Patterns (Auto-Reject)

- Any proposal that sets `min > max`, introduces NaN/inf, or changes key **types**.  
- Setting quorum < approval threshold semantics (nonsense logic).  
- Simultaneously pushing multiple critical levers beyond **two** delta domains (e.g., VM gas + DA blob + Γ) without an **upgrade** class proposal and risk sign-off.  
- PQ changes that introduce algorithms not in the allowed lists.  
- Setting diversity caps above 50% for any single provider/participant class.

---

## 11) Validation Pipeline

1. **Schema check:** JSON Schema Draft 2020-12 for proposal headers.  
2. **Bounds check:** Compare proposed values to `params_bounds.json` (min/max/enums).  
3. **Delta check:** Compare to `params_current.json` with rate limits (§9).  
4. **Policy cross-check:** For certain keys, require linked artifacts (benchmarks, risk checklists, rollout plans).  
5. **CI & Review:** Failing any step blocks merge; stewards may only **loosen** proposals (never tighten beyond bounds).

Commands:

```bash
python governance/scripts/validate_proposal.py path/to/proposal.md --strict --pretty
python governance/scripts/check_registry.py --strict --pretty
pytest -q governance/tests
12) Extending Bounds
Add new keys to params_bounds.json with both min and max (or an enum).

Update test_schemas.py and test_validate_examples.py to include fixtures.

Include rationale here with a short paragraph and link to benchmarks/ADRs.

13) Emergency Levers
In Severe incidents, stewards may invoke Emergency Mode:

Temporarily override one domain within absolute bounds for ≤ 14 days.

Must publish notice per TRANSPARENCY.md §10 and open a follow-up proposal to ratify or revert.

Emergency overrides cannot change PQ algorithms.

Suggested flags:

emergency.fees.override_enabled (bool)

emergency.vm.gas_ceiling_delta_percent (≤ +20)

emergency.da.rate_limit_percent (≤ −25)

14) Rationale Cheatsheet (Failure Modes)
Gas ceilings too high → verifier timeouts, uncle/orphan spikes, DoS.

Γ caps too high → centralization to specialized providers, degraded permissionless mining.

DA blobs too large → bandwidth spikes, light client breakage.

Quorum too low → easy capture; too high → veto gridlock.

Randomness windows too long → grinding/withholding; too short → participation failures.

PQ sprawl → wallet fragmentation, foot-guns in key management.

15) Traceability
Every merged change must reference:

Proposal ID, PR, commit SHA

Bounds snapshot diff

Benchmarks / docs (if required)

Rollout plan (if activation-coupled)

16) Change Log
1.0 (2025-10-31): Initial non-negotiable limits and delta guards across governance, VM, PoIES, DA, networking, randomness, and PQ policy.

