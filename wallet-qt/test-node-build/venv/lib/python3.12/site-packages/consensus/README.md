# Animica Consensus — PoIES Core

This module implements **PoIES** (Proof-of-Integrated-External-Services), our hybrid acceptance and retargeting layer that turns heterogeneous, verifiable work into block production weight.

It provides:

- Deterministic **accept/reject** of candidate blocks using a single scalar score:
  
  \[
  S = H(u) + \sum_{p \in \mathcal{P}} \psi(p) \qquad \text{accept iff } S \ge \Theta
  \]

  where \(H(u)=-\ln(u)\) is the hash share contribution from nonce sampling and \(\psi(p)\) are normalized, policy-bounded contributions from external proofs (AI, Quantum, Storage, VDF, …).

- **Caps** and **fairness** enforcement to avoid single-type dominance, plus an **α-tuner** that slowly compensates for type imbalances over time.
- **Fractional retargeting** of \(\Theta\) with stable EMA and clamps, targeting a configured mean block interval.
- **Fork choice** that is height-first with deterministic tie-breakers and optional weight-aware bias.
- **Nullifier** handling to prevent proof replay within sliding windows.
- **Share receipts** aggregation to allow micro-target accounting without bloating the block.

Everything is *pure* and *deterministic* given the inputs; there is no network I/O here.

---

## Quick map of files

- `math.py` – safe numerics; fixed-point μ-nats; \(H(u)=-\ln u\).
- `policy.py`, `caps.py` – load/validate **poies_policy.yaml** and apply per-proof/total Γ caps & escort rules.
- `scorer.py` – maps verified `ProofMetrics` → ψ values and aggregates \(S\).
- `difficulty.py` – EMA retarget for \(\Theta\), micro-targets for share difficulty.
- `fork_choice.py` – deterministic strategy (height → weight → hash).
- `interfaces.py` – `ProofVerifier` protocol and typed envelopes.
- `validator.py` – header/block acceptance: recompute \(S\), check policy roots, nullifiers, \(\Theta\).
- `nullifiers.py` – TTL window set; replay resistance.
- `share_receipts.py` – micro-target share accounting + Merkle aggregation.
- `alpha_tuner.py` – slow fairness correction across proof types.
- `state.py` – minimal in-memory state for sims/tests.

See `consensus/tests/*` for unit coverage, and `cli/bench_poies.py` to exercise the scorer.

---

## Concepts & Invariants

### 1) Acceptance Scalar

**Inputs per candidate block \(B\):**

- `u` – uniform variate derived from header nonce & mix domain (see `mining/nonce_domain.py`).  
- `P` – multiset of attached **proof envelopes** (hash shares, AI, Quantum, Storage, VDF).  
- `Θ` – current threshold from `difficulty.py`.  
- `policy` – loaded from `spec/poies_policy.yaml`, including:
  - per-type caps \( \Gamma_{\text{type}} \)
  - total cap \( \Gamma_{\text{total}} \)
  - escort/diversity requirements
  - α-tuner parameters.

**Score:**
\[
S = H(u) + \sum_{p \in P} \psi(p)
\]
with \(H(u)=-\ln(u)\) in μ-nats fixed-point. Proofs contribute via **metrics → ψ mapping** (see `proofs/policy_adapter.py`), *then* are clipped by `caps.py`.

**Invariant A (Monotonicity):**  
If `policy` fixed, adding a valid proof that increases \(\sum \psi\) cannot flip `accept`→`reject`.

**Invariant B (Safety of Caps):**  
For any block, per-type and total Γ caps bound \(\sum \psi \le \Gamma_{\text{total}}\). No single type can exceed its configured cap.

**Invariant C (Replay Safety):**  
Any proof with an already-observed **nullifier** within the sliding window is rejected (`NullifierReuseError`).

**Invariant D (Policy Roots):**  
The block’s header binds to the **alg-policy root** and **PoIES policy root**. Mismatch ⇒ reject.

---

### 2) Fairness & α-tuning

To prevent systematic bias toward a proof type with temporarily easier economics, an **α-tuner** adjusts per-type scaling slowly based on observed contribution shares over large windows. Bounds and hysteresis ensure stability and monotonic acceptance (see `alpha_tuner.py`).

**Invariant E (Slow movement):** α updates are rate-limited and clamped; a single block cannot shift scaling enough to invert acceptance of otherwise similar candidates.

---

### 3) Difficulty Retarget (Θ)

`difficulty.py` computes \(\Theta_{t+1}\) from recent inter-block intervals via an EMA:

logT_next = clamp(
(1-β) * logT_prev + β * logT_target + k*(observed - target),
min_logT, max_logT
)

Parameters (β, k, clamps) live in `spec/params.yaml`. Targets are in μ-nats for exact, deterministic fixed-point math.

**Invariant F (Stability):**  
Clamps prevent runaway oscillations; EMA ensures mean interval approaches target under stationary conditions.

---

### 4) Fork Choice

1. **Height wins.**
2. **If equal height:** optional **weight bias** (sum of S over last N) breaks ties.
3. **If still equal:** deterministic hash lexical order (or mixSeed order) breaks ties.

**Invariant G (Determinism):** Same view of headers yields same head on all honest nodes.

---

### 5) Nullifiers & Windows

Each proof kind defines how to derive its **nullifier** (domain-separated hash of body). `nullifiers.py` maintains a TTL window; reuse ⇒ reject and record reason.

**Invariant H (Bounded Memory):**  
Window and index structures have O(window) memory with periodic pruning.

---

## Units & Numerics

- **μ-nats**: all logs in micro-nats (1e-6 nats), stored as `int64`.  
- **Ratios**: Q32.32 fixed-point (see `math.py`).  
- **Rounding**: *floor* unless explicitly noted (documented in `math.py`).

This ensures reproducibility across languages and platforms.

---

## Data Flow

1. `proofs/*` verifies envelopes → emits **ProofMetrics**.
2. `consensus/policy_adapter.py` (in `proofs/`) maps metrics → ψ inputs (no caps).
3. `consensus/scorer.py`:
   - loads caps/escort settings (`policy.py`, `caps.py`)
   - clips & sums ψ
   - adds hash-share term \(H(u)\)
   - outputs \(S\) and a **breakdown** (per type, clipped vs unclipped).
4. `validator.py`:
   - checks **policy roots** in header
   - validates **nullifiers** window
   - recomputes \(S\) and compares with current `Θ`
   - returns `ACCEPT` / `REJECT(reason)` plus a compact **receipt**.

---

## Escort / Diversity Rules (Sketch)

Some networks may require minimum diversity, e.g.:

- At least `q` distinct proof types present, or  
- If a type exceeds its soft share, require at least one *escort* proof of another type.

These rules are pure functions in `caps.py` applied *before* final clipping. Violations produce a structured `PolicyError`.

---

## Security & Abuse Notes

- **Bypass via bogus metrics**: prevented by `proofs/*` verifiers; only verified metrics enter scoring.
- **Replay**: nullifier windows hard-reject.
- **Domination**: Γ caps + α-tuner + escort rules.
- **Time skew**: Θ retarget uses block arrival deltas (not wall clock); windows in block heights.
- **DoS**: scorer and validator are \(O(n)\) in proofs with small constants; schema checks happen earlier.

---

## Example (Pseudo)

```python
from consensus.scorer import score_block
from consensus.policy import load_policy
from consensus.difficulty import next_theta
from consensus.validator import validate_header

policy = load_policy("spec/poies_policy.yaml")
theta  = params.initial_theta
state  = InMemoryConsensusState()

for candidate in stream_candidates():
    result = validate_header(candidate.header, policy, theta, state)
    if result.accept:
        state = state.apply(candidate)  # update nullifiers, windows, EMA inputs
        theta = next_theta(state)
        publish_new_head(candidate)


⸻

Running Tests

This module ships a comprehensive test suite. From repo root:

# Fast unit suite
pytest -q consensus/tests

# Focused tests
pytest -q consensus/tests/test_scorer_accept_reject.py -k accept

# Nullifiers window
pytest -q consensus/tests/test_nullifiers.py

Smoke with fixtures:

pytest -q consensus/tests/test_validator_header_accept.py
pytest -q consensus/tests/test_difficulty_retarget.py

Bench

python -m consensus.cli.bench_poies --vectors spec/test_vectors/proofs.json \
  --policy consensus/fixtures/poies_policy.example.yaml \
  --theta 145000000  # μ-nats

Outputs acceptance % and per-type breakdown.

⸻

Integration Points
	•	Proofs: proofs/policy_adapter.py defines the exact metrics→ψ mapping used here.
	•	Mining: mining/proof_selector.py consumes the same policy to pack candidates under Γ.
	•	Core: core/chain/block_import.py calls into consensus/validator.py during import.
	•	RPC: Exposes per-block PoIES breakdown for Explorer (see explorer-web).

⸻

Configuration

Primary knobs live in:
	•	spec/params.yaml – Θ targets, EMA coefficients, windows.
	•	spec/poies_policy.yaml – per-type caps Γ, escort/diversity, α-tuner bounds.

These are hash-bound in headers via policy roots; changing them requires governance/upgrades.

⸻

Extending with a New Proof Type
	1.	Add verifier in proofs/ → emits ProofMetrics.
	2.	Extend proofs/policy_adapter.py to map metrics → ψ inputs.
	3.	Update spec/poies_policy.yaml with caps & weights.
	4.	Add unit tests:
	•	metrics mapping
	•	caps clipping
	•	accept/reject around Θ
	5.	(Optional) Add α-tuner entries and escort relations.

No changes to retarget or fork choice are needed.

⸻

Error Taxonomy
	•	ConsensusError – base.
	•	PolicyError – caps/escort/diversity violations, policy root mismatch.
	•	ThetaScheduleError – invalid Θ schedule or window underflow.
	•	NullifierError – proof replay within window.
	•	SchemaError – malformed inputs caught at the consensus boundary.

All are deterministic, structured, and safe to surface via RPC.

⸻

References in this repo
	•	spec/poies_math.md – derivations & rationale.
	•	spec/poies_policy.yaml – canonical policy.
	•	spec/params.yaml – thresholds & EMA.
	•	proofs/* – verifiers & metrics.
	•	mining/* – packing heuristics & micro-targets.

⸻

Repro Tips
	•	Use μ-nats everywhere for logs.
	•	Never mix floating-point into consensus paths.
	•	Keep windows in heights, not seconds.
	•	Ensure CBOR canonical maps → stable hashing of headers/policies.

Happy hacking! 🔬
