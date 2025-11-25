# Governed Parameters & Boundaries
_What is configurable by governance, where it lives, and the safe operating bounds._

This document catalogs **chain parameters** that may be changed through **Parameter Governance Proposals (PGPs)** (see `docs/governance/OVERVIEW.md`). For each parameter family we list: **location**, **intent**, **units**, **safe bounds**, and **invariants** that must hold in CI before activation.

> **Never via params:** new fields in consensus objects, encoding changes, or semantics that alter historical validity. Those are **protocol upgrades** (see `docs/spec/UPGRADES.md`).

---

## 0) Sources of Truth

- **Consensus & Economics**
  - `spec/params.yaml` — global chain/economic/consensus knobs
  - `spec/poies_policy.yaml` — PoIES ψ mapping caps and diversity/escort rules
- **PQ / Crypto policy**
  - `spec/pq_policy.yaml`, `spec/alg_policy.schema.json`
- **Data Availability**
  - `da/config.py`, `da/constants.py`, `da/erasure/params.py`
- **Randomness**
  - `randomness/config.py`, `randomness/constants.py`
- **Execution / VM(Py)**
  - `execution/config.py`, `spec/opcodes_vm_py.yaml`, `vm_py/gas_table.json`
- **Networking / RPC**
  - `rpc/config.py`, `p2p/config.py`, `mempool/config.py`
- **AICF (AI/Quantum)**
  - `aicf/config.py`, `aicf/policy/example.yaml`

Parameter bundles must enumerate file paths and SHA-256 digests; nodes verify signatures and digests at load time.

---

## 1) Consensus / PoIES

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `difficulty.ema_alpha` | `spec/params.yaml` | Retarget smoothing | [0..1] | 0.60–0.98 | Monotone in stability sims; no oscillatory regime (jitter ≤ target) |
| `difficulty.clamp_up/down` | `spec/params.yaml` | Per-epoch Θ change caps | % | up ≤ +15%, down ≥ −20% | λ_obs → λ_target within 2–6 epochs in sims |
| `theta_target` | `spec/params.yaml` | Target acceptance threshold Θ | µ-nats | 10^4–10^8 | Keeps target block interval within SLA ±15% |
| `poies.total_gamma_cap` (Γ) | `spec/poies_policy.yaml` | Total ψ cap per block | “psi-units” | ≥ max per-type cap; ≤ safety upper bound | Σψ_clip ≤ Γ; receipts aggregation deterministic |
| `poies.per_type_caps[*]` | same | Clip per proof type | psi-units | Non-negative; sum ≤ Γ | Clipping preserves fairness bounds |
| `poies.escort_q` | same | Diversity escort weight | unitless | 0–1 | HHI/Gini improvement in fairness tests |
| `nullifiers.ttl_blocks` | `consensus/policy.py` | Reuse-prevention window | blocks | ≥ 2× reorg_limit | No false-accept in reuse tests |
| `fork_choice.reorg_limit` | `consensus/policy.py` | Max reorg depth | blocks | 8–1024 | ≥ consensus tests’ min; bounded memory |

**Must pass:** `consensus/tests/*` (accept/reject, retarget, fork-choice, nullifiers).

---

## 2) Mempool / Fee Market

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `limits.max_txs`, `limits.max_bytes` | `mempool/config.py` | Pool size | count/bytes | Fit in target RAM | Eviction terminates; latency not explosive |
| `min_gas_price` | same | Admission floor | atto-ANM | ≥ dust; ≤ surge target | Base floor EMA converges |
| `fee_market.ema_alpha` | same | Dynamic floor smoothing | [0..1] | 0.6–0.99 | No fee oscillations beyond spec |
| `replacement.bump_pct` | same | RBF threshold | % | 5–200 | Prevents griefing; replacement tests pass |
| `rate_limit.*` | same | DoS controls | tx/s, bytes/s | tuned to infra | Drop rates < target under attack sims |

**Must pass:** `mempool/tests/*`.

---

## 3) Economics / Rewards / Inflation

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `issuance.per_block` | `spec/params.yaml` | Base issuance | ANM | ≤ policy cap | Matches schedule; halving OK |
| `rewards.splits` (leader/committee/treasury/AICF) | `aicf/config.py` or `spec/params.yaml` | Reward shares | % | Sum = 100% | No negative balances; escrow bounded |
| `aicf.epoch_length` | `aicf/config.py` | Accounting cadence | blocks | 128–65536 | Settlement tests pass |
| `aicf.sla.thresholds` | same | Quality/latency bars | domain | Within audited ranges | Slashing math conservative |

**Must pass:** `aicf/tests/*`, economics sims in CI.

---

## 4) Data Availability (DA)

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `erasure.k`, `erasure.n` | `da/erasure/params.py` | RS profile | shares | n ≤ 2k; k≥16 | Recovery with any k shares |
| `blob.max_size` | `da/constants.py` | Single blob cap | bytes | ≤ block bytes budget | Commitment stable; proofs bounded |
| `das.samples_per_blob` | `da/sampling/probability.py` | DAS security | samples | Tuned to p_fail target | p_fail ≤ configured bound |

**Must pass:** `da/tests/*` (NMT, erasure, DAS).

---

## 5) Randomness / Beacon

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `round.length` | `randomness/config.py` | Round cadence | blocks/time | Align with block time | Liveness ≥ 99% in sims |
| `reveal.grace` | same | Late tolerance | blocks | ≤ 25% of round | Bias analysis holds |
| `vdf.iterations` | `randomness/constants.py` | Security/time | steps | Calibrated per hardware | Verify time within SLA |

**Must pass:** `randomness/tests/*`.

---

## 6) Execution / VM(Py)

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `gas.table` | `vm_py/gas_table.json` (derived from `spec/opcodes_vm_py.yaml`) | Cost model | gas/op | Non-zero, monotone where required | Refund cap ≤ 1/5 of used |
| `gas.refund_cap_ratio` | `execution/config.py` | Refund limit | ratio | ≤ 0.2 | No zero-cost loops |
| `access_list.limits` | same | Access bounds | items | Reasonable vs block | Deterministic metering |

**Must pass:** `execution/tests/*`, `vm_py/tests/*`.

---

## 7) P2P / RPC

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `p2p.ratelimits.*` | `p2p/config.py` | Per-peer/topic tokens | msgs/s | Sized to infra | No deadlock; mesh stable |
| `rpc.cors_allowlist` | `rpc/config.py` | Origins | hosts | Principle of least privilege | Tests enforce deny-by-default |
| `rpc.rate_limit` | same | Ingress caps | req/s | Protects node | 503 under surge, no panic |

**Must pass:** `p2p/tests/*`, `rpc/tests/*`.

---

## 8) PQ / Crypto Policy

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `pq.enabled_algs` | `spec/pq_policy.yaml` | Allowed sig/KEM | set | From audited list | Address rules satisfied |
| `alg_policy.root` | registry | Version pin | hash | Updated with signatures | Backward-compat window respected |

**Must pass:** PQ tests & vector checks.

---

## 9) ZK Verifier Policy (Optional)

| Key | Location | Intent | Units | Safe Bounds | Invariants |
|---|---|---|---|---|---|
| `zk.allowed_circuits` | `zk/integration/policy.py` | Allowlist | set | Only vetted IDs | VK cache hash matches |
| `zk.max_proof_bytes` | same | Size limit | bytes | Prevent DoS | All fixtures verify < cap |

**Must pass:** `zk/tests/*`.

---

## 10) Global Guardrails

1. **Timelocks:** All parameter bundles require timelock ≥ 48h (configurable).
2. **Reorg Safety:** `nullifiers.ttl_blocks ≥ 2 × reorg_limit`.
3. **Economics Conservation:** Reward splits sum to 100%; fees non-negative.
4. **Resource Caps:** Any increase in _per-tx_ or _per-block_ caps must prove:
   - No OOM in stress tests.
   - Block verification time ≤ target percentile (e.g., P95 ≤ 1s on reference HW).
5. **Compatibility:** No parameter change may alter historical validity (hashes, signatures, Merkle/NMT roots) of existing chain data.

---

## 11) Change Template (YAML Excerpt)

```yaml
bundle: params-v1.5.0
effective_height: 1234567
files:
  spec/params.yaml: "sha256:…"
  spec/poies_policy.yaml: "sha256:…"
changes:
  spec/params.yaml:
    difficulty:
      ema_alpha: 0.85 -> 0.80
    issuance:
      per_block: 5.0 -> 4.5
  spec/poies_policy.yaml:
    total_gamma_cap: 1200 -> 1000
risk: MEDIUM
ci_checks:
  consensus: pass
  mempool: pass
  economics: pass (ΔAPR -4%)
  da_p_fail: <= 1e-9
signatures:
  - signer: animica-governance-1
    sig: 0x…


⸻

12) FAQ
	•	Can we disable a proof type via params?
Yes, by setting its per-type cap to 0 and updating allowlists, provided this does not alter encoding or historical acceptance semantics.
	•	Can we change the chain ID via params?
No. Chain ID is part of the signature domain separation and is immutable post-genesis.

⸻

13) References
	•	docs/spec/* — canonical specs for each subsystem
	•	docs/economics/* — issuance, fees, payouts
	•	docs/security/* — DoS defenses, supply chain
	•	docs/governance/OVERVIEW.md — process & actors

