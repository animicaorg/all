# Fork Choice — Aggregate PoIES Work & Reorg Limits

This document specifies the canonical fork-choice rule used by Animica full nodes and light clients. It is implemented by `consensus/fork_choice.py` and exercised in `consensus/tests/test_fork_choice.py` and end-to-end tests.

Related specs:
- **PoIES math**: `spec/poies_math.md` — defines block acceptance score `S = H(u) + Σψ` and the schedule `Θ`.
- **Block/Header format**: `docs/spec/BLOCK_FORMAT.md`.
- **Validator**: `consensus/validator.py` — recomputes `S`, enforces policy roots and acceptance predicate `S ≥ Θ`.

---

## 1) Intuition

- Each block carries **evidence of work**: a mix of a **hash draw** (`H(u) = −ln u`) and **useful proofs** (AI/Quantum/Storage/VDF) converted into additive units `ψ` by policy.
- The **acceptance predicate** is `S = H(u) + Σψ ≥ Θ`.  
- Fork choice selects the chain with the **greatest cumulative work**, not simply the tallest height.

---

## 2) Per-block effective work

Let a block at height *i* have recomputed score `S_i` and the network’s target threshold `Θ_i` (post-retarget). We define a bounded, strictly positive **effective work**:

w_i = clamp( S_i,  Θ_i,  Θ_i + Δ_cap )

Where:

- `S_i` and `Θ_i` are expressed in **µ-nats** (see `consensus/math.py`).
- `Δ_cap` is a policy constant that limits outlier dominance (e.g., `Δ_cap = 4·σ_target`, default: **4.0 µ-nats** in tests; configurable in network params).
- Blocks **must** satisfy `S_i ≥ Θ_i` to be valid; the clamp ensures `w_i ∈ [Θ_i, Θ_i+Δ_cap]`.

> Rationale: PoIES is additive; using `S_i` (bounded) yields a stable aggregate independent of transient jackpot outliers while preserving incentives to include useful proofs.

---

## 3) Chain work and selection

For a chain tip **T**, define its **cumulative work**:

W(T) = Σ_{b ∈ path(genesis→T)} w_b

### Selection rule (strict order):

Given competing tips `A` and `B`:

1. **Heaviest work wins**: choose tip with larger `W`.
2. **Work tie-breaker**: if `|W(A) − W(B)| < ε_work`, choose higher **height**.  
   - `ε_work` is a tiny tolerance to absorb floating-point/µ-nats rounding (default: **1e-6 µ-nats**).
3. **Height tie-breaker**: if heights equal, choose **lowest header hash** (lexicographic) for determinism.

This produces a **total order** over tips and is independent of peer topology.

### Incremental maintenance
Nodes keep for each stored header:
- `cum_work[hash] = cum_work[parent] + w_this`
- `height[hash] = height[parent] + 1`

Updates are O(1) per new header (plus validation).

---

## 4) Reorg limits & finality windows

Reorganizations are permitted **only** if the competing tip delivers a **sufficient improvement** in cumulative work relative to the **reorg depth** and **age**.

Let:
- `D` = reorg depth (distance from current best tip down to the fork point).
- `ΔW = W(new_tip) − W(current_tip)` evaluated at the point the node would switch.

### 4.1 Depth-aware threshold

A switch is allowed iff:

ΔW ≥ τ(D)

with a policy function `τ`:

τ(D) = τ0 + k · D

- `τ0` (base slack; default: **0.0 µ-nats**) — often 0 when clocks are sane.
- `k` (per-block penalty; default: **0.25 µ-nats**).  
  Intuition: deeper reorgs require increasingly convincing work advantage.

> On dev/test networks, `k` may be set very low to allow frequent reorgs; on production networks it should be high enough to make long reorgs practically infeasible.

### 4.2 Hard caps

- **Max reorg depth**: `D_max` (default: **64** on testnets; network-configurable).
- **Max reorg age**: if the fork point is older than `T_max_reorg` wall-clock (e.g., **2 hours**), reject unless `ΔW ≥ τ_hard` (a large override threshold, e.g., **8.0 µ-nats**).

### 4.3 Soft finality (confirmation count)

For user guidance and light clients:
- A block with **K confirmations** is considered **soft-final** once the expected `ΔW` needed to overturn exceeds an operator threshold (derived from `k` and observed variance). Typical K: **12–24** on testnets, network-specific on mainnet.

---

## 5) Interaction with difficulty retarget (Θ)

- Retarget (`consensus/difficulty.py`) keeps `E[S_i] ≈ Θ_i + ε` (small drift).  
- Because `w_i` is clamped to `[Θ_i, Θ_i + Δ_cap]`, **average work per block** is stabilized across epochs, making `W` comparable over time despite parameter drift.
- A step change in policy `ψ` caps (e.g., new proofs) changes `S_i` distribution but does not break the monotonic nature of `W`.

---

## 6) Light client fork choice

Light clients maintain:
- A header chain with `w_i` validated via the **light rules** (policy roots, commitment roots, and succinct checks).
- `W(T)` computed identically from `w_i`.
- Reorg policy: same `τ(D)`, `D_max`, and tie-breakers.  
  Availability (DA) should be verified via light proofs as per `docs/spec/MERKLE_NMT.md` and DA specs.

---

## 7) Edge cases & safety

- **Header floods**: DoS controls in P2P limit ingestion rate; fork choice remains O(1) per accepted header.  
- **Equal work races**: deterministic tie-breaker avoids oscillation.  
- **Clock skew**: age-based caps use **monotonic block timestamps** with sanity checks; never rely on local wall-clock without validation.  
- **Policy root changes**: blocks carry policy roots; any change is part of header validity; fork choice only sees `w_i` post-validation.

---

## 8) Pseudocode

```python
# Given a validated header h with parent p:

w_h = clamp(score(h), theta(h), theta(h) + DELTA_CAP)
cum_work[h] = cum_work[p] + w_h
height[h]   = height[p] + 1

def better_tip(a, b):
    dW = cum_work[a] - cum_work[b]
    if abs(dW) > EPS_WORK:
        return dW > 0
    if height[a] != height[b]:
        return height[a] > height[b]
    return hash(a) < hash(b)  # lexicographic

def maybe_switch(best, new_tip):
    # Find fork point and depth
    fp = lca(best, new_tip)
    D  = height[best] - height[fp]
    dW = cum_work[new_tip] - cum_work[best]

    if D > D_MAX: return best
    # Age-based guard (optional if timestamps present)
    if age(fp) > T_MAX_REORG and dW < TAU_HARD: return best
    if dW < tau(D): return best

    # Passed; switch if deterministic order says so
    return new_tip if better_tip(new_tip, best) else best


⸻

9) Parameters (defaults; network-configurable)

Name	Meaning	Default (test)
DELTA_CAP	Max bonus above Θ per block (µ-nats)	4.0
EPS_WORK	Work tie tolerance (µ-nats)	1e-6
D_MAX	Hard maximum reorg depth (blocks)	64
T_MAX_REORG	Hard maximum reorg age (wall-clock)	2h
τ0	Base improvement to reorg (µ-nats)	0.0
k	Per-depth improvement slope (µ-nats/block)	0.25
τ_hard	Override for very old reorgs (µ-nats)	8.0

Mainnet values are stricter and published in spec/params.yaml.

⸻

10) Test obligations
	•	Determinism: identical inputs across nodes ⇒ identical best tip under the rules.
	•	Tie handling: produce expected tip given equal work and height (hash order).
	•	Depth thresholds: reorg permitted for shallow depths with small ΔW, but rejected for deep depths without sufficient ΔW.
	•	Policy changes: when caps/roots change, previously computed w_i remain stable for stored headers.

⸻

11) Rationale & alternatives considered
	•	Pure height: too brittle; ignores useful work variance.
	•	Unbounded S_i: allows jackpot blocks to dominate; clamping prevents pathological selection by a single outlier.
	•	Work-per-time: introduces clock coupling; rejected for simplicity and portability.

