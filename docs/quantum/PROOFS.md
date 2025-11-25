# Quantum/PROOFS — Job Model, Trap Families, and Fraud Proofs

**Status:** Stable (v1)  
**Audience:** protocol engineers, provider operators, auditor tools  
**Related:** `docs/spec/proofs/QUANTUM_V1.md`, `aicf/specs/*`, `proofs/quantum_attest/*`

This document specifies how **Quantum jobs** are modeled, which **trap families** we use to detect cheating or degradation, and how **fraud proofs** are constructed and evaluated. It complements the normative wire format in `QUANTUM_V1.md`.

---

## 1) Job Model

A **Quantum Job** represents a parameterized circuit evaluation with explicit audit hooks.

### 1.1 JobSpec

- `circuit_desc`: abstracted family id + parameters (depth, width, connectivity hints).
- `shots`: number of repetitions.
- `trap_plan`: seed + schedule that deterministically inserts trap circuits among target circuits.
- `qos_targets`: soft constraints (latency budget, availability).
- `pricing_hint`: estimated quantum-units for economics.

Jobs are enqueued via capabilities (`capabilities/host/compute.py`) and assigned by AICF. Providers return a **QuantumProof v1** that contains:
- Provider attestation bundle
- Trap outcomes summary
- Benchmarks (depth×width×shots)
- QoS measures (latency, availability, error-rate proxies)
- Proof nullifier & linkage (job_id/task_id)

See `docs/spec/proofs/QUANTUM_V1.md` for field-level definitions.

---

## 2) Trap Families

**Traps** are circuits with publicly verifiable expectations (known output distributions). We mix traps and target circuits using a deterministic, seed-derived schedule so a dishonest provider cannot reliably skip or spoof traps without detection.

We currently define four core families:

### 2.1 Clifford Stabilizer Traps (CST)
- **Idea:** Clifford-only circuits on |0…0⟩ whose final Pauli stabilizers are known.
- **Expectation:** Deterministic outcomes (up to global phase).  
- **Use:** High-sensitivity correctness check.  
- **Metric:** fraction of perfect matches.

### 2.2 Randomized Benchmarking (RB) Probes
- **Idea:** Random sequences of Clifford gates with a final inverting gate should return to |0…0⟩.
- **Expectation:** Success probability decays with sequence length; fitted error per Clifford (EPC).
- **Use:** Coarse-grained fidelity + drift detection.  
- **Metric:** fitted EPC vs provider baseline.

### 2.3 Heavy Output Generation (HOG) Checks
- **Idea:** For certain pseudo-random circuits, “heavy” outputs (above median ideal probability) should appear with frequency > 2/3 (in idealized settings).
- **Expectation:** Heavy-output frequency threshold.  
- **Use:** Non-Clifford coverage; catches classical re-labeling attacks.  
- **Metric:** heavy-fraction and confidence interval.

### 2.4 IQP/Diagonal-in-X Traps (IQP)
- **Idea:** Instantaneous Quantum Polynomial-like circuits with known Fourier structure lead to predictable bias patterns on parities.
- **Expectation:** Specific parity bias > threshold.  
- **Use:** Sampling hardness proxy; catches simplistic simulators.  
- **Metric:** parity-bias z-scores.

> Families are versioned. Parameters (depth ranges, widths, mixing weights) are policy-controlled and included in the proof’s metadata.

---

## 3) Trap Scheduling

Given `(seed, traps_ratio, total_shots)`, we derive a deterministic schedule:

trap_indices = PRNG(seed).choose(total_shots, size=⌊traps_ratio·total_shots⌋, without_replacement)
for i in 0..total_shots-1:
if i in trap_indices: run TrapFamily.sample(seed, i)
else: run TargetCircuit.sample(seed, i)  # still seeded for replay/audit

Scheduling and circuit-generation must be **reproducible** from the seed and parameters to enable independent auditing.

---

## 4) Evaluation & Statistics

### 4.1 Per-Family Metrics
For each family `f`, compute a test statistic `T_f` and an **estimated p-value** under the null hypothesis “provider behaves at/above agreed fidelity”.

Examples:
- CST: `T = (# matches)` with exact binomial tail `p = BinomTest(n, k, p0=1−ε)`.
- RB: fit EPC via nonlinear least squares; test residuals and EPC vs policy bands.
- HOG: `T = (# heavy)`; `p = BinomTest(n, k, p0=2/3 − δ)`.
- IQP: z-score of observed parity biases vs ideal; p from normal approximation (or permutation).

### 4.2 Multi-Test Aggregation
We combine per-family evidences using **Fisher’s method** or **Tippett’s** (policy-chosen). Let `p_comb` be the aggregated p-value. The proof passes traps if:

p_comb ≥ p_min  AND  (∀f: family-specific thresholds satisfied)

Network policy (`spec/poies_policy.yaml`) pins: minimum trap coverage, p-value floors, and EPC/latency bands.

---

## 5) Fraud Proofs

A **Fraud Proof** demonstrates, with high confidence, that a returned QuantumProof is inconsistent with the declared circuit family or expected distributions.

### 5.1 Constructing a Fraud Proof
- Inputs: `(seed, schedule, circuit_params, raw measurement records)` extracted from the provider’s proof or accompanying logs.
- Recompute trap circuits deterministically from the seed.
- Re-evaluate expected outcomes/distributions (no secrets required).
- Run statistical tests; if `p_comb < p_fraud` (e.g., 1e−6), assemble:

```json
{
  "type": "quantum_fraud_v1",
  "job_id": "...",
  "task_id": "...",
  "seed": "0x...",
  "trap_family_stats": [
    {"family":"CST","n":4096,"k":3971,"p_val":1.2e-9},
    {"family":"HOG","n":2048,"k":1120,"p_val":7.5e-5}
  ],
  "p_comb": 2.3e-11,
  "evidence": {
    "sample_hash": "sha3_256 of raw shots",
    "indices": "compressed trap indices bitmap or range list"
  }
}

The evidence must allow independent recomputation without exposing user-private target data. Raw shots may be provided as commitments plus a selective opening for trap indices.

5.2 On-Chain/Off-Chain Handling
	•	On-chain: We avoid large blobs; contracts or system handlers accept compact summaries (hashes, counts, p-values), with raw data retrievable via DA/Blob storage when necessary.
	•	Off-chain (AICF): SLA/economics modules ingest full Fraud Proofs; consistent failures trigger slashes, jailing, or stake reductions as per aicf/specs/SLA.md and aicf/specs/SECURITY.md.

⸻

6) QoS & Availability

Correctness alone isn’t enough. The proof also carries latency, availability, and error-rate proxies:
	•	latency_ms vs bands.
	•	availability over the lease window.
	•	error_rate from RB/EPC or provider telemetry.

Policy combines correctness (traps) and QoS to compute ψ inputs; see proofs/policy_adapter.py and consensus caps in consensus/caps.py.

⸻

7) Security Considerations
	•	Schedule secrecy: The trap schedule is not secret; it is deterministic from seed. Security stems from randomness and coverage, not obscurity.
	•	Replay resistance: Each proof includes a nullifier tied to header/height context; reuse is rejected.
	•	Classical simulation risk: Trap families are chosen to make naive classical spoofing statistically detectable. We do not claim post-quantum hardness—this is an audit mechanism, not a proof-of-quantum-advantage.
	•	Drift & cherry-pick: Randomized scheduling across the lease window thwarts selective skipping. Availability penalties discourage cherry-picking only “good” shots.
	•	Provider collusion: Independent registry & attestation plus trap performance histories make long-term collusion detectable (leaderboards and public metrics planned).

⸻

8) Pseudocode: Trap Verification

def verify_traps(seed, total_shots, traps_ratio, families, records):
    idx = derive_trap_indices(seed, total_shots, traps_ratio)
    fam_stats = []
    for fam in families:
        expected = fam.gen_expected(seed, indices=idx, params=fam.params)
        observed = records.select(indices=idx, mask=fam.mask)
        T, p = fam.test_stat(expected, observed)
        fam_stats.append((fam.name, T, p))
    p_comb = combine_pvalues([p for _,_,p in fam_stats], method="fisher")
    return fam_stats, p_comb

Acceptance: p_comb ≥ p_min and per-family tests within thresholds.
Fraud: p_comb < p_fraud or any hard per-family violation.

⸻

9) Data Availability & Auditing
	•	Raw shots or compressed encodings SHOULD be committed via DA (da/blob/*) with content-addressed receipts referenced in proofs.
	•	Auditors fetch the DA blobs, recompute traps, and publish Fraud Proofs when applicable.

⸻

10) Versioning & Policy
	•	Trap families are versioned (family_id@v).
	•	Networks may update parameters via governance; changes are reflected in aicf/policy/* and spec/poies_policy.yaml.
	•	Verifiers MUST reject unknown family versions unless explicitly allowed by policy.

⸻

11) Worked Example (Sketch)
	•	shots = 8192, traps_ratio = 0.5, seed = 0xabc….
	•	Families: CST@v1 (60%), HOG@v1 (40%) of trap slots.
	•	Observed: CST matches k=4032/4096; HOG heavy k=2810/4096.
	•	Tests:
	•	CST binomial p = 3.1e-5 vs threshold 1e-4 → pass.
	•	HOG binomial p = 4.2e-3 vs threshold 1e-3 → fail.
	•	Combine (Fisher): p_comb = 1.7e-4; policy requires p_min ≥ 1e-3 → reject; publish Fraud Proof with commitments to trap shots.

⸻

12) References
	•	proofs/quantum_attest/traps.py — trap math & helpers
	•	aicf/sla/evaluator.py — SLA & thresholds
	•	docs/spec/proofs/QUANTUM_V1.md — canonical proof fields
	•	da/* — data availability pipeline

⸻

Changelog
	•	v1: Define job model, four trap families, aggregation & fraud proof schema.
