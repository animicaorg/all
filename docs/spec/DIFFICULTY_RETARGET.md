# Difficulty Retarget — Fractional EMA & Windows

This document specifies the **fractional retarget** algorithm that updates the PoIES acceptance threshold `Θ` to keep the **expected inter-block interval** near a configured target. The reference implementation lives in:

- `consensus/difficulty.py` — core update rule & clamps
- `consensus/window.py` — rolling windows, epoch math, observed rate
- Tests: `consensus/tests/test_difficulty_retarget.py`

Related:
- PoIES math: `spec/poies_math.md` (block score `S = H(u) + Σψ`)
- Fork choice: `docs/spec/FORK_CHOICE.md`

---

## 1) Goal & invariants

- Maintain **stable block cadence** with target interval `τ_target` (e.g., 12s).
- Adapt to changes in **available work** (hash power and useful proofs) without oscillations.
- Keep `Θ` in **µ-nats** (same unit as `H(u)` and `ψ`) so updates are additive and composable.
- Deterministic & local: nodes derive the same `Θ` sequence from the chain.

---

## 2) Observations & inputs

At each accepted block `i` (height `h = i`), compute:

- Inter-block time  
  `Δt_i = timestamp_i − timestamp_{i−1}` (monotone-checked; see §7)
- Fractional deviation (log space):  
  `z_i = ln(Δt_i / τ_target)`

  - If blocks are **too fast** (`Δt_i < τ_target`), then `z_i < 0`, so `Θ` should **increase**.
  - If blocks are **too slow**, `z_i > 0`, so `Θ` should **decrease**.

We smooth `z_i` with an EMA to suppress noise:

m_i = (1 − β) · m_{i−1} + β · clip(z_i, −z_cap, +z_cap)

- `β ∈ (0,1]` is the fractional weight for the latest sample (typical 0.1–0.3)
- `z_cap` limits extreme outliers before they hit the EMA (e.g., 0.405 ≈ ln(1.5))

Genesis: `m_0 = 0`.

---

## 3) Update rule (fractional in log space)

We adjust `Θ` **additively in µ-nats** by a gain applied to `m_i`:

ΔΘ_i = k · clip(m_i, −m_cap, +m_cap)
Θ_i  = clamp(Θ_{i−1} + ΔΘ_i, Θ_min, Θ_max)

- `k` is the **retarget gain** (µ-nats per unit log error). Smaller `k` → slower, more stable.
- `m_cap` is a second clamp to bound per-block step after smoothing.
- `Θ_min`, `Θ_max` are safety rails.

This log-space fractional controller is dimensionally consistent and **load-agnostic**: doubling aggregate work pushes `m_i < 0`, nudging `Θ` upward until the average `S` lands back near the acceptance boundary.

> Intuition: in steady state we want `E[S] ≈ Θ + ε` (small positive margin), hence keeping acceptance probability near the target cadence.

---

## 4) Optional epoch windows

Instead of per-block EMA, a network may configure **epochs** of `W` blocks:

1. Accumulate `z̄_epoch = mean( clip(z_j) )` over the last `W` accepted blocks.
2. Apply the same update at epoch boundary:
   `Θ ← clamp(Θ + k_epoch · clip(z̄_epoch, −m_cap, +m_cap))`

Epoch windows reduce jitter at the cost of **slower reaction**. The reference code supports both **EMA-every-block** and **per-epoch** modes.

---

## 5) Interaction with useful proofs (Σψ)

`S = H(u) + Σψ` is additive. If the network starts including more useful proofs so that `E[Σψ]` rises, the controller treats it as **excess effective work**, observes **faster blocks** (`z_i < 0`), and **raises `Θ`** until the cadence returns to target.

No special casing is required; the PoIES policy and caps ensure `Σψ` is bounded per block.

---

## 6) Parameters (defaults; network-configurable)

| Name            | Meaning                                                | Typical |
|-----------------|--------------------------------------------------------|---------|
| `τ_target`      | Target inter-block interval (seconds)                  | 12 s    |
| `β`             | EMA weight for newest observation                      | 0.2     |
| `z_cap`         | Pre-EMA clip on `z_i = ln(Δt/τ_target)`                | 0.405   |
| `k`             | Gain: µ-nats per unit log error                        | 0.75    |
| `m_cap`         | Post-EMA clamp before applying gain                    | 0.35    |
| `Θ_min`         | Hard lower bound (µ-nats)                              | −10.0   |
| `Θ_max`         | Hard upper bound (µ-nats)                              | +40.0   |
| `mode`          | `ema_per_block` or `per_epoch`                         | ema     |
| `W`             | Epoch size (blocks) when `per_epoch`                   | 64–256  |

> Mainnet values are published in `spec/params.yaml` and locked at genesis.

---

## 7) Timestamps and guards

- **Monotonicity**: `timestamp_i ≥ timestamp_{i−1} + t_min_step` (e.g., 1s). Reject otherwise.
- **Bounds**: clip `Δt_i` into `[Δt_min, Δt_max]` before computing `z_i` to limit adversarial skew.
- **Wall-clock trust**: We use block timestamps only after basic sanity; consensus does not rely on local wall clock for retarget.

Recommended: `Δt_min = 0.25·τ_target`, `Δt_max = 4·τ_target`.

---

## 8) Pseudocode

```python
# Inputs each accepted block:
#   prev_state: (Theta_prev, m_prev)
#   ts_prev, ts_now: previous and current block timestamps
#   cfg: {tau_target, beta, z_cap, m_cap, k, Theta_min, Theta_max}

def retarget_step(prev_state, ts_prev, ts_now, cfg):
    dt   = clamp(ts_now - ts_prev, cfg.dt_min, cfg.dt_max)
    z    = ln(dt / cfg.tau_target)
    z    = clamp(z, -cfg.z_cap, cfg.z_cap)

    m    = (1.0 - cfg.beta) * prev_state.m + cfg.beta * z
    m_c  = clamp(m, -cfg.m_cap, cfg.m_cap)

    dTheta = cfg.k * m_c
    Theta  = clamp(prev_state.Theta + dTheta, cfg.Theta_min, cfg.Theta_max)

    return State(Theta=Theta, m=m)

Epoch mode substitutes z̄_epoch for z and applies the update every W blocks.

⸻

9) Stability notes
	•	Working in log space yields a multiplicative response: a 2× speedup and a 0.5× slowdown are symmetric (±ln 2).
	•	Two-stage clamp (z_cap, m_cap) protects against timestamp outliers and over-correction.
	•	Gains (k, β) should satisfy acritically damped behavior in simulation (see tests), avoiding under/over-shoot.

⸻

10) Edge cases & safety
	•	Halts: if no blocks for a long period, the first Δt back is large; capping prevents a giant downward jump in Θ.
	•	Burst mining: sudden work spikes produce z_i < 0; the controller raises Θ over a few blocks, not instantly.
	•	Policy shifts: updates to PoIES caps are reflected as smooth changes in Σψ; retarget remains stable.

⸻

11) Test obligations

consensus/tests/test_difficulty_retarget.py must cover:
	1.	Convergence: from mis-set Θ, the interval converges toward τ_target.
	2.	Step response: ±X% change in effective work leads to bounded transient in Θ.
	3.	Outlier robustness: single extreme Δt (min/max) does not destabilize.
	4.	Epoch vs EMA: both modes pass the same qualitative stability checks.
	5.	Determinism: given the same timestamps and params, all nodes derive identical Θ sequences.

⸻

12) Rationale & alternatives
	•	Median-of-N windows: robust but slower and non-differentiable; EMA chosen for simplicity and responsiveness.
	•	Direct probability control (set Θ so Pr[S ≥ Θ] matches target): appealing but requires modeling Σψ distribution; the EMA implicitly learns it online.
	•	Height-based retarget epochs (e.g., fixed 2016-block windows): supported as per_epoch mode; EMA gives smoother UX.

