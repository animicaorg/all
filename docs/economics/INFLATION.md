# Inflation & Issuance — Schedule, Halvings, and Supply Caps (v1)

This document defines how **ANM** issuance evolves over time: the per-block mint `I_n`, optional **halving** cadence, **exponential decay** alternatives, **tail emission** floors, and an optional **terminal supply cap**. It also covers upgrade rules and implementation details.

> The reward *splitting* across Leader/Committee/Rain/AICF is specified in **docs/economics/REWARDS.md**. This page focuses only on **how many ANM enter circulation** per block/epoch, not who receives them.

---

## 1) Goals & Design Constraints

- **Predictability:** A simple, parameterized schedule that applications can forecast.
- **Security Budget:** Enough issuance in early eras to bootstrap and fund useful work.
- **Sustainability:** Glideslope to low/near-zero inflation; optional **tail emission** to keep incentives non-zero.
- **Governability:** Upgrades are discrete, announced, and checkpointed; never silently change past issuance.
- **Caps:** Optional **terminal cap** if chosen by policy; otherwise monotone convergence to an asymptote.

---

## 2) Terms & Symbols

- `n` — block height (genesis = 0).
- `B` — average blocks per year (chain parameter).
- `I_n` — issuance minted **at block n** (ANM).
- `S_n` — **cumulative supply** after block `n` (ANM).
- `E_k` — epoch index (if schedule changes by epochs).
- `H` — **halving interval** in blocks.
- `λ` — **per-block exponential decay factor**, `0 < λ < 1`.
- `I_0` — initial per-block issuance at genesis.
- `I_min` — **tail emission floor** (ANM/block).
- `S_max` — **terminal cap** (ANM). When enabled, supply is clamped `S_n ≤ S_max`.

---

## 3) Schedules

### 3.1 Geometric Halvings (Bitcoin-style)

- Parameters: `I_0`, `H` (blocks per halving), `min_era = 0`, optional `I_min`.
- Per block:

era(n)  = floor(n / H)
I_n     = max(I_0 / 2^era(n), I_min)

- Cumulative (without tail):

S_n = Σ_{k=0}^{era(n)-1} (H * I_0 / 2^k)
+ (n mod H + 1) * (I_0 / 2^{era(n)})

This geometric series converges to `2 * H * I_0` as `n → ∞` (if `I_min = 0`).

- With **tail emission**, once `I_n` would drop below `I_min`, we instead fix `I_n = I_min` forever (or until a policy upgrade).

**Pros:** very simple mental model.  
**Cons:** step changes → visible reward cliffs; tuning requires careful `H`.

---

### 3.2 Exponential Decay (Smooth)

- Parameters: `I_0`, `λ` (per-block decay), optional `I_min`.
- Per block:

I_n = max(I_0 * λ^n, I_min)

- Cumulative (continuous approximation; exact sum of geometric):

S_n = Σ_{i=0}^{n} I_0 * λ^i = I_0 * (1 - λ^{n+1}) / (1 - λ)

As `n → ∞`, `S_∞ = I_0 / (1 - λ)` (if `I_min = 0`).

**Pros:** smooth glide path; no cliffs.  
**Cons:** λ must be expressed per block (or per epoch) which is less intuitive than halving years.

> **Tip:** Choose λ from a target **half-life** `T_half` via `λ = 2^(−1/T_half)` (measured in blocks).

---

### 3.3 Piecewise Epoch Schedule (Table)

- Parameters: table of `(start_height, I_per_block)` rows; optional `I_min`.
- Per block: choose the last row whose `start_height ≤ n`.  
- Useful for **testnets** and explicit governance schedules.

---

### 3.4 Terminal Cap

If `S_max` is set, we **clamp** additional issuance whenever the next mint would exceed the cap:

if S_{n-1} + I_n > S_max:
I_n = max(0, S_max - S_{n-1})

- With **tail emission** and a cap simultaneously, ensure `I_min` is consistent:
  - Either set `I_min = 0` once cap is reached, or allow `I_min > 0` **only** if `S_max` is not configured.

---

## 4) Canonical Parameters (spec/params.yaml)

Parameters live in `spec/params.yaml`:

```yaml
# Issuance model: "halving" | "exp_decay" | "piecewise"
issuance:
  model: "halving"

  # Common
  i0_per_block: 6.0e0         # ANM per block at genesis
  i_min: 0.0e0                 # tail emission floor; e.g., 0.05e0 for tail

  # Halving only
  halving:
    blocks_per_halving: 2_102_400  # ~4 years @ 15s

  # Exponential decay only
  exp_decay:
    lambda_per_block: 0.99999985   # choose from desired half-life

  # Piecewise only
  piecewise:
    - start_height: 0
      i_per_block: 6.0
    - start_height: 5_000_000
      i_per_block: 3.0

  # Optional terminal cap
  cap:
    enabled: false
    s_max_total: 2_100_000_000.0   # ANM

Validation & hashing
	•	The effective issuance function is hashed into the chain parameters root (spec/params.yaml → core/types/params.py) to make forks measurable.

⸻

5) Implementation Hooks
	•	Computation of I_n: execution/runtime/fees.py (mint at coinbase credit step).
	•	Parameter access: core/types/params.py (parsed from spec/params.yaml).
	•	Upgrade activation: consensus/difficulty.py / consensus/validator.py read effective params based on height/epoch.
	•	Proof-of-compute & fees: Issuance is orthogonal to fees and AICF settlements (see REWARDS); burns in FEES.md reduce net inflation.

⸻

6) Upgrades & Governance
	•	Activation granularity: at epoch boundaries (avoid mid-epoch splits). An upgrade record includes:
	•	new issuance.model and parameters,
	•	activate_at_height (or activate_at_epoch),
	•	a hash of the params object for auditability.
	•	No retroactive changes: past blocks keep their original I_n.
	•	Reproducibility: all schedules produce identical I_n independently (deterministic math; bounded floating use or fixed-point µ-ANM).

⸻

7) Fixed-Point Arithmetic & Rounding

To avoid drift:
	•	Represent ANM in µ-ANM (1e-6) integers on-chain.
	•	For exponential decay, precompute λ^k in fixed-point or use rational approximations per epoch.
	•	Rounding: bankers’ rounding to nearest µ-ANM. Any residual from a cap clamp is credited in the block that hits the cap.

⸻

8) Examples (Illustrative)

These numbers are illustrative only; consult spec/params.yaml for actual networks.

8.1 Halving every ~4 years
	•	I_0 = 6.0 ANM, H ≈ 2.1M blocks, I_min = 0.
	•	Era rewards: 6 → 3 → 1.5 → …
	•	Long-run supply converges; practical terminal supply around 2x era-0 area.

8.2 Exponential Decay, 10-year half-life
	•	I_0 = 6.0, T_half = 10 years, λ = 2^(−1/(10*B)).
	•	Smooth annualized inflation declines continuously.

8.3 Tail Emission (0.05 ANM/block)
	•	After schedule drops below 0.05, freeze I_n = 0.05.
	•	Ensures non-zero miner incentives (security budget) even at far future heights.
	•	No terminal cap if tail > 0 (unless tail is financed via fees/treasury instead of mint).

⸻

9) Metrics & Monitoring

Expose via RPC or Prometheus:
	•	economics_current_issuance_per_block (ANM),
	•	economics_annualized_inflation (derived vs circulating),
	•	economics_supply_circulating,
	•	economics_schedule_hash (for fork detection).

Explorers should render:
	•	Schedule curve (I_n vs n),
	•	Cumulative supply (S_n),
	•	Upgrade markers.

⸻

10) Invariants & Tests
	•	Monotone non-decreasing S_n.
	•	If cap.enabled, eventually I_n → 0 and S_n → S_max.
	•	With tail emission and no cap, I_n ≥ I_min and S_n diverges linearly after the tail era.
	•	Test vectors:
	•	execution/tests/test_issuance_schedule.py (unit): matches closed form.
	•	sdk/common/test_vectors/headers.json: header economic fields match chain params.
	•	Golden: schedule hash stable across implementations.

⸻

11) Choosing a Policy
	•	Bootstrap mainnet: Halving or slow exponential decay with no tail initially; revisit at Year 2+ with data.
	•	Testnet/devnet: Piecewise schedule; higher I_0 to encourage activity; may enable small tail.
	•	Security posture: If fee burns become dominant, consider tail emission or an AICF-funded leader stipend (reduces mint).

⸻

12) Summary
	•	The issuance function I_n is derived from a clear, parameterized schedule (halving / exponential / piecewise), with optional tail emission and an optional terminal cap.
	•	Upgrades are epoch-gated, reproducible, and never retroactive.
	•	Burns and AICF flows affect net inflation, not gross issuance.

Version: v1.0 — Parameter names and locations are stable; future revisions may add polynomial or adaptive schedules if justified by data.

