# PoIES — Intuition & Goals (Multi-Work Incentives)

**PoIES** (Proof-of-Interleaved Evidence Scoring) lets multiple kinds of **verifiable work** jointly secure the chain. It keeps the **unpredictable leader selection** of hash-based mining, while rewarding **useful evidence** (AI, quantum, storage, VDF) that can be checked quickly on-chain.

> **Acceptance predicate (core idea)**  
> We compute a scalar score  
> **S = H(u) + Σ ψ(pᵢ)**  
> and accept the candidate block iff **S ≥ Θ**, where:
>
> - **H(u) = −ln(u)** is the *hash share* from a uniform nonce draw `u ∈ (0,1]`.  
> - **ψ(pᵢ) ≥ 0** are evidence scores for attached proofs (**pᵢ**) from allowed work types.  
> - **Θ** is the moving threshold (retargeted to keep intervals stable).

This preserves **lottery-style unpredictability** (via `H(u)`) while allowing miners to **tilt the odds** by producing **verifiable, scarce proofs** whose value is encoded as **ψ**.

---

## Why PoIES?

Classic designs face a trade-off:

- **Pure PoW**: clean randomness, simple verification, but *wastes energy* on intrinsically useless hashes.
- **Pure useful-work**: risks *centralization*, *oracle complexity*, and *gaming* if the “usefulness” isn’t verifiable on-chain with identical rules for everyone.

**PoIES combines both**:

1. **Hash lottery** ensures anti-grinding unpredictability and simple safety fallbacks.
2. **Evidence scores** pay real work that has public, fast verifiers with **deterministic** inputs/outputs.
3. **Caps & fairness** keep any single work type from dominating security.

---

## Goals

- **Security First**: Maintain Nakamoto-style unpredictability and simple safety arguments even with zero useful proofs.
- **Usefulness with Verifiability**: Only admit work that has *deterministic verifiers* and bounded verification cost (gas/runtime).
- **Open Market of Work**: Let diverse providers/algorithms compete (AI/Quantum/Storage/VDF), paid by the same block reward flow.
- **Anti-Centralization**: Prevent dominance by one hardware/region/work-type via **per-type caps**, **total Γ cap**, and **escort/diversity rules**.
- **Predictable Economics**: Parameterize ψ-weights so marginal revenue ≈ marginal social value; keep intervals stable via Θ retargeting.

---

## Anatomy of a PoIES Block

A candidate block may carry **zero or more** proof envelopes:

- **HashShare**: nonce draw binding to header (gives **H(u)**).
- **AI**: TEE-attested job + trap receipts + QoS; verified to **metrics → ψ**.
- **Quantum**: provider cert + trap-circuit outcomes; verified to **metrics → ψ**.
- **Storage**: PoSt heartbeat (optionally retrieval tickets); **metrics → ψ**.
- **VDF**: Wesolowski proof with seconds-equivalent; **metrics → ψ**.

Each proof:
- has a **schema** (CBOR/JSON-Schema or CDDL),
- maps to **ProofMetrics** (see `proofs/metrics.py`),
- then to a **ψ input** (see `proofs/policy_adapter.py`),
- and is **nullified** (non-reusable) via a domain-separated hash (see `proofs/nullifiers.py`).

The consensus layer clips/weights these via **policy** and applies acceptance:

> Accept if **H(u) + Σ ψᵉᶠᶠ ≥ Θ** under caps and escort/diversity rules.

---

## Caps, Γ, and Diversity / Escort Rules

To balance security across heterogeneous work, the policy enforces:

- **Per-proof caps**: each proof’s ψ is clipped to guard against outliers.
- **Per-type caps**: sum of ψ for a work type cannot exceed a type-specific max.
- **Total Γ cap**: Σ ψ across all proofs is bounded by Γ, ensuring the hash lottery still matters.
- **Diversity / Escort**: require a minimum presence or ratio of certain work kinds to unlock higher caps (e.g., “AI ψ beyond level X requires some Storage ψ”). This nudges **portfolio construction** instead of monoculture.

Parameters live in `spec/poies_policy.yaml` and are loaded by `consensus/policy.py`, enforced by `consensus/caps.py`.

---

## Retargeting Θ

We target a stable block interval by adjusting **Θ** with an EMA and clamps:

- Observe inter-block times / acceptance rates.
- Update Θ smoothly to keep average **λ** near target.
- Protect against oscillations with **min/max step clamps** and windowed estimates.

Implementation: `consensus/difficulty.py`, tests in `consensus/tests/test_difficulty_retarget.py`.

---

## Incentives & Miner Strategy

Miners choose a **portfolio** of proofs:

- The **marginal acceptance gain** is the slope of **ψ** w.r.t. cost (units, time, fees).
- Under caps/escort rules, optimal miners diversify until **weighted marginal ψ per unit cost** equalizes across chosen proofs (plus the always-available HashShare).
- A miner with no useful proofs still participates via HashShare; useful-work miners gain a probabilistic edge.

**AICF** (AI Compute Fund) integrates economics:
- Providers stake/attest, get matched to off-chain jobs, and produce proofs.
- Pricing/splits and SLA enforcement ensure **ψ correlates with quality/effort**.
- See `aicf/` module and docs.

---

## Security Intuition

- **Unpredictability**: `H(u)` is memoryless and cannot be hoarded.
- **Bounded advantage**: Σ ψ cannot exceed Γ; escort rules prevent single-type capture.
- **Replay resistance**: proofs carry **nullifiers** (type-specific, time-scoped) so the same evidence can’t be reused across blocks.
- **Determinism**: verifiers are deterministic, bounded, and version-pinned (policy roots / VK cache).

If every useful-work path fails (market outage), the chain **continues** with HashShare alone: **graceful degradation.**

---

## Parameters at a Glance

Defined in `spec/params.yaml` and `spec/poies_policy.yaml`:

- **Θ target** and EMA config (window, clamps).
- **Γ** (total ψ cap) and per-type caps.
- **Weights/curves** mapping proof metrics → ψ.
- **Escort/diversity** thresholds.
- **Nullifier TTL** windows.

Consistency checks have vectors in `spec/test_vectors/proofs.json` and unit tests under `consensus/tests/`.

---

## Math Sketch (intuition)

- **HashShare**: If `u ~ U(0,1]`, then `X = −ln(u)` is **Exp(1)**. This is a classic way to turn a uniform draw into an additive exponential “ticket” with nice memoryless properties.
- **Evidence**: Map each proof’s verified metrics `m` through a **monotone, concave** function into ψ to avoid corner solutions (e.g., diminishing returns). Clip by caps.
- **Acceptance**: `S = X + Σ ψ`. With **Θ** chosen by retarget, the block interval approaches the target without oscillation.

---

## Implementation Pointers

- **Policy load & caps**: `consensus/policy.py`, `consensus/caps.py`
- **Scoring & acceptance**: `consensus/scorer.py`
- **Difficulty/retarget**: `consensus/difficulty.py`
- **Nullifiers / proof verifiers**: `proofs/*`
- **Mining portfolio / selector**: `mining/proof_selector.py`
- **Receipts for micro-targets**: `consensus/share_receipts.py`

Tests demonstrate accept/reject correctness and stability: see `consensus/tests/test_scorer_accept_reject.py`, `consensus/tests/test_alpha_tuner.py`, and related fixtures.

---

## FAQ

**Q: Can ψ alone win without a lucky H(u)?**  
A: ψ helps *lower the needed H(u)*, but Σ ψ is capped by Γ. Pure ψ cannot deterministically win; there is always a residual lottery component.

**Q: Could one work type dominate?**  
A: Per-type caps + escort rules + α-tuner (fairness correction) prevent long-run dominance.

**Q: What if verifiers become faster over time?**  
A: That’s fine—Θ retarget handles global rates; **policy** and **gas** updates can re-weight ψ to keep economics aligned.

---

## Upgrade & Governance

Any change to:
- ψ-mappings, caps, or Γ, or
- verifier registries / VK caches,

is **policy-rooted** and typically gated by a **hard fork** boundary (see `docs/spec/UPGRADES.md`). The first block after activation **pins** the relevant roots.

---

## Summary

PoIES is a **hybrid, incentive-compatible** path to embed **useful, verifiable work** into consensus without sacrificing the **safety and simplicity** of hash-based randomness. It aligns miner profits with **socially valuable** computation and storage, governed by conservative caps and transparent policy.

