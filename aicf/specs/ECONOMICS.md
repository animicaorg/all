# AICF Economics
_pricing → reward splits → epoch accounting → caps & settlement_

This document specifies how the **AI/Quantum Compute Fund (AICF)** converts verifiable useful work into token rewards, how those rewards are split among stakeholders, and how per-epoch accounting and caps (Γ_fund) constrain total issuance.

**Status:** Normative for the AICF subsystem. Code is authoritative when there is ambiguity (see `aicf/economics/*` and tests).

---

## 1) Scope & Definitions

- **JobKind**: `{AI, Quantum}` (see `aicf/types/job.py`).
- **Units**: A normalized measure of work:
  - `ai_units`: platform-normalized compute units (e.g., tokens·sec at QoS ≥ threshold, post-TEE attestation).
  - `quantum_units`: normalized depth×width×shots with trap-circuit coverage.
- **Base Price**: Per-unit baseline rate per kind, from policy.
- **Quality Multiplier (q_mult)**: Rewards scale with measured quality/SLA bands (bounded by policy).
- **Fairness Multiplier (f_mult)**: Optional slow-moving nudger to avoid long-term crowd-out across kinds (see α-tuner in consensus; bounded).
- **Γ_fund (epoch cap)**: Maximum AICF outflow per epoch (sum across kinds).
- **Epoch**: Fixed window defined by policy (blocks or wall-time) for accounting and settlement.
- **Split**: Reward partition between `{provider, miner, treasury}`.

---

## 2) Pricing Model

### 2.1 Inputs

For a completed job with validated proof (post attestation + normalization):

- `kind ∈ {AI, Quantum}`
- `units ≥ 0` — integer or fixed-point (policy decides decimals)
- `q_mult ∈ [q_min, q_max]` — derived from SLA metrics band (see §5)
- `f_mult ∈ [f_min, f_max]` — system-wide fairness nudger (optional)
- `base_price[kind]` — from policy
- `max_reward[kind]` — per-job ceiling (anti-whale)
- `rounding_mode` — deterministic banker’s or floor per policy

### 2.2 Formula

raw_reward = units * base_price[kind]
adj_reward = raw_reward * q_mult * f_mult

reward = clamp(adj_reward, 0, max_reward[kind])
reward = round_(reward, rounding_mode)

Policy MAY add a **surge factor** tied to backlog or utilization; if enabled:

surge_mult ∈ [1.0, surge_max]
reward = clamp(reward * surge_mult, 0, max_reward[kind])

**Determinism:** All multipliers are derived from on-chain or header-committed policy roots and discrete bins, never floating environment state.

### 2.3 Configuration Surface

- `aicf/policy/example.yaml`
  - `pricing.base_price.ai`
  - `pricing.base_price.quantum`
  - `pricing.max_reward_per_job.*`
  - `pricing.rounding: {bankers|floor}`
  - `pricing.surge: {enabled, bins, surge_max}`
- Implementation: `aicf/economics/pricing.py`
- Tests/Vectors: `aicf/tests/test_pricing_split.py`, `aicf/test_vectors/settlement.json`

---

## 3) Reward Split

Once the **per-job reward** is computed, split it:

provider_share = reward * split[kind].provider
miner_share    = reward * split[kind].miner
treasury_share = reward * split[kind].treasury

Constraints:
- `split[kind].provider + miner + treasury = 1.0` (exact by fixed-point policy).
- Shares are represented as fixed-point integers to avoid FP drift.
- Remainder after integer division (if any) is assigned by policy (`to_treasury` or `round_robin` deterministic).

Config:
- Policy: `splits.{ai,quantum}.{provider,miner,treasury}`
- Code: `aicf/economics/split.py`
- Rationale: `capabilities/specs/TREASURY.md`

---

## 4) Epoch Accounting & Caps (Γ_fund)

### 4.1 Epoch Window

- Defined by `policy.epoch.kind`:
  - `blocks: N` or
  - `time: {seconds: T}` (if wall-time, settlement aligns to nearest block ≥ T since epoch start).
- Epoch ID `E`: monotonically increasing integer, committed in settlement records.

### 4.2 Cap Enforcement

Let `R_E` be total AICF outflow in epoch `E` (sum across settled jobs).
- **Hard cap:** `R_E ≤ Γ_fund[E]`.

Enforcement occurs at **claim → settlement**:
- Jobs completing within `E` accrue **provisional** rewards.
- During settlement, if `Σ provisional > Γ_fund[E]`, apply **pro-rata haircut**:

haircut = Γ_fund[E] / Σ provisional
payout_job = provisional_job * haircut

- Haircut computed in fixed-point; any rounding remainder goes to `treasury`.

Policy options:
- **Carryover:** `carryover ∈ {none, partial, full}` moves unused capacity `Γ_fund[E] - R_E` to `E+1` (bounded by `max_carry`).
- **Per-kind sub-caps:** Optional `Γ_fund_ai`, `Γ_fund_quantum` before global.

### 4.3 Minting / Funding Source

Two supported modes:
1. **Budgeted Mint:** Treasury mints `Γ_fund[E]` at epoch open (or on settle) → unspent is burned or carried per policy.
2. **Fee-backed:** Funded from base fees/allocations; if insufficient, haircuts bring payouts into budget.

Config:
- Policy: `fund.gamma_per_epoch`, `fund.carryover`, `fund.subcaps`, `fund.mode`
- Code: `aicf/economics/epochs.py`, `aicf/treasury/mint.py`
- Tests: `aicf/tests/test_epoch_rollover.py`, `aicf/tests/test_payouts_settlement.py`

---

## 5) SLA Interaction (q_mult)

SLA metrics (traps coverage, QoS, latency, availability) are bucketed:

- Each job receives a **band** at proof intake:
  - Example bands: `{gold: 1.10, silver: 1.00, bronze: 0.85, fail: 0.0}`
- Failing SLA yields `q_mult = 0` and may trigger slashing (outside this doc).

Inputs:
- `aicf/sla/metrics.py` (measurement)
- `aicf/sla/evaluator.py` (banding & confidence windows)
- `aicf/economics/slashing_rules.py` (penalty magnitudes)

Determinism:
- Band thresholds & lookback windows come from policy; no floating averages beyond committed windows.

---

## 6) Settlement Pipeline

1. **Proof Intake → Pricing:** Normalize proof to `units`, compute `reward` with multipliers.
2. **Split:** Compute `{provider, miner, treasury}` shares.
3. **Accrual:** Append provisional entries into epoch ledger.
4. **Cap Check:** On settlement, compute haircut if `Σ > Γ_fund[E]`.
5. **Apply:** Credit balances in `aicf/treasury/state.py`.
6. **Emit Records:** Settlement batch with epoch ID, totals, and per-claim outputs.
7. **Finalize:** Mark claims settled; any unclaimed residue handled per policy.

Code:
- `aicf/economics/payouts.py`
- `aicf/economics/settlement.py`
- Adapters: `aicf/integration/execution_hooks.py`, `aicf/adapters/block_db.py`

Events & RPC:
- WS: `epochSettled` (see `aicf/rpc/ws.py`)
- RPC: report balances and claim status (see `aicf/rpc/methods.py`)

---

## 7) Numerical Example (deterministic)

Policy (excerpt):

base_price:
ai:       0.80   # token per ai_unit
quantum:  3.00   # token per quantum_unit
split:
ai:       {provider: 0.82, miner: 0.10, treasury: 0.08}
quantum:  {provider: 0.85, miner: 0.08, treasury: 0.07}
max_reward_per_job:
ai:       5_000
quantum:  12_000
epoch:
blocks:   600
fund:
gamma_per_epoch: 250_000
carryover: partial

Job:
- kind = AI
- units = 4_000
- q_mult = 1.00 (silver band)
- f_mult = 1.00
- surge disabled

Computation:

raw_reward = 4_000 * 0.80 = 3_200
adj_reward = 3_200 * 1.00 * 1.00 = 3_200
reward     = clamp(3_200, 0, 5_000) = 3_200

provider = 3_200 * 0.82 = 2,624.00
miner    = 3,200 * 0.10 =   320.00
treasury = 3,200 * 0.08 =   256.00

At epoch settlement:
- Suppose Σ provisional = 300,000 > Γ_fund = 250,000
- `haircut = 250,000 / 300,000 = 0.833333…`
- Final credits:
  - provider = 2,624.00 * 0.833333… = 2,186.67
  - miner    =   320.00 * 0.833333… =   266.67
  - treasury =   256.00 * 0.833333… =   213.33
- Remainders distributed to `treasury` (deterministic rule).

---

## 8) Edge Cases & Rules

- **Zero/Low Units:** If `units = 0` → reward = 0 (no split).
- **Partial Proof:** If normalization marks partial success with `q_mult < 1`, apply banding; if below minimum QoS, treat as fail.
- **Duplicate Claims:** Deduplicate by `(task_id, nullifier)`; idempotent settlement.
- **Reorgs:** Claims are tied to block heights; on reorg, rollback provisional entries and re-apply per canonical chain (see adapters).
- **Slashed Providers:** Settlement can net-out slashing penalties first, then credit remainder (cannot go negative beyond lock rules).
- **Sub-caps:** If enabled, apply per-kind haircut first, then global Γ_fund haircut on the post sub-cap sums.
- **Precision:** All economics done in fixed-point with policy-defined `scale` (e.g., 1e6). Rounding is deterministic chain-wide.

---

## 9) Configuration & Versioning

- All knobs live in `aicf/policy/*.yaml`; the active policy root is committed in chain headers.
- Backward-incompatible changes:
  - Require a new activation epoch and version bump in `aicf/version.py`.
  - Tests and vectors updated alongside (`aicf/test_vectors/*`).

---

## 10) References (Code & Tests)

- Pricing: `aicf/economics/pricing.py`
- Splits: `aicf/economics/split.py`
- Epochs & caps: `aicf/economics/epochs.py`
- Payouts & settlement: `aicf/economics/payouts.py`, `aicf/economics/settlement.py`
- Treasury: `aicf/treasury/{state,mint,rewards,withdraw}.py`
- Proofs → claims: `aicf/integration/proofs_bridge.py`
- Tests:
  - `aicf/tests/test_pricing_split.py`
  - `aicf/tests/test_payouts_settlement.py`
  - `aicf/tests/test_epoch_rollover.py`
  - Vectors: `aicf/test_vectors/settlement.json`

---

## 11) Invariants (must hold)

1. `∀ kind: provider+miner+treasury = 1.0 (exact in fixed-point)`
2. `0 ≤ R_E ≤ Γ_fund[E]`
3. Deterministic rounding and remainder assignment produce identical results across nodes.
4. No settlement may create or destroy value except per policy: mint, burn, or carryover.

> If an invariant is violated in code paths, settlement MUST abort and raise an error that halts the batch until corrected (defensive default).
