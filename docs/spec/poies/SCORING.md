# PoIES Scoring — ψ Contributions, Caps, & Diversity Bonus

This document defines how **evidence scores** (ψ) are computed from proof metrics, then bounded by **caps**, and finally adjusted by **diversity/escort rules** before the acceptance predicate

> **S = H(u) + Σ ψ_eff ≥ Θ**

is evaluated. See the overview in `POIES_OVERVIEW.md`.

---

## 1) Inputs & Terminology

- **Proof** `p`: one attached evidence item of a permitted type `t ∈ {AI, Quantum, Storage, VDF}`. (The *hash share* is accounted separately as `H(u)` and does **not** contribute ψ.)
- **Metrics** `m(p)`: typed, verifiable measures emitted by the corresponding verifier (e.g., `ai_units`, `traps_ratio`, `qos`, `vdf_seconds`, `redundancy`).
- **Raw score** `ψ_raw(p) ≥ 0`: continuous score from mapping metrics → utility (before any caps).
- **Per-proof cap** `C_proof[t]`: maximum allowed ψ for a single proof of type `t`.
- **Per-type cap** `C_type[t]`: maximum allowed Σψ for all proofs of the same type `t` in a candidate block **after** per-proof caps and diversity adjustments.
- **Total cap** `Γ`: global maximum of Σψ across all proofs in the candidate block.
- **Escort/diversity policy**: additional rules that either unlock higher caps or apply a smooth bonus factor when multiple work types are present.

All numeric parameters are configured in `spec/poies_policy.yaml` and loaded by `consensus/policy.py`. Unit tests in `consensus/tests/` validate the semantics.

---

## 2) Mapping Metrics → Raw ψ

Each proof type defines a **monotone, concave** mapping from its metrics to a nonnegative scalar:

ψ_raw(p) = w[t] · f_t(m(p); θ_t)

Where:
- `w[t]` is a type-level weight (units → ψ scale).
- `f_t(·)` is a type-specific utility curve with parameters `θ_t` (kept small & interpretable).
- `ψ_raw(p) ≥ 0`, with `ψ_raw(p) = 0` if metrics are at minimal admissible values.

### 2.1 Suggested Utility Curves

To avoid corner solutions, curves should be **concave** or **piecewise-concave**:

- **Affine clamp**: `f(x) = clamp(a·x + b, 0, u_max)`
- **Log**: `f(x) = k · ln(1 + x/x0)`  
- **Saturating rational**: `f(x) = k · x / (x + x0)`
- **Piecewise** (tiers): increasing slopes at small x then flattening.

Examples (non-normative defaults):

- **AI**: `f_AI(ai_units, traps_ratio, qos) = k1·ln(1 + ai_units/u0) · g_traps(traps_ratio) · g_qos(qos)`  
  with monotone modifiers `g_traps, g_qos ∈ [0,1]`.
- **Quantum**: `f_Q(quantum_units, traps_ratio) = k2·ln(1 + quantum_units/q0) · g_traps(traps_ratio)`.
- **Storage**: `f_S(qos, redundancy) = k3·ln(1 + redundancy/r0) · g_qos(qos)`.
- **VDF**: `f_V(vdf_seconds) = k4·ln(1 + vdf_seconds/v0)`.

Modifiers `g_*` are simple clamps or sigmoids that reduce ψ when quality thresholds are not met.

**Implementation**: `proofs/policy_adapter.py` converts `ProofMetrics` → ψ inputs using parameter values from `consensus/policy.py`.

---

## 3) Caps & Aggregation Order

**Order matters** to ensure determinism and prevent circular effects. We apply:

1. **Per-proof caps**  
   `ψ_cap(p) = min(ψ_raw(p), C_proof[type(p)])`

2. **Diversity / Escort adjustment** (see §4)  
   Produces a **per-type multiplier** `β_t ∈ [1, β_max]` or **tier unlocks** that expand caps conditionally:
   - `ψ_div(p) = β_type(p) · ψ_cap(p)`

3. **Per-type caps**  
   For each type `t`:  
   `Ψ_type[t] = min( Σ_{p∈t} ψ_div(p), C_type_eff[t] )`  
   where `C_type_eff[t]` may itself depend on escort/diversity state (tiered unlocks).

4. **Total Γ cap**  
   `Σψ_eff = min( Σ_t Ψ_type[t], Γ )`

`Σψ_eff` is the aggregate ψ that enters the acceptance predicate.  
Caps are **hard**: if any limit is hit, excess ψ is discarded without reallocation.

**Implementation**:
- Per-proof: `consensus/caps.py`
- Per-type & Γ: `consensus/caps.py`
- Aggregation & acceptance: `consensus/scorer.py`

---

## 4) Diversity / Escort Rules

We provide two interoperable modes. Networks can pick one or combine both (with clear precedence) in `poies_policy.yaml`.

### 4.1 Tiered Unlocks (Escort)

**Goal**: unlock higher per-type caps only when *escorted* by other work types.

- **Policy** declares tiers per type `t`:  
  `tiers[t] = [{cap: c1, requires: {S: s1, V: v1}}, {cap: c2, requires: {...}}, ...]`
- For candidate block, compute provisional per-type sums `Σ_{p∈t} ψ_cap(p)` and **escort availability**:

escort_ok(level L for t) ⇔
for each required type r in tiers[t][L].requires:
Σ_{p∈r} ψ_cap(p) ≥ requires[r]

- Effective per-type cap `C_type_eff[t]` is the **highest tier** whose requirements are satisfied.

This encourages **portfolio construction**, e.g., “AI beyond 30 μ-nats requires Storage ≥ 5 μ-nats and VDF ≥ 2 μ-nats”.

### 4.2 Smooth Diversity Bonus

**Goal**: reward balanced mixes continuously without hard thresholds.

Define a **diversity index** `D ∈ [0,1]`. Choices include:
- **Normalized min-ratio**:

D = min_r ( Σ_{p∈r} ψ_cap(p) / R_r )  clipped to [0,1]

where `R_r` are per-type reference escorts.
- **Entropy-style** (bounded):  
`D = H_normalized( share[t] )` with `share[t] = Σ_{p∈t} ψ_cap(p) / Σ_all ψ_cap(·)` and `H_normalized ∈ [0,1]`.

Then apply **per-type multipliers**:

β_t = 1 + b_t · D          with 0 ≤ b_t ≤ b_max
ψ_div(p) = β_type(p) · ψ_cap(p)

**Precedence**: diversity multipliers run **before** per-type caps; tiered unlocks adjust the per-type caps themselves. If both are enabled:
1) Apply smooth multipliers; 2) Evaluate tier requirements; 3) Apply per-type caps.

---

## 5) α-Tuner (Fairness Correction)

To avoid long-run dominance by a single type under noisy markets, we allow a **slow α-correction** across epochs (outside single-block scoring):

- Track trailing shares `share_t` over window `W`.
- Compute error vs. **target shares** `τ_t` (policy).
- Adjust `w[t]` or a small `α_t` multiplier within tight clamps:

α_t(next) = clamp( α_t + η · (τ_t − share_t),  α_min, α_max )
ψ_raw_typed := α_t · w[t] · f_t(·)

Parameters `η, α_min, α_max, W` live in policy. Tested in `consensus/tests/test_alpha_tuner.py`.

---

## 6) Gas & Runtime Bounds

Each proof type has a **max verification gas** and **runtime upper bound**, enforced by:
- Pre-admission screening (e.g., size checks)
- Deterministic verifiers (no network I/O, no unbounded allocations)
- Gas metering (execution layer) for on-chain or near-chain checks

If verification exceeds bounds, the **proof is rejected** (ψ=0). Policies should ensure the *marginal ψ* cannot rationalize denial-of-service attempts.

---

## 7) Pseudocode (Normative)

```python
def score_block(proofs, policy) -> float:
  # 1) raw → per-proof cap
  capped = []
  by_type_provisional = defaultdict(float)
  for p in proofs:
      t = p.type
      psi_raw = weight[t] * f_t(p.metrics, policy.theta[t])
      psi_cap = min(max(psi_raw, 0.0), policy.caps.proof[t])
      capped.append((t, psi_cap))
      by_type_provisional[t] += psi_cap

  # 2) diversity escort
  if policy.escort.enable_smooth:
      D = diversity_index(by_type_provisional, policy)
      beta = {t: 1.0 + policy.escort.smooth.b[t] * D for t in TYPES}
  else:
      beta = {t: 1.0 for t in TYPES}

  # Apply multipliers pre cap
  adjusted = defaultdict(float)
  for t, psi_cap in capped:
      adjusted[t] += beta[t] * psi_cap

  # 3) tiered unlocks → effective per-type caps
  C_eff = {}
  for t in TYPES:
      C_eff[t] = compute_tiered_cap(t, adjusted, policy)  # highest unlocked cap

  # 4) per-type caps
  psi_type = {t: min(adjusted[t], C_eff[t]) for t in TYPES}

  # 5) total Γ cap
  total = sum(psi_type.values())
  return min(total, policy.caps.gamma_total)

Determinism requires stable sorting for ties and consistent floating semantics (we use fixed-point μ-nats internally in consensus; see consensus/math.py).

⸻

8) Worked Example

Policy (excerpt):
	•	C_proof: AI=8, Q=8, S=6, V=4 (μ-nats)
	•	Base per-type caps: C_type: AI=24, Q=16, S=12, V=8
	•	Γ (total) = 32
	•	Smooth diversity: b_AI=0.10, b_Q=0.10, b_S=0.10, b_V=0.05, refs: R_S=4, R_V=2
	•	Tiered unlock for AI:
	•	Tier0: cap=16 (no escort)
	•	Tier1: cap=24 if S≥4 and V≥2 (after per-proof caps, pre-multiplier)

Candidate proofs (after raw mapping & per-proof cap):
	•	AI: three proofs → 8 + 6 + 5 = 19
	•	Storage: one proof → 4
	•	VDF: one proof → 2
	•	Quantum: none → 0

Diversity smooth:
	•	Provisional sums: AI=19, S=4, V=2 → ref met ⇒ D = min(4/4, 2/2) = 1
	•	Multipliers: β_AI=1.10, β_S=1.10, β_V=1.05
	•	Adjusted: AI=20.9, S=4.4, V=2.1

Tier unlock:
	•	AI Tier1 requirements met (S≥4, V≥2) ⇒ C_eff[AI]=24 (vs Tier0=16)
	•	Others keep base caps.

Per-type caps:
	•	AI=min(20.9, 24)=20.9
	•	S=min(4.4, 12)=4.4
	•	V=min(2.1, 8)=2.1
	•	Q=0

Total Γ:
	•	Σψ = 27.4 ≤ 32 ⇒ Σψ_eff = 27.4 μ-nats

The acceptance predicate will accept when H(u) ≥ Θ − 27.4.

⸻

9) Edge Cases & Invariants
	•	Nonnegativity: ψ_raw ≥ 0; negatives are clamped to 0.
	•	Monotonicity: Increasing any productive metric never reduces ψ_raw (before caps).
	•	Idempotent capping: Reapplying caps does not change results.
	•	Deterministic arithmetic: Consensus uses fixed-point integers (μ-nats) to avoid FP non-determinism.
	•	Size & time guards: Oversized or slow proofs ⇒ reject (ψ=0).
	•	Escort safety: Escort conditions depend only on already-capped sums to prevent recursion.
	•	Γ dominance: Σψ_eff ≤ Γ always (even if per-type caps are large).

⸻

10) Policy Keys (Reference)

In spec/poies_policy.yaml:

weights:
  AI:  k1
  Quantum: k2
  Storage: k3
  VDF: k4

curves:
  AI: { kind: log, k: ..., x0: ..., traps: {min: ...}, qos: {min: ...} }
  Quantum: { kind: log, k: ..., x0: ..., traps: {min: ...} }
  Storage: { kind: log, k: ..., x0: ..., qos: {min: ...}, redundancy: {...} }
  VDF: { kind: log, k: ..., x0: ... }

caps:
  proof: { AI: 8, Quantum: 8, Storage: 6, VDF: 4 }
  type:  { AI: 24, Quantum: 16, Storage: 12, VDF: 8 }
  gamma_total: 32

escort:
  smooth:
    enable: true
    refs: { Storage: 4, VDF: 2 }
    beta: { AI: 0.10, Quantum: 0.10, Storage: 0.10, VDF: 0.05 }
  tiers:
    AI:
      - { cap: 16 }  # Tier0
      - { cap: 24, requires: { Storage: 4, VDF: 2 } }  # Tier1
    # ... optional tiers for other types ...

alpha_tuner:
  enable: true
  targets: { AI: 0.40, Quantum: 0.20, Storage: 0.25, VDF: 0.15 }
  window_blocks: 2048
  step: 0.01
  clamps: { min: 0.8, max: 1.2 }


⸻

11) Testing & Vectors
	•	Unit: consensus/tests/test_caps.py (per-proof/type/Γ), test_scorer_accept_reject.py (around Θ), test_alpha_tuner.py (fairness).
	•	Vectors: spec/test_vectors/proofs.json (ψ inputs & caps), headers.json (Θ/retarget).
	•	Integration: mining/tests/test_proof_selector.py (pack under caps & escort rules).

⸻

12) Notes on Upgrades

Changes to curves, weights, caps, or escort policy alter ψ semantics and must be:
	•	versioned in poies_policy.yaml,
	•	hashed into the policy root pinned in headers,
	•	rolled out via the upgrade process (docs/spec/UPGRADES.md).

