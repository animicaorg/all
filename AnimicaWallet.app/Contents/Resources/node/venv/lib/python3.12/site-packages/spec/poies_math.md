# Proof-of-Integrated Evidence of Score (PoIES) — Math Notes

> This document is the human-readable companion to the formal spec and test
> vectors. It defines the acceptance predicate, tuning rules, and fairness
> mechanics that drive Animica’s consensus. See `spec/formal/poies_equations.lean`
> for machine-checked lemmas and `consensus/tests/*` for executable invariants.

---

## 1) High-level intuition

Blocks are accepted when **random hash luck** plus **useful evidence** clears a moving
threshold. Randomness ensures liveness and a lottery feel; evidence makes work
useful and multi-device. Concretely:

- Draw a uniform `u ∈ (0,1]` from the header’s nonce domain.
- Convert to exponential work via `H(u) = −ln(u)`.
- Aggregate verified evidence (AI/Quantum/Storage/VDF/Hash shares) into scores
  `ψ(p) ≥ 0` using policy from `spec/poies_policy.yaml`.
- Accept if the **PoIES score** clears the **difficulty threshold** `Θ`:

\[
\boxed{ \; S = H(u) \;+\; \sum_{p \in \mathcal{P}} \psi(p) \;\;\ge\;\; \Theta \; }
\]

Here `𝒫` is the multiset of proofs carried by the candidate header. A proof’s
contribution `ψ(p)` is clipped by per-proof, per-type, and total caps.

---

## 2) Notation & domains

- `u`: base nonce luck (uniform via Keccak/SHA-3 personalization domain).
- `H(u) = −ln(u)`: exponential(1) work; infinite near 0, 0 at 1.
- `ψ(p)`: non-negative score from a single proof `p` **after** caps.
- `Θ`: current acceptance threshold (aka “difficulty”).
- `Γ_total`: max integrated evidence per block (global cap).
- `Γ_type[t]`: per-type cap (e.g., AI/Quantum/Storage/Hash/VDF).
- `α_type[t]`: fairness gain factors per type (slow-moving tuner).
- `q_escort`: optional “escort” rule: require ≥ *q* distinct types if evidence
  exceeds a policy fraction (prevents monoculture gaming).

Domains & tags are defined in `spec/domains.yaml`.

---

## 3) Evidence mapping ψ(p)

Each proof is verified into **metrics** (see `proofs/metrics.py`) and then mapped
to pre-cap score units using type-specific functions parameterized by policy.

**HashShare (HSH):**
- Inputs: difficulty ratio `d_ratio = target / share_target ≥ 1`.
- Baseline (before caps/α):  
  \[
  \psi_{\text{hash}}^{raw}(p) = \beta_{\text{hash}} \cdot \ln(d\_ratio)
  \]
  with `β_hash` from policy. This makes doubling the ratio additively beneficial.

**AI (TEE+redundancy+traps):**
- Inputs: `ai_units` (normalized compute), `qos∈[0,1]`, `traps_ratio∈[0,1]`,
  `redundancy≥1`.
- Baseline:  
  \[
  \psi_{\text{ai}}^{raw}(p)=\beta_{\text{ai}}\cdot ai\_units\cdot qos \cdot g(traps\_ratio)\cdot r(redundancy)
  \]
  where `g(x)` is convex below target and flat above (discourages over-trapping),
  `r(k)=\min(k, r_{max})^{\rho}` with `ρ∈(0,1]`.

**Quantum (attest+traps):**
- Inputs: `quantum_units` (depth×width×shots scaled), `qos`, `traps_ratio`.
- Baseline:  
  \[
  \psi_{\text{qpu}}^{raw}(p)=\beta_{\text{qpu}}\cdot quantum\_units\cdot qos \cdot g(traps\_ratio)
  \]

**Storage (heartbeat PoSt + optional retrieval bonus):**
- Inputs: `sealed_bytes`, `uptime_qos`, `retrieval_bonus∈\{0,1\}`.
- Baseline:  
  \[
  \psi_{\text{stor}}^{raw}(p)=\beta_{\text{stor}}\cdot sealed\_bytes^{\sigma}\cdot uptime\_qos\cdot (1+\delta\cdot retrieval\_bonus)
  \]
  with sublinear `σ∈(0,1)` to avoid whales.

**VDF (bonus, anti-bias):**
- Inputs: `t_seconds` verified.
- Baseline:  
  \[
  \psi_{\text{vdf}}^{raw}(p)=\beta_{\text{vdf}}\cdot t\_{seconds}
  \]

**Fairness α multipliers:**  
Per-type factors adjust incentives toward policy mix targets:
\[
\psi^{adj}(p) = \alpha_{\text{type}(p)} \cdot \psi^{raw}(p)
\]

**Caps:**  
Apply three clamps in order:
1) Per-proof: `ψ(p) ← min(ψ^{adj}(p), Γ_{proof\_cap}(type))`  
2) Per-type running sum: `Σ_type ≤ Γ_type[type]`  
3) Global running sum: `Σ_all ≤ Γ_total`

Escort/diversity rule: if `Σ_all > τ·Γ_total`, require ≥ `q_escort` distinct types.

The **block evidence sum** is:
\[
\Psi = \sum_{p\in \mathcal{P}} \psi(p) \quad \text{(after caps & escort)}
\]

---

## 4) Acceptance, shares, and receipts

- **Block acceptance:** `S = H(u) + Ψ ≥ Θ`.
- **Micro-target shares:** miners may submit proofs meeting a **share threshold**
  `Θ_share = Θ · m` with `m∈(0,1)` to receive **share receipts** (for rewards,
  pool accounting, dashboards). These do **not** extend the chain.

Receipts are Merkle-aggregated into the `receiptsRoot`; breakdowns are emitted
for transparency (`consensus/share_receipts.py`).

---

## 5) Difficulty retarget (fractional EMA)

We aim for mean inter-block interval `T_target`. Let `Δ_t` be the observed time
between accepted blocks; maintain an EMA of the **rate**
\[
\lambda_t = \text{EMA}\Big(\frac{1}{\Delta_t}\Big)
\]
and update `Θ` additively in log-space with clamps:

\[
\Theta_{t+1} \;=\; \text{clamp}\!\left(
\Theta_t \;+\; \kappa \cdot \big[\ln(\lambda_t) - \ln(\lambda_{\text{target}})\big],
\;\Theta_t - \Delta^{-},\;\Theta_t + \Delta^{+}\right)
\]

- `κ` is the responsiveness (small, e.g. 0.05–0.15).  
- `Δ⁺, Δ⁻` bound step size to avoid oscillations.  
- This works even as `Ψ` varies, because `H(u)` remains exponential and additive.

Windowing (`consensus/window.py`) defines EMA decay and epoch boundaries.

---

## 6) Fairness tuner α (slow, bounded)

Let `π_type` be the observed fraction of evidence coming from a type over a
window (weighted by accepted `ψ`). Target fractions `π*_type` live in policy.
Each epoch:

\[
\alpha_{k}^{t+1}
= \text{clamp}\!\left(\alpha_k^t \cdot \exp\big(\rho \cdot (\pi^*_k - \pi_k)\big),\; \underline{\alpha},\; \overline{\alpha}\right)
\]

- `ρ` is tiny (e.g., 0.01); bounds keep incentives predictable.
- Multiplicative update preserves dimension and avoids sign errors.
- This gradually tilts rewards toward under-represented work **without** making
  any single type mandatory (except when escort triggers near saturation).

---

## 7) Fork choice (weight-aware)

Define block weight as its **accepted score**:
\[
w(B) = S(B) = H(u_B) + \Psi_B \quad \text{(when } S(B)\ge \Theta_B \text{)}
\]

The preferred chain is the one with **maximum cumulative weight**; on ties,
break deterministically by header hash then height (`consensus/fork_choice.py`).
Bounded reorg depth and honest majority assumptions follow standard analyses for
additive-weight longest-chain protocols.

---

## 8) Security notes (nullifiers, reuse, coupling)

- Each proof has a **nullifier** (domain-separated hash of identity+payload)
  preventing replay across headers (`proofs/nullifiers.py` + `consensus/nullifiers.py`).
- Cross-proof collusion is limited by escort diversity and per-type caps.
- Hash luck cannot be pre-fabricated: the `u` draw domain binds the header
  template (including `policyRoot`, `daRoot`, `receiptsRoot`, `mixSeed`).
- VDF and randomness mixing (see `randomness/`) reduce last-minute bias.

---

## 9) Units & dimensions

- `H(u)` is dimensionless (nats). All `ψ` are calibrated into the **same unit**,
  “µ-nats” internally, so addition is meaningful. Policy `β_*` parameters convert
  domain metrics (seconds, bytes, units) into µ-nats before caps.
- All caps `Γ_*` are expressed in the same unit.

---

## 10) Worked micro-example

- Policy (toy): `Θ=20`, `Γ_total=8`, per-type caps: AI=5, QPU=5, Stor=3, Hash=4.
- Candidate proofs (after verify, before caps):
  - AI: `ψ_ai^{raw}=4.2`, QPU: `ψ_qpu^{raw}=3.0`, Stor: `ψ_stor^{raw}=2.2`, HashShare: `ψ_hash^{raw}=1.5`.
  - Caps apply → sums to `Ψ = min(4.2,5)+min(3.0,5)+min(2.2,3)+min(1.5,4)=10.9` then clipped by
    `Γ_total=8` ⇒ `Ψ=8.0`.
- Nonce draw `u=0.002` ⇒ `H(u)=−ln(0.002)≈6.2146`.
- Score `S=6.21+8.00=14.21 < Θ=20` → **reject**.  
  Another try `u=1e-6` ⇒ `H≈13.8155` → `S≈21.82 ≥ 20` → **accept**.

Lottery feel remains (luck bursts win), but useful work consistently lowers how
much luck is needed—within the policy’s caps.

---

## 11) Determinism & canonicalization

- All ψ mappings use **pure, fixed-point math** with saturating clamps
  (`consensus/math.py`).  
- Policies are loaded from `spec/poies_policy.yaml`, referenced by hash in
  headers. Changing policy requires governance & hard/soft-fork rules in
  `governance/`.  
- Proof parsing/verification is canonical (CDDL/JSON-Schema + deterministic CBOR).

---

## 12) Where to look in the codebase

- Scoring & acceptance: `consensus/scorer.py`, `consensus/validator.py`  
- Caps & totals: `consensus/caps.py`  
- Retarget: `consensus/difficulty.py`, `consensus/window.py`  
- Fairness tuner: `consensus/alpha_tuner.py`  
- Policy loader: `consensus/policy.py`  
- Formal: `spec/formal/poies_equations.lean` (acceptance monotonicity, cap non-negativity)

---

## 13) Invariants (proved / tested)

1. **Non-negativity:** `ψ(p) ≥ 0`; caps never increase a value.  
2. **Monotonicity:** Adding a valid proof cannot decrease `S`.  
3. **Boundedness:** For fixed policy, `Ψ ≤ Γ_total`.  
4. **Determinism:** Given the same header template, proofs, and policy root,
   all honest nodes compute identical `S` and accept/reject decisions.  
5. **Stability:** EMA-retarget avoids unbounded oscillations under bounded
   variance of inter-arrival times (see tests & Lean sketch).

---

## 14) Parameter guidance (defaults live in `spec/poies_policy.yaml`)

- `Γ_total`: start small (e.g., 8–12 µ-nats) to keep hash luck relevant.  
- `β_*`: set via benchmarks so realistic AI/QPU/Stor work contributes 1–5 µ-nats per proof.  
- `q_escort`: 2–3 encourages diversity when near saturation.  
- `κ, Δ⁺, Δ⁻`: conservative (e.g., `κ=0.08`, `Δ±=ln(2)` per epoch).  
- `α` bounds: `[0.5, 2.0]` with `ρ=0.01` keeps incentives stable.

---

## 15) Appendix: acceptance pseudocode

```text
S = H(u(header))
Ψ = 0
budget_type = {AI: Γ_AI, QPU: Γ_QPU, STOR: Γ_STOR, HASH: Γ_HASH, VDF: Γ_VDF}
budget_total = Γ_total
types_seen = ∅

for p in proofs_sorted:           # deterministic order
    ψ_raw = map_metrics_to_psi_raw(p)
    ψ_adj = α[type(p)] * ψ_raw
    ψ_cap = min(ψ_adj, Γ_proof_cap[type(p)])
    ψ_take = min(ψ_cap, budget_type[type(p)], budget_total)
    if ψ_take > 0:
        Ψ += ψ_take
        budget_type[type(p)] -= ψ_take
        budget_total -= ψ_take
        types_seen.add(type(p))

if Ψ > τ·Γ_total and |types_seen| < q_escort:
    # undo excess or fail validation depending on policy
    enforce_escort_rule()

accept = (S + Ψ) ≥ Θ

