# AICF SLA — Latency, Quality, Redundancy, Penalty Schedule

This document defines the **Service Level Agreement (SLA)** for AI/Quantum providers
participating in the AI Compute Fund (AICF). It specifies measured metrics, evaluation
windows, target thresholds, penalties, and recovery rules. The evaluator lives in:

- Code: `aicf/sla/{metrics.py,evaluator.py,slash_engine.py}`
- Policy knobs (network-defined): `aicf/config.py`, `aicf/policy/example.yaml`
- Registry/penalties integration: `aicf/registry/{heartbeat.py,penalties.py}`

> All measurements are recorded with **monotonic timestamps**, aggregated per **epoch**
> (configurable block/time length), and persisted for auditability.

---

## 1) Scope

Applies to all **ACTIVE** providers (AI and/or Quantum capability). A provider must:
1. Accept assigned jobs (leases).
2. Return verified results/proofs within **latency targets**.
3. Maintain **quality & availability** above thresholds.
4. Respect **redundancy policies** where multiple results are required.

---

## 2) Metrics (per provider)

### 2.1 Latency
- **Definition:** `t_complete - t_assign`, in seconds, measured server-side on the queue.
- **Stats:** p50, p90, **p95**, max.
- **Out-of-band caps:** absolute timeout `job_ttl_s`.
- **Exclusions:** jobs canceled by dispatcher before lease start are ignored.

### 2.2 Availability
- **Definition:** `completed_jobs / assigned_jobs` (within epoch), excluding dispatcher cancels.
- **Windowed:** exponential decay with half-life `H_avail` (e.g., 24h).

### 2.3 Quality (AI)
- **QoS Score (0..1):** deterministic function of normalized metrics:
  - correctness proxies (checksum, output schema),
  - **redundancy agreement** (see 2.5),
  - throughput stability (jitter bounds),
  - optional evaluator-specific checks (toxicity guard, etc.).
- **Traps Ratio:** fraction of **known trap prompts** answered within acceptable bounds.
  - Threshold: `traps_ratio >= traps_min` (e.g., 0.985).

### 2.4 Quality (Quantum)
- **Trap Families Pass Rate:** pass / total traps (Clifford, T-depth suites).
- **Attest Continuity:** attestation hash unchanged or re-verified on rotation.
- **Shot QoS:** variance and success probability within policy bounds.

### 2.5 Redundancy Agreement (k-of-n)
When policy requests redundancy (e.g., `k=2 of n=3`):
- **Agreement Rate:** fraction of jobs where at least `k` results match within tolerance.
- **Tolerance:** hash-equality for exact outputs, or metric band (e.g., MSE ≤ ε) for ML.

### 2.6 Validity
- **Proof Validity Rate:** `valid_proofs / submitted_proofs` (post-verification in `proofs/*`).
- Any **invalid proof** is treated as a **critical fault**.

---

## 3) Evaluation Windows

- **Epoch:** accounting bucket (e.g., 1 hour or N blocks). Used for payouts and slashing.
- **Rolling Windows:** EWMA with half-lives `H_lat`, `H_qos`, `H_avail` (policy parameters).
- **Grace Period:** new providers have grace epochs `G` (reduced penalties; still measured).

---

## 4) Targets & Thresholds (defaults; network may override)

```yaml
# Example policy (see aicf/policy/example.yaml)
sla:
  job_ttl_s: 60
  latency:
    p95_ai_s: 10
    p95_quantum_s: 30
    H_lat_hours: 24
  availability:
    min: 0.98
    H_avail_hours: 24
  ai:
    traps_min: 0.985
    qos_min: 0.95
  quantum:
    traps_min: 0.98
    shot_qos_min: 0.95
  redundancy:
    enabled: true
    k: 2
    n: 3
    agree_min: 0.97
  weights:     # composite score weights
    w_latency: 0.35
    w_avail:   0.25
    w_quality: 0.25
    w_agree:   0.15


⸻

5) Composite SLA Score

For a provider in an epoch (or EWMA window), compute:

latency_ok  = clamp01( target_p95 / max(eps, p95_obs) )          # higher is better
avail_ok    = clamp01( (avail_obs - min_avail) / (1 - min_avail) )
quality_ok  = clamp01( min(qos_obs / qos_min, traps_obs / traps_min) )
agree_ok    = clamp01( agree_obs / agree_min )

S = w_latency*latency_ok + w_avail*avail_ok + w_quality*quality_ok + w_agree*agree_ok

Where clamp01(x)=min(max(x,0),1). Networks may swap in piecewise penalties for tails.

⸻

6) Fault Classes & Penalties

Class	Trigger (examples)	Immediate Action	Epoch Penalty (stake/weight)
Minor	S < 0.9 once; p95 over target by ≤2×; avail in [0.95, 0.98)	reduce assignment weight -20% for next epoch	Demerit 1; auto-clear after 2 clean epochs
Major	S < 0.8, avail <0.95, traps below min once, redundancy agree <0.95	weight -50%, temporary jail (1 epoch)	Demerit 2, Slash 0.25% of bonded stake
Critical	Invalid proof, repeated timeouts (≥3 in epoch), forged attestation, lease drop	Immediate jail, revoke leases, notify ops	Slash 2–5% of bonded stake; cooldown C epochs

Notes:
	•	Slashing magnitudes and cooldown C are policy-configurable.
	•	Repeated majors in rolling window may escalate to critical.
	•	During grace (G epochs), replace slashes with extended jailing unless security-critical.

⸻

7) Recovery & Appeals
	•	Auto-Recovery: after a clean epoch (S ≥ 0.95 and no majors), weights gradually restore (+20%/epoch).
	•	Appeal Window: provider may submit logs and signed metrics within A epochs for re-evaluation.
	•	Audit Trail: all penalties recorded with reason codes (aicf/sla/slash_engine.py) and hashes of evidence.

⸻

8) Measurement & Telemetry

Heartbeat payload (aicf/registry/heartbeat.py):

{
  "provider_id": "provider:abc123",
  "attest_hash": "0x…",
  "metrics": {
    "latency_p95_s": 8.7,
    "availability": 0.991,
    "ai": { "qos": 0.964, "traps_ratio": 0.988 },
    "quantum": { "traps_ratio": null, "shot_qos": null },
    "redundancy_agree": 0.975
  },
  "load": { "leases_active": 3, "leases_capacity": 8 },
  "timestamp": 1713202001,
  "signature": "0x<sig>"
}

Dispatcher confirms job outcomes (success/timeout/cancel) and emits per-job records used by the evaluator.

⸻

9) Redundancy Rules
	•	When redundancy is enabled (k-of-n):
	•	All n results are requested in parallel (or staggered).
	•	Earliest k passing agreement gate settlement; late arrivals still counted for SLA.
	•	Disagreeing providers are penalized by quality/agree components.

⸻

10) Example Outcomes
	•	AI provider, good state: p95=7s, avail=0.995, qos=0.97, traps=0.99, agree=0.98 → S≈0.97 (healthy).
	•	Quantum provider, slow: p95=45s vs 30s target → latency_ok≈0.67; others OK → S≈0.86 → Minor.
	•	Invalid proof: immediate Critical → jail, slash, cooldown.

⸻

11) Penalty Encoding (on-chain/off-chain)
	•	Reason Codes: SLA_MINOR, SLA_MAJOR, INVALID_PROOF, DROPPED_LEASE, ATTEST_FORGED.
	•	Record: {provider_id, epoch, reason, delta_stake, cooldown_epochs, evidence_hash}.
	•	Integration: penalties propagate to aicf/treasury/state.py and registry status.

⸻

12) Tuning Guidance
	•	Start with conservative targets; tighten as network stabilizes.
	•	Prefer weight reductions before slashes unless safety impacted.
	•	Keep trap suites rotating; publish public seeds post-epoch for audit.
	•	Separate policy for burst epochs (surge demand) to avoid unfair slashing.

⸻

13) References
	•	docs/aicf/OVERVIEW.md, docs/aicf/JOB_API.md
	•	docs/economics/SLASHING.md
	•	proofs/* (attest & proof verification)
	•	aicf/metrics.py (Prometheus: latency histograms, availability gauges)

