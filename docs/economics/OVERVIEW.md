# Economics Overview — Utility Mining & Sustainability (v1)

This document explains the economic design targets of **Animica**: the system rewards *useful work* (AI/Quantum/Storage/VDF alongside hashing) while keeping issuance and operating costs sustainable across market cycles. It ties together parameters from **PoIES** (Proof of Incentivized-External-Services), the **AICF** (AI Compute Fund), **fee markets**, and **treasury** behavior.

**Related specs**
- Consensus: `docs/spec/FORK_CHOICE.md`, `docs/spec/DIFFICULTY_RETARGET.md`
- PoIES math: `docs/spec/poies/*`
- Proofs: `docs/spec/proofs/*`
- AICF: `aicf/specs/*`
- Fees/Mempool: `docs/spec/MEMPOOL.md`
- Receipts/Events: `docs/spec/RECEIPTS_EVENTS.md`

---

## 1) Objectives

1. **Utility Mining.** Reward miners/validators who contribute *verifiable* external services:
   - **AI** inference/training fragments (TEE+traps),
   - **Quantum** trap-circuit jobs,
   - **Storage** availability/heartbeat & retrieval proofs,
   - **VDF** time-delay proofs,
   - **HashShares** to stabilize block timing and openness.
2. **Sustainability.** Keep **net issuance** bounded and predictable, aligning long-run security with:
   - Fee revenue from real usage,
   - AICF budgets with caps (`Γ_fund`),
   - Base-fee *burns* to offset inflation,
   - Parameter schedules that adapt as usage grows.
3. **Fairness & Diversity.** Avoid monocultures of work; maintain competitive access across proof types using **caps**, **escort rules**, and the **α-tuner**.

---

## 2) Roles & Flows

- **Users & Contracts** pay **fees** (base + tip) and optionally **AI/Quantum units** via AICF.
- **Miners/Validators** assemble blocks; attach verified proofs; earn block reward + tips + AICF splits.
- **Providers (AI/Quantum/Storage)** earn payouts for completed jobs/heartbeats.
- **Treasury** receives configured slices and handles epoch settlements; a portion may be *burned*.

Users/Contracts ── fees/tips ─▶ Mempool/Execution
│                                 │
└── AICF job fees ─▶ AICF queue ──┤
▼
Providers ◀─ payouts/splits ── AICF settlement ──▶ Treasury
▲
│
Miners/Validators ◀── block reward + tips + AICF split

---

## 3) Reward Budget & Caps

Let:
- `R_block` = nominal per-block issuance (micro-units)
- `fees_base` = protocol base-fee (burned partially/all)
- `fees_tip`  = user tips (to block producer)
- `AICF_payouts` = sum of verifiable job rewards this block
- `Γ_total` = *total* per-block cap of ψ-creditable useful work (policy-level)
- `Γ_fund`  = per-epoch AICF budget cap (prevents runaway compute spend)
- `θ` = current difficulty threshold (PoIES acceptance target)

**Block producer revenue:**

rev_miner = R_block + fees_tip + split_miner(AICF_payouts) - slashing(if any)

**Protocol burn (deflationary pressure):**

burn = burn_ratio * fees_base + optional_burn * AICF_payouts

By default **all base-fee** is burned, while AICF payouts are **not** burned (they compensate external costs). An optional small burn on AICF payouts can offset growth phases.

**Policy caps**
- **Per-proof-type caps**: limit contribution of a single type to Σψ.
- **Total-Γ cap**: limit sum of ψ credits per block.
- **Escort rules**: require presence of HashShare(s) and/or minimum diversity for eligibility.

See `consensus/policy.py`, `consensus/caps.py` and `spec/poies_policy.yaml`.

---

## 4) Utility Mining via Σψ and Acceptance

Block acceptance uses:

S = H(u) + Σ ψ_i(p_i)  ≥  Θ

- `H(u) = −ln(u)` comes from hash nonce sampling,
- `ψ_i(p_i)` are **scored, capped** contributions from external proofs,
- `Θ` adapts via retarget to hit a target block interval (`λ_target`).

**Design intent**
- Hashing guarantees *liveness & permissionless access*.
- Useful proofs shift expected acceptance so rational miners include diverse, high-quality work.
- Caps ensure no single domain can crowd out others.

---

## 5) AICF Budgets, Pricing & Splits

**Pricing**
- **AI units** (e.g., tokens/sec × QoS) and **Quantum units** (depth×shots) map to a *base schedule* (see `aicf/economics/pricing.py`).
- Schedules convert units → *base reward* per job; then **splits** divide among provider / miner / treasury (see `aicf/economics/split.py`).

**Budget constraints**
- **Per-epoch Γ_fund**: max payouts the epoch can settle. When near the cap:
  - lower-priority jobs are deferred,
  - on-chain Σψ still reflects *verified proofs*, but AICF settlement honors the budget order (FIFO + priority).

**Splits example**

provider_share = 0.80
miner_share    = 0.15
treasury_share = 0.05

Numbers are network-configurable; they balance *hardware/computation cost* (provider), *block production incentives* (miner), and *public goods* (treasury).

---

## 6) Fee Market & Issuance Offsets

- **Base fee** adjusts with block fullness (EMA); it is **burned** (EIP-1559-like).
- **Tips** go to block producers.
- **Issuance schedule**: `R_block` decays over time (e.g., exponential decay per epoch or stepwise “halvings”).
- **Net issuance** target:
  - In high usage, `burn ≥ R_block` → deflationary/neutral,
  - In low usage, `R_block` secures the chain while AICF throttles to `Γ_fund`.

**Tuning levers**
- `R_block` decay rate,
- Base-fee elasticity parameters,
- `Γ_fund` per epoch,
- AICF base pricing & splits,
- Caps (`Γ_total`, per-type).

---

## 7) Fairness, Diversity & α-Tuner

To prevent single-work dominance:
- **Per-type caps** and **escort rules** demand mixtures.
- **α-tuner** (see `consensus/alpha_tuner.py`) nudges relative scoring for underrepresented proof types over rolling windows, bounded to avoid oscillations.
- Objective: keep *Herfindahl-Hirschman Index (HHI)* or *Gini* within policy targets (see `docs/spec/poies/FAIRNESS.md`).

---

## 8) Sustainability Scenarios

### Bootstrapping
- Higher `R_block`, conservative `Γ_fund`, modest provider payouts to seed a healthy set of participants.
- Base-fee burn in place from day one.

### Growth
- Increase `Γ_fund` gradually as real demand for AI/Quantum grows.
- Tighten per-type caps if one resource starts dominating.
- Monitor *payout efficiency* (ψ per paid unit) and adjust pricing schedules.

### Maturity
- `R_block` decayed; fees and AICF drive most revenue.
- Consider small burn on AICF payouts to keep net issuance neutral in high-demand regimes.

---

## 9) Risks & Mitigations

- **Over-subsidized compute** → runaway costs.  
  *Mitigate with `Γ_fund` caps, dynamic pricing floors, priority queues.*
- **Monoculture of work** (e.g., AI only).  
  *Mitigate via per-type caps, α-tuner, escort rules.*
- **Fee insufficiency in downturns.**  
  *Ensure `R_block` doesn’t decay faster than security budget; keep emergency levers.*
- **Provider centralization.**  
  *Stake/attest diversity; regional quotas; transparent SLA metrics and slashing (see `aicf/sla/*`).*
- **Gaming ψ inputs.**  
  *Strong attestations, trap audits, QoS checks; reject unverifiable metrics.*

---

## 10) Example Parameterization (Illustrative)

- Target block interval: **2s**
- Initial issuance: `R_block = 2.0 ANM`, epoch decay: **−12%/year**
- Base fee burn: **100%**
- Tips: to miner
- AICF `Γ_fund`: **1.5%** of supply per year (declining with adoption)
- Splits: **80/15/5** (provider/miner/treasury)
- Caps:
  - `Γ_total`: limits Σψ so a single block can’t over-credit,
  - Per-type cap: **≤ 50%** of Σψ per block,
  - Escort: at least **1 HashShare** + **1 non-hash proof** for max credit tier.

> Actual mainnet values are in `spec/poies_policy.yaml` and `aicf/policy/example.yaml`.

---

## 11) KPIs & Dashboards

- **Security budget (real)**: tips + AICF miner share + issuance (per day)
- **Burn / Issuance ratio**
- **Γ_fund utilization** and backlog
- **Diversity indices** (HHI of proof types)
- **Provider health**: SLA pass rate, slash events
- **Cost-to-ψ** efficiency: paid units vs accepted ψ

---

## 12) Governance & Upgrades

Economic parameters change via the network’s upgrade process (see `docs/spec/UPGRADES.md`). Changes **must**:
- Update policy roots in headers,
- Be pre-announced with simulation results,
- Ship revised `spec/poies_policy.yaml` and `aicf/policy/*.yaml`.

---

## 13) Summary

Animica’s economics reward *useful*, **verifiable** external services while maintaining a predictable, sustainable monetary policy. Caps, burns, and budgets coordinate incentives across miners, providers, and users, keeping security robust and costs aligned with real utility.

*Version: v1.0 (informational; non-normative—see spec files for consensus rules).*
