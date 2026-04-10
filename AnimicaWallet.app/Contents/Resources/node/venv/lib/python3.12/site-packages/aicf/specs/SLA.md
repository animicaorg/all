# AICF SLA
_metrics, thresholds, evaluation windows_

This document standardizes the Service Level Agreement (SLA) model for **AI/Quantum** providers in the AICF. It defines metrics, how they are measured, the evaluation windows, and the deterministic **banding → quality multiplier** used by pricing.

**Status:** Normative for SLA. See code in `aicf/sla/*`, economics interaction in `aicf/economics/*`, and tests in `aicf/tests/test_sla_eval.py`.

---

## 1) Goals & Principles

- Reward providers that are **correct**, **consistent**, **timely**, and **available**.
- Produce per-job **q_mult** deterministically from committed policy and observable facts.
- Avoid noisy/short-term flukes via **windowed** evaluation and **confidence bounds**.
- Never require floating environment state: all thresholds and bins are **policy-bound**.

---

## 2) Metric Catalog

All metrics are normalized to fixed ranges and computed in `aicf/sla/metrics.py` from queue events, registry heartbeats, and proof intake.

### 2.1 Quantum-only
- **Traps Coverage** `traps_ratio ∈ [0,1]`  
  Fraction of shots allocated to trap circuits. Derived from proof metadata (normalized in `aicf/adapters/proofs.py`).

- **Integrity** `integrity_ok ∈ {0,1}`  
  Proof bundle attestation passes (TEE/QPU cert chain, measurement checks). Boolean per job.

### 2.2 AI-only
- **QoS Score** `qos ∈ [0,1]`  
  Workload-defined quality score (e.g., accuracy/BLEU/fidelity) normalized by the job spec (`JobSpecAI.qos_norm`). Produced/validated at proof intake.

### 2.3 Common (AI & Quantum)
- **Latency** `latency_ms ≥ 0`  
  `t_proof - t_assign` measured in milliseconds from the dispatcher. We evaluate **P50** and **P95** over windows using fixed histogram bins.

- **Availability** `availability ∈ [0,1]`  
  Uptime proxy combining:
  - Heartbeat success rate
  - Lease acceptance ratio (accepted / offered)
  - Timely proof ratio (completed within SLO)
  Aggregated as a weighted fixed-point sum, weights in policy.

- **Invalid/Timeout Rate** `fail_rate ∈ [0,1]`  
  Share of jobs that timed out or produced invalid proofs.

---

## 3) Evaluation Windows

Defined in `aicf/sla/evaluator.py`, configured by policy.

### 3.1 Windows & Sample Floors
Two rolling windows per provider:
- **Short window** `W_s`: recent responsiveness (by jobs or time).
- **Long window** `W_l`: stability trend (by jobs or time).

Policy fields:

sla.windows.short: {jobs: N_s | time_seconds: T_s}
sla.windows.long:  {jobs: N_l | time_seconds: T_l}
sla.min_samples:   M   # minimum jobs required for banding
sla.weights:       {short: w_s, long: w_l} # fixed-point weights, w_s + w_l = 1

If `samples < M` in both windows → **provisional band** (see §6.4).

### 3.2 Aggregation
- Ratios: arithmetic mean over the window.
- Percentiles: computed from fixed histogram bins (policy-specified edges).
- Combined metric: `metric* = w_s · metric_s + w_l · metric_l` (fixed-point).

### 3.3 Confidence Bounds (Deterministic)
For binomial-style ratios (availability, timely, integrity), compute **Wilson lower bound** with policy z-score:

p̂ = successes / N
den = 1 + z²/N
delta = z * sqrt(p̂(1-p̂)/N + z²/(4N²))
p_lb = (p̂ + z²/(2N) - delta) / den

Use `p_lb` in threshold comparisons. Policy: `sla.confidence.z` (e.g., 1.96). If N < M, skip Wilson and use provisional banding.

---

## 4) SLOs & Thresholds (Policy-Sourced)

Thresholds are discrete **bins**; no runtime curve fitting. Example policy keys:

sla.thresholds.ai.qos:          {gold: 0.90, silver: 0.80, bronze: 0.70}
sla.thresholds.quantum.traps:   {gold: 0.05, silver: 0.03, bronze: 0.01}
sla.thresholds.latency_ms.p95:  {gold: 3_000, silver: 6_000, bronze: 12_000}
sla.thresholds.availability_lb: {gold: 0.990, silver: 0.975, bronze: 0.950}
sla.thresholds.fail_rate_max:   {gold: 0.01, silver: 0.03, bronze: 0.07}

Notes:
- `availability_lb` is compared against **Wilson lower bound**.
- `latency_ms.p95` is an **upper bound** (lower is better).
- `fail_rate_max` is an **upper bound**.

---

## 5) Banding → Quality Multiplier

The evaluator assigns a **band** per job using the provider’s latest windowed metrics at assignment time (or proof intake if policy requires). Bands map to **q_mult** used by pricing (see `aicf/economics/pricing.py`).

### 5.1 Decision Rules
Per **kind**, all conditions must pass for a band; otherwise fall through:

**AI**
- **Gold**: `qos ≥ Q_g` AND `p95 ≤ L_g` AND `availability_lb ≥ A_g` AND `fail_rate ≤ F_g`
- **Silver**: thresholds at `Q_s, L_s, A_s, F_s`
- **Bronze**: thresholds at `Q_b, L_b, A_b, F_b`
- **Fail**: otherwise (q_mult = 0)

**Quantum**
- **Gold**: `traps_ratio ≥ T_g` AND `integrity_ok = 1` AND `p95 ≤ L_g` AND `availability_lb ≥ A_g` AND `fail_rate ≤ F_g`
- Then Silver/Bronze with their thresholds; Fail otherwise.

### 5.2 Multipliers (policy)

pricing.q_mult:
gold:   1.10
silver: 1.00
bronze: 0.85
fail:   0.00

### 5.3 Determinism
- Thresholds and multipliers are fixed-point integers in policy.
- Ties: prefer **lower** band (conservative).
- If any required metric is missing → treat as worst value for that band test.

### 5.4 Provisional Band (Cold Start)
If provider has < `sla.min_samples` in both windows:
- Assign **provisional_silver** (i.e., `q_mult = pricing.q_mult.silver`) **unless** integrity/attestation fails, in which case **fail**.
- Policy may choose `bronze` instead via `sla.provisional_band`.

---

## 6) Availability & Latency Details

- **Availability** = `w_hb·hb_ok + w_acc·accept_rate + w_timely·timely_rate`, weights in policy; each component is a ratio with Wilson LB applied before weighting.
- **Timely** uses `latency ≤ slo_ms(kind)` per job as a Bernoulli.
- **Latency P95**: from fixed histogram (policy bins), interpolated by nearest-upper bin (conservative).

Clock source: dispatcher monotonic timestamps, persisted with jobs; no reliance on wall-clock NTP.

---

## 7) Violations, Penalties, and Jailing

- Any **Fail** banded job: `q_mult = 0`.  
- Window-level **persistent failure** (e.g., `availability_lb < A_b` for `K` consecutive evaluations) triggers `SlashEvent` per `aicf/sla/slash_engine.py` and `aicf/economics/slashing_rules.py`.
- Jailing/cooldown timers and repeated-offense multipliers are policy-driven.

Policy keys (examples):

sla.penalties:
consecutive_fail_k: 3
jail_seconds: 86_400
slash:
minor: 0.01
major: 0.05
repeated: 0.10

---

## 8) Interaction with Economics

- Band → `q_mult` feeds the reward formula (see ECONOMICS.md §2).
- Evidence timestamps and band decision are included in claim records for audit.
- Haircuts from epoch caps apply **after** banding and pricing.

---

## 9) Configuration Surface

Key YAML knobs (non-exhaustive):

sla:
windows:
short: {jobs: 200}
long:  {jobs: 2000}
min_samples: 50
confidence: {z: 1.96}
weights: {short: 400000, long: 600000}   # fixed-point 1e6
provisional_band: silver
thresholds:
ai:
qos: {gold: 900000, silver: 800000, bronze: 700000}        # 1e6 scale
quantum:
traps: {gold: 50000, silver: 30000, bronze: 10000}         # 1e6 scale
latency_ms:
p95: {gold: 3000, silver: 6000, bronze: 12000}
availability_lb: {gold: 990000, silver: 975000, bronze: 950000}
fail_rate_max:   {gold: 10000,  silver: 30000,  bronze: 70000}
availability_weights: {hb: 300000, accept: 300000, timely: 400000}
hist_bins_ms: [50,100,200,400,800,1200,2000,3000,6000,12000,24000]

---

## 10) Edge Cases

- **Lease Lost / Queue Faults:** Jobs canceled by the system before assignment do not impact provider SLA.
- **Reorgs:** Metrics derive from dispatcher logs; if a claim disappears on reorg, job metrics remain but economic payouts re-evaluate on canonical chain.
- **Mixed Kinds:** Banding executed per kind; providers may hold different bands for AI vs Quantum simultaneously.
- **Data Gaps:** If heartbeats missing but jobs are timely, availability falls back to accept/timely components (weighted).

---

## 11) References

- Metrics & evaluator: `aicf/sla/metrics.py`, `aicf/sla/evaluator.py`
- Penalties: `aicf/sla/slash_engine.py`, `aicf/registry/penalties.py`
- Economics: `aicf/economics/pricing.py`, `aicf/economics/split.py`
- Proof normalization: `aicf/adapters/proofs.py`
- Tests: `aicf/tests/test_sla_eval.py`, `aicf/test_vectors/slashing.json`

**Invariants**
1. All SLA computations use fixed-point arithmetic, deterministic histogram bins, and policy-defined constants.
2. Band decisions are reproducible given the same logs, windows, and policy root.
3. `q_mult ∈ {pricing.q_mult.{gold,silver,bronze,fail}}`.

