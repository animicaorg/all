# Parameters Under Governance & Safe Ranges

**Status:** Adopted  
**Related:** `spec/params.yaml`, `spec/poies_policy.yaml`, `docs/spec/UPGRADES.md`, `governance/PROCESS.md`, `governance/THRESHOLDS.md`, `governance/UPGRADE_PROCESS.md`

This document enumerates **which chain parameters are mutable**, who can change them (proposal type), and **safe operating ranges** with rationale. It complements the canonical sources of truth in `spec/*.yaml` and provides guardrails for proposers, reviewers, and operators.

> ⚠️ **Consensus parameters** must be shipped behind **feature gates** and activated per `governance/UPGRADE_PROCESS.md`. Any change outside the “Safe Range” requires a Research Note and explicit Risk section in the proposal.

---

## 1) Principles

- **Safety first:** Prefer changes that preserve liveness, determinism, and bounded resource growth.  
- **Predictability:** Changes should be gradual (bounded step size) and announced with deprecation/activation windows.  
- **Observability:** Every governed parameter must have metrics and alerts tied to it.  
- **Reversibility:** Provide rollback plan (down-migration or compatibility shim) for each change.

---

## 2) Proposal Classes (Who May Change What)

| Class | Examples | Proposal Type |
|---|---|---|
| **Consensus-Critical** | Θ/retarget constants, PoIES caps/Γ/diversity rules, block/DA limits, receipts layout, randomness/VDF params | **Upgrade** or **ParamChange** (if no format change) |
| **Economic / Fee** | base fee algo, min gas price, AICF splits/rates | **ParamChange** or **Policy** |
| **Network/DoS** | mempool limits, P2P rate limits, message sizes | **Policy** |
| **Operational** | logging, metrics windows, default RPC CORS | **Ops** (maintainers) with public changelog |

---

## 3) Global Invariants

- **Determinism:** No parameter may introduce nondeterministic behavior across nodes.  
- **Monotone resource caps:** Increasing any *per-block* cap must keep worst-case memory/time within tested bounds.  
- **Wire compatibility:** Format changes require a deprecation window and test vectors.

---

## 4) Governed Parameters & Safe Ranges

> Defaults are illustrative; consult `spec/params.yaml` and module configs for the current network.

### 4.1 Consensus — PoIES / Difficulty

| Param | Default | Safe Range | Step Limit | Notes / Rationale |
|---|---:|---:|---:|---|
| `difficulty.target_block_time_sec` | 12 | 2 – 30 | ≤ ±10% per release | Indirectly realized via Θ retarget; too low → orphan risk, too high → UX latency. |
| `difficulty.ema_halflife_blocks` | 900 | 64 – 4096 | x½ … x2 | Smoother retarget stabilizes λ; short halflife overreacts to variance. |
| `difficulty.retarget_clamp_pct` | 25% | 10% – 40% | ≤ ±5% | Cap per-window change in Θ to prevent oscillations. |
| `poies.total_gamma_cap (Γ)` | 1.00 | 0.80 – 1.25 | ≤ ±0.05 | Aggregate work normalization cap to prevent runaway multi-proof stacking. |
| `poies.per_type_cap[hash|ai|quantum|storage|vdf]` | 0.50 | 0.10 – 0.80 | ≤ ±0.10 | Ensure diversity; prevents single-proof dominance. |
| `poies.diversity_escort_q` | 0.10 | 0.00 – 0.30 | ≤ ±0.05 | Small escort bonus fosters mixed proofs without gaming. |
| `nullifiers.ttl_blocks` | 65,536 | 4,096 – 262,144 | x½ … x2 | Prevents proof reuse; larger TTL increases state but raises safety. |
| `fork_choice.max_reorg_depth` | 64 | 16 – 512 | x½ … x2 | Bound deep reorgs; tradeoff with liveness in partitions. |

### 4.2 Consensus — Block & Data Availability

| Param | Default | Safe Range | Step Limit | Notes |
|---|---:|---:|---:|---|
| `block.max_gas` | 15,000,000 | 3,000,000 – 60,000,000 | ≤ +20% | Higher gas increases execution load; must track CPU/memory headroom. |
| `block.max_bytes` | 1.5 MiB | 0.5 – 8 MiB | ≤ +20% | Hard upper bound on header+tx+proof envelope size. |
| `da.blob.max_size_bytes` | 4 MiB | 1 – 16 MiB | ≤ +25% | Larger blobs stress erasure/NMT & network; see DA benches. |
| `da.block_total_bytes_cap` | 16 MiB | 4 – 64 MiB | ≤ +25% | Sum of all blobs per block; protects bandwith and DAS time. |
| `da.erasure.k_n` | 128/256 | 64/128 – 256/384 | ±(16/32) | Code rate 0.5–0.75; higher rate reduces redundancy. |
| `da.light_client.samples_min` | 40 | 20 – 120 | ±10 | Sampling for p_fail ≤ 2^-k; see `docs/da/SAMPLING.md`. |

### 4.3 Consensus — Randomness (Beacon)

| Param | Default | Safe Range | Step Limit | Notes |
|---|---:|---:|---:|---|
| `rand.commit_window_blocks` | 120 | 30 – 360 | ±30 | Commit phase length; too short → low participation. |
| `rand.reveal_window_blocks` | 120 | 30 – 360 | ±30 | Reveal phase; balance latency vs liveness. |
| `rand.reveal_grace_blocks` | 16 | 4 – 64 | ±8 | Grace for network jitter & clock skew. |
| `vdf.iterations_per_round` | 2^30 | 2^24 – 2^36 | x½ … x2 | Verify should be ≤ 250 ms on reference hardware. |
| `vdf.modulus_bits` | 2048 | 2048 – 3072 | one step | Security vs verification cost. |

### 4.4 Economic / Fees

| Param | Default | Safe Range | Step Limit | Notes |
|---|---:|---:|---:|---|
| `fees.min_gas_price` | 1 gwei-eq | 0 – 50 | ≤ ×2 / release | Floor for DoS; dynamic floor may supersede. |
| `fees.basefee.target_gas_per_block` | 7,500,000 | 25% – 75% of `block.max_gas` | ≤ ±10% | EIP-1559 style target. |
| `fees.basefee.adjust_rate_pct` | 12.5% | 5% – 25% | ≤ ±5% | Controls basefee volatility. |
| `fees.blob_price_base` | 1 | 0.5 – 5.0 | ≤ ±0.5 | Linear or demand-based; DA pressure relief. |
| `aicf.split.{provider,treasury,miner}` | 70/20/10 | Sum = 100 | ≤ ±10 pts | Reward split; miner share aligns inclusion incentives. |
| `aicf.pricing.{ai_unit,quantum_unit}` | Network-specific | See policy | ≤ ±20% | Must track market costs & SLA. |

### 4.5 Network / DoS (Mempool & P2P)

| Param | Default | Safe Range | Step Limit | Notes |
|---|---:|---:|---:|---|
| `mempool.max_txs` | 100k | 10k – 500k | ≤ +25% | Memory/GC pressure; depends on hardware profile. |
| `mempool.max_bytes` | 512 MiB | 128 – 2048 MiB | ≤ +25% | Global occupancy cap. |
| `mempool.ttl_seconds` | 600 | 60 – 3600 | ±300 | Evict stale txs; interacts with fee market. |
| `mempool.min_tip_gwei` | 0 | 0 – 3 | ≤ +1 | Prevents spam during zero basefee epochs. |
| `mempool.rbf.percent_threshold` | 12% | 5% – 30% | ±5% | Replace-by-fee fairness. |
| `p2p.max_peers` | 64 | 32 – 256 | ±16 | Connection fanout vs resource use. |
| `p2p.msg_max_bytes` | 4 MiB | 1 – 8 MiB | ±1 MiB | Gossip frame cap. |
| `p2p.rate.bytes_per_sec_per_peer` | 256 KiB | 64 – 1024 KiB | ±128 KiB | Token-bucket DoS control. |

### 4.6 zk / Capabilities / PQ (Policy Surfaces)

| Param | Default | Safe Range | Notes |
|---|---:|---:|---|
| `zk.allowlist.circuits[*]` | curated | curated-only | Add/remove via **Policy**; circuit VKs pinned (see zk registry). |
| `zk.verify.gas_per_scheme` | profiled | ±25% | Reflect real verifier costs; update with benches. |
| `capabilities.input_size_limits` | profiled | monotone ↑ | Determinism & bandwidth caps. |
| `pq.alg_set` | Dili3/SPHINCS/Kyber768 | add/remove via **Policy** | Rotations per `docs/pq/POLICY.md`. |

---

## 5) Change Control & Checklists

**Every proposal must include:**

- Parameter(s) & current values (with file paths in `spec/` or module configs).
- Proposed new values, step deltas, and justification.
- Simulation/bench results: impact on TPS, orphan rate, mempool pressure, DA sampling time, VDF verify time.
- Rollback plan (and abort window, if consensus).
- Monitoring plan: metrics and alert thresholds to watch post-change.

---

## 6) Worked Examples

### Example A — Raise Block Gas by 10%
- **From/To:** `block.max_gas` 15M → 16.5M  
- **Class:** Consensus; within safe step (≤ +20%).  
- **Preconditions:** Execution CPU headroom ≥ 30% on p50 hardware; receipts hash stability verified; mempool drain bench updated.  
- **Rollout:** Testnet 2 weeks → mainnet height activation.

### Example B — Tighten Basefee Adjust Rate
- **From/To:** `fees.basefee.adjust_rate_pct` 12.5 → 10  
- **Class:** Economic/Policy; smoother basefee response to bursts.  
- **Monitor:** Pending TXs, inclusion latency, underpriced spam.

### Example C — DA Blob Cap Increase
- **From/To:** `da.blob.max_size_bytes` 4 MiB → 6 MiB  
- **Risks:** Light-client sampling time & proof sizes.  
- **Mitigation:** Increase `da.light_client.samples_min` +10; re-run DAS benches.

---

## 7) Parameter Index → File Paths

- **Chain Params:** `spec/params.yaml` → loaded by `core/types/params.py`.  
- **PoIES Policy / Caps:** `spec/poies_policy.yaml` → consumed by `consensus/policy.py`.  
- **DA:** `da/config.py`, `da/erasure/params.py`.  
- **Randomness/VDF:** `randomness/config.py`, `randomness/vdf/params.py`.  
- **Mempool:** `mempool/config.py`.  
- **P2P:** `p2p/constants.py`, `p2p/config.py`.  
- **Fees:** `execution/runtime/fees.py`, `mempool/fee_market.py`.  
- **AICF:** `aicf/config.py`, `aicf/policy/example.yaml`.  
- **zk:** `zk/integration/policy.py`, `zk/registry/*`.  
- **PQ:** `spec/pq_policy.yaml`, `pq/alg_policy/*`.

---

## 8) Appendix — Proposal Template

```markdown
# ParamChange: <short title>

## Summary
Change <param(s)> from <old> to <new>.

## Motivation
<throughput, latency, security, economics>

## Specification
- Files/keys: <paths & YAML keys>
- New values: <table>

## Safety & Rationale
- Invariants preserved
- Bench results (links)
- Step within safe range? <yes/no; explain>

## Activation & Rollback
- Gate: <name>
- Activation: <height/time/vote>
- Abort window: <K blocks>
- Rollback path: <soft/hard>

## Monitoring
- Metrics & alerts to watch
- Success/abort criteria

## Changelog
- docs/spec updated?
- test vectors updated?

