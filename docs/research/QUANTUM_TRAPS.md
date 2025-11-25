# Quantum “Traps”: Families & Detection Power

This note defines the **trap-circuit framework** for auditing *quantum* compute providers in Animica’s PoIES model and AICF SLA. Trap circuits are **verifiable probes** interleaved with user jobs. They are cheaply checkable on the verifier side and statistically powerful against cheating (classical fakery, replay) or excessive noise.

> TL;DR — We insert cryptographically seeded, simulator-cheap circuits (stabilizer, IQP, mirror) with known/estimable output statistics. We test accuracy with binomial/Wilson bounds or cross-entropy scores, compute a per-window confidence bound, and map it into ψ (utility) and AICF SLA outcomes.

---

## 1) Goals

1. **Integrity:** Detect fabricated or stale quantum results.
2. **Quality:** Quantify effective error rates and stability.
3. **Economics:** Provide objective scalars for payouts and slashing.
4. **Practicality:** Keep verifier cost *classical and cheap*; generation uses a PRF seeded from the global randomness beacon.

---

## 2) Threat Model

- **Adversaries:** (a) classical simulation faking “quantum” outputs; (b) low-effort sampling ignoring circuit; (c) replay of old results; (d) over-claiming qubits/depth; (e) excessive noise / drift.
- **Assumptions:** Providers do not know trap identity or expected result; seeds are derived from the **beacon** and job/provider identifiers; circuits & checks are versioned and rotated.

---

## 3) Trap Families

We maintain a **catalog** \(\mathcal{F} = \{F_i\}\). Each family exposes:

- `gen(seed, n, depth, params) -> (circuit, checker)`
- `score(output_shots, checker) -> {metric(s), pass/fail}`

### F1. Clifford Stabilizer Parity (Exact & Efficient)
- **Idea:** Random Clifford \(C\) prepares a stabilizer state \(C|0^n\rangle\). Measure Pauli stabilizers with known parities.
- **Check:** For a set \(\{S_j\}\), each shot’s bitstring must satisfy \(S_j\) parity = expected. Report **stabilizer pass rate** \(p_\text{stab}\).
- **Verifier cost:** \(O(n \cdot \text{shots})\).
- **Power:** Very high against random guessing or replay; sensitive to coherent errors.

### F2. IQP / XZ-Diagonal Quadratic Forms (Classically Checkable)
- **Idea:** Commuting circuits (e.g., \(Z\)-diagonal phases + Hadamards). The **all-zero** outcome probability is a quadratic form over \(\mathbb{F}_2\) computable classically.
- **Check:** Predict \(P(0^n)\) and compare empirical \(\hat{P}(0^n)\). Use **binomial deviation test**.
- **Verifier cost:** \(O(n^2)\) to compute phases; cheap to score.
- **Power:** Detects uniform/noisy impostors; tolerant with moderate samples.

### F3. Mirror Circuits (Roundtrip Reversibility)
- **Idea:** Random gate block \(U\) followed by \(U^\dagger\) should return to \(|0^n\rangle\).
- **Check:** **Return probability** \(P_\text{ret}\) to \(0^n\) should be high; threshold via bounds.
- **Power:** Strong against random/shortcut sampling; probes decoherence and calibration.

### F4. Linear Cross-Entropy / Heavy Output Checks (Anti-Concentration)
- **Idea:** For pseudo-random circuits with computed ideal probabilities \(\{p_x\}\) over a small shot set, compute **cross-entropy** or **heavy-output** fraction (top-quantile outcomes).
- **Check:** Score \( \mathrm{XEB} = 2^n \,\overline{p_x} - 1\) or heavy-output rate vs expected baseline.
- **Power:** Discriminates genuine sampling from uniform or biased classical fakers.

### F5. Hidden-Angle Phase Tags (Few-Qubit “Beacons”)
- **Idea:** Embed a small, secret phase program on 2–4 qubits whose **phase parity** is keyed.
- **Check:** Compare parity distribution to closed-form expectation.
- **Power:** Cheap, resilient; complements F1–F4.

> **Family Rotation:** We rotate parameters (qubits n, depth d), basis choices, and seeded instances each epoch. Each family carries a `family_id` and `version`.

---

## 4) Seeding & Nonce Discipline

- **Seed:** \( s = \mathrm{H}(\text{beacon}_h \| \text{jobId} \| \text{providerId} \| \text{slot}) \)
- **Derivations:** PRF(s) → (n, depth, angles, Clifford tableau, quadratic form).
- **Nonces:** Results are bound to (circuit hash, seed, providerId); replays fail MAC-style checks.

---

## 5) Scoring & Statistics

### 5.1 Binomial/Wilson for Exact-Pass Families
For tests with pass/fail per shot (F1, F3), with \(T\) trap shots and \(C\) passes:
- \(\hat{p}=C/T\).
- **Wilson lower bound** \( \mathrm{LCB}_{\alpha}(\hat{p},T) \) at \(\alpha=10^{-3}\) (see AI_TRAPS.md for formula).

Decision:
- **Pass** if \( \mathrm{LCB} \ge \tau_\text{stab} \) (e.g., 0.90 for stabilizer; 0.80–0.95 by n, depth).
- Else **fail** (de-weight ψ and trigger SLA actions).

### 5.2 Deviation Tests for Probability Checks (F2, F4)
- **All-zero IQP:** \(X \sim \mathrm{Binomial}(T, p_0)\). Two-sided test rejecting \(|\hat{p}_0-p_0|\) beyond critical band; or compute **z-score** with continuity correction.
- **Cross-Entropy (XEB):** Compute \(\mathrm{XEB}\) over a small calibrated subset; compare to expected honest band \([m_\text{honest}-\delta, m_\text{honest}+\delta]\).

### 5.3 Windowing & Aggregation
- Maintain sliding window \(W\) traps per provider (e.g., \(W=500\)).  
- Record per-family metrics and a **composite score**:
\[
\pi = w_1\cdot \mathrm{LCB}_\text{stab} + w_2\cdot \mathrm{LCB}_\text{mirror} + w_3\cdot \mathrm{XEB}_\text{norm} + w_4\cdot Z_\text{IQP}
\]
Clamp \(\pi \in [0,1]\). Map to \(\psi_\text{Q}\) (quantum utility) via policy coefficients.

---

## 6) Detection Power (Back-of-Envelope)

### 6.1 Binomial Power
Against a cheater with effective pass rate \(p_c\) vs honest \(p_h\), Wilson-based pass threshold \(\tau\), approximate **power** (probability to detect cheat) with normal approximation:

1. Choose \(T\) trap shots so that
\[
\mathbb{P}(\mathrm{LCB}( \hat{p},T ) \ge \tau \mid p=p_h) \ge 1-\beta
\]
and
\[
\mathbb{P}(\mathrm{LCB}( \hat{p},T ) \ge \tau \mid p=p_c) \le \alpha
\]

2. For rough sizing, use standard error \(\sigma = \sqrt{p(1-p)/T}\).  
   Solve for \(T\) to separate \(p_h\) and \(\tau\) by \(k\sigma\) (e.g., \(k=3.3\) for 0.1% tail).

**Example:** \(p_h=0.97\), \(\tau=0.90\), \(p_c=0.70\).  
With \(T=30\), power ≈ 95%+ to flag \(p_c\) while honest passes with >99% probability.

### 6.2 Cross-Entropy Sensitivity
Let honest mean \(\mu_h\) and cheater mean \(\mu_c \approx 0\) (uniform). With per-shot variance \(v\), number of scored shots \(T_s\):
\[
Z = \frac{\overline{\mathrm{XEB}} - \tau_\mathrm{xeb}}{\sqrt{v/T_s}}
\]
Pick \(T_s\) so that \(Z\) exceeds target quantile (e.g., 3.0) under \(\mu_h\), and falls below under \(\mu_c\).

---

## 7) Receipts & Verifier Interface

Each trap yields a **QuantumTrapReceipt**:
- `family_id`, `version`, `n`, `depth`, `circuit_hash`
- `seed_commitment` \(H(s)\), `shot_count`, `metric(s)` (pass rate, XEB, z-scores)
- `verdict` (pass/fail), latency stats
- Optional TEE quote, device id, queue id

Receipts aggregate into the **QuantumProof** metrics used by `proofs/quantum.py` (ψ inputs and SLA).

---

## 8) Policy & Thresholds (Suggested Defaults)

| Family | Metric | Threshold (Illustrative) | Min Shots |
|---|---|---:|---:|
| F1: Stabilizer | Wilson LCB (99.9%) | ≥ 0.90 | 24 |
| F3: Mirror | Wilson LCB (99.9%) | ≥ 0.85 | 24 |
| F2: IQP P(0…0) | |z| ≤ 3.0 vs ideal | 200 |
| F4: XEB / Heavy | XEB ≥ τ_xeb (by n, d) | τ per calibration | 200 |

Composite pass requires: all families with weight > 0 have metric ≥ threshold in-window.

**SLA Actions**
- **Warn / de-weight ψ** on first failure.
- **Temporary jail** on 2 consecutive window failures.
- **Slash** on sustained failure or policy-trap violation.

---

## 9) Calibration & Drift

- Calibrate per **device class** (ion trap, superconducting, neutral atom) and size \(n\).
- Maintain a reference *honest band* for each family & depth; update quarterly.
- Drift monitor: CUSUM/EMA over pass metrics to flag hardware degradation.

---

## 10) Evasion & Countermeasures

- **Replay/Memoization:** Seeded circuit hashes + provider-bound commitments prevent reuse.
- **Classical faking:** Families chosen for **cheap verify yet quantum-typical** statistics (mirror, IQP) that are difficult to fake consistently without the actual circuit.
- **Partial compute:** Randomize which shots are scored; include **canary sub-circuits** across shots.
- **Adversarial alignment:** Rotate families/versions; diversify basis and depth distribution.

---

## 11) Implementation Sketch (Pseudocode)

```python
def score_stabilizer(shots, tableau, checks):
    # checks: list of Pauli parity predicates derived from tableau
    passes = sum(1 for b in shots if all(pred(b) for pred in checks))
    T = len(shots)
    p_hat = passes / T
    lcb = wilson_lcb(p_hat, T, z=3.29)  # ~99.9%
    return {"passes": passes, "T": T, "lcb": lcb, "ok": lcb >= 0.90}

def score_iqp_allzero(shots, p0):
    T = len(shots)
    x = sum(1 for b in shots if int(b, 2) == 0)
    p_hat = x / T
    z = (p_hat - p0) / max(1e-9, ((p0*(1-p0)/T)**0.5))
    return {"p_hat": p_hat, "z": z, "ok": abs(z) <= 3.0}

def score_mirror(shots):
    T = len(shots)
    ret = sum(1 for b in shots if int(b, 2) == 0)
    p_hat = ret / T
    lcb = wilson_lcb(p_hat, T, z=3.29)
    return {"ret": ret, "T": T, "lcb": lcb, "ok": lcb >= 0.85}

def score_xeb(shots, ideal_probs):
    # small evaluated subset of outcomes with known p(x)
    px = [ideal_probs.get(b, 0.0) for b in shots]
    xeb = (2**n) * (sum(px)/len(px)) - 1.0
    ok = xeb >= tau_xeb(n, depth)
    return {"xeb": xeb, "ok": ok}


⸻

12) Example Numbers
	•	n=16, depth=8, mirror: honest (p_\text{ret}\approx 0.93), (T=30) ⇒ Wilson LCB ≈ 0.88–0.90 → close to τ; choose (T\ge 40) for margin.
	•	n=20, depth=12, XEB: honest mean 0.08 with σ≈0.03 at 200 shots; τ=0.02 cleanly separates from uniform (0.0) with >99% confidence.

⸻

13) Governance & Transparency
	•	Publish family descriptions and target thresholds; keep seeds/instances private until epoch end.
	•	Record artifacts (circuit hash, parameters, metrics) for third-party audit.
	•	Rotate families and re-calibrate thresholds on hardware/firmware changes.

⸻

14) Checklists

Design
	•	Classical-checkable or small-subset ideal probabilities computed
	•	Distinguishes uniform/replay from honest sampling
	•	Cheap scoring (≤ 1 ms/shot equivalent on CPU)
	•	Seeded & provider-bound

Ops
	•	Windowed Wilson / deviation tests with α=1e-3
	•	Receipts aggregated into proof metrics
	•	Alerts on dips; dashboards by family
	•	Version/rotation schedule

⸻

Summary. Quantum trap families give statistically strong, verifier-cheap signals of honest quantum execution. Combined with attestations and SLA rules, they make classical fakery or degraded operation measurably unprofitable under Animica’s PoIES incentives.
