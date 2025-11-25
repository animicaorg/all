# HashShare Proofs — u-draws, micro-targets, aggregation

This document specifies the **HashShare** proof kind used by PoIES as the
probabilistic “lottery” component that complements useful-work proofs. A
HashShare encodes a *u-draw* bound to a specific header template; nodes verify
the draw against a **micro-target** and convert it into the metric
`d_ratio` consumed by consensus scoring.

> Acceptance predicate at block assembly time:
> \( S = -\ln u \;+\; \sum \psi \;\ge\; \Theta \).
> A HashShare contributes via the \( -\ln u \) term; useful-work proofs contribute
> through \( \sum \psi \).

---

## 1) Goals & properties

- **Binding:** A share is bound to a concrete header template (height, chainId,
  mixSeed, nonce domain). It cannot be transplanted to another header.
- **Uniform draw:** The draw \( u \in (0,1] \) is derived deterministically from
  a domain-separated hash, modeled as uniform.
- **Micro-targeting:** Nodes and miners use a *share* threshold
  \( \theta_{\text{share}} \) stricter or looser than the current consensus
  target \( \Theta \) to control reporting rate and pool latency.
- **Aggregation:** Accepted shares included in a block are summarized as compact
  **receipts** and Merkle-aggregated into the `proofsRoot` with deterministic
  ordering.

---

## 2) Body format (CBOR, canonical)

HashShareBody = {

Header binding (selected fields from the template)

“chainId”:   uint,            # CAIP-2 chain id (spec/chains.json)
“height”:    uint,            # parent height + 1
“mixSeed”:   bstr .size 32,   # seed mixed into the nonce domain
“nonce”:     bstr .size 32,   # miner-chosen
“domain”:    tstr,            # “HASHSHARE/V1” (or future variants)

Optional helpers (do not affect u below if omitted)

“extra”:     bstr,            # reserved; length-capped in policy
}

**Envelope:** as defined in `proofs/OVERVIEW.md`.

- **Nullifier:** `nullifier = H("PROOF_NULLIFIER/HashShare" || cbor(HashShareBody))[:32]`
- **Header bind digest** (used only for computing `u`):  
  `bind = H("HEADER_BIND/HashShare" || chainId || height || mixSeed)`

All hashing in this document refers to SHA3-256 unless otherwise stated.

---

## 3) u-draw and score

The uniform draw is derived from a domain-separated hash of the binding and
nonce:

draw = H(“U_DRAW/HashShare” || bind || nonce)        # 32 bytes
u    = (int(draw) + 1) / 2^256                       # (0,1]
score = -ln(u)                                       # µ-nats in code, real here

- Adding `+1` avoids `u=0`.
- Implementations compute **fixed-point µ-nats** for `score` with safe clamps
  (see `consensus/math.py`).

---

## 4) Micro-target threshold

Let the current consensus threshold be \( \Theta \) (from the retarget schedule).
Miners and validators use a **share micro-target**

\[
\theta_{\text{share}} = \alpha \cdot \Theta, \quad \alpha \in (0,1]
\]

- \(\alpha=1\): share acceptance equals block acceptance threshold (rare shares).
- Smaller \(\alpha\): more frequent shares for smoother hashrate/latency
  estimation and incentives.

**Acceptance test for a share (stateless):**

valid_share := (score >= theta_share)  # theta_share supplied by template/policy

**Expected rate:** \( \Pr[\text{valid}] = e^{-\theta_{\text{share}}} \).

**Difficulty ratio metric (for observability):**
\[
d_{\text{ratio}} = \frac{\text{score}}{\Theta}
\]
This is carried in the derived **ProofMetrics** (see §7).

---

## 5) Verification algorithm (node)

Given `HashShareBody` and its envelope:

1. **Schema & size checks**
   - Canonical CBOR, bounded lengths (nonce 32, mixSeed 32, `extra` ≤ policy cap).
2. **Recompute bindings**
   - `bind = H("HEADER_BIND/HashShare" || chainId || height || mixSeed)`
   - `draw = H("U_DRAW/HashShare" || bind || nonce)`
   - `score = -ln((int(draw)+1)/2^256)` (clamped, fixed-point)
3. **Micro-target check**
   - Obtain `theta_share` from the current header template/context.
   - Reject if `score < theta_share`.
4. **Nullifier check**
   - Recompute nullifier; reject on mismatch.
   - Check against the **nullifier TTL set** (no reuse within sliding window).
5. **Emit metrics**
   - Build `ProofMetrics.HashShare { d_ratio, score_micro, theta_share_micro }`.

All failures raise `ProofError`/`SchemaError`/`NullifierReuseError` with
machine-readable reasons.

---

## 6) Aggregation → receipts → Merkle root

Accepted shares **included in a block** are summarized into *share receipts*
(`proofs/receipts.py`) and deterministically aggregated:

- **Receipt fields (conceptual)**

ShareReceipt = {
“nullifier”:  bstr .size 32,
“score_uN”:   uint,          # µ-nats fixed-point of score
“height”:     uint,          # binding height for traceability
“nonce_hint”: bstr .size 8,  # optional: first 8 bytes of nonce for audits
}

- **Ordering:** receipts are sorted by `(score_uN DESC, nullifier ASC)` before
Merkleization to yield a stable `proofsRoot`.
- **Merkle leaf:** `leaf = H("PROOF_RECEIPT/HashShare" || cbor(ShareReceipt))`
- The per-block `proofsRoot` commits to all proof receipts (HashShare and
useful-work kinds alike), enabling light clients to verify inclusion.

> Note: full envelopes need not be stored in the block; receipts suffice for
> accounting and audits while the **nullifier set** prevents replay.

---

## 7) Metrics → ψ (policy adapter)

HashShare’s numeric contribution to PoIES uses the raw `score` term in
\( S \). For observability, verifiers emit:

ProofMetrics.HashShare = {
d_ratio:         float,   # score / Theta
score_micro:     uint,    # fixed-point µ-nats
theta_share_micro:uint,   # fixed-point µ-nats
}

The **policy adapter** does **not** cap or scale this term; PoIES caps and
escort rules apply to **ψ from useful-work kinds**, not to the u-draw term.

---

## 8) Mining template & device loops (informative)

Mining uses the same formulas as verification, but evaluates in tight loops:

```python
bind = H("HEADER_BIND/HashShare" || chainId || height || mixSeed)
while work_active:
    nonce = rng()  # or device counter
    draw  = H("U_DRAW/HashShare" || bind || nonce)
    # accept if -ln((int(draw)+1)/2^256) >= theta_share

Share submission: accepted shares are wrapped as envelopes and submitted via
RPC or stratum WS endpoints; nodes can precheck cheaply before admitting to a
pending pool.

⸻

9) Security considerations
	•	Domain separation: Distinct tags for HEADER_BIND, U_DRAW, and
PROOF_NULLIFIER prevent cross-context reuse.
	•	Binding freshness: mixSeed changes per header template; transplanting a
share that binds to an old mixSeed fails.
	•	Entropy & bias: Using SHA3-256 with the header binding + nonce gives
uniform draws under standard assumptions.
	•	DoS limits: Envelope/body sizes are capped; early rejections (schema,
target) avoid expensive work. APIs rate limit submissions.
	•	Replay: Nullifiers are enforced with a TTL window (see
consensus/nullifiers.py); receipts include only the nullifier & scalars,
not the full nonce.

⸻

10) Test vectors (see repository)
	•	proofs/test_vectors/hashshare.json — header binds, nonces, expected scores,
accept/reject vs theta_share.
	•	consensus/tests/test_share_receipts.py — ordering and Merkle root
determinism.
	•	mining/tests/test_share_target.py — micro-target math and thresholds.

⸻

11) Parameterization

Network configs define:
	•	alpha (share multiplier) or directly theta_share policy per epoch.
	•	Max envelope size; extra max length.
	•	Nullifier TTL window length.

These live in spec/params.yaml and consensus/fixtures/* for tests.

⸻

12) Worked example

Given:
	•	Theta = 18.0 (µ-nats: 18_000_000)
	•	alpha = 0.25 ⇒ theta_share = 4.5
	•	Compute u from the hash ⇒ u = 0.002 ⇒ score = -ln(0.002) ≈ 6.2146
	•	Accept share (6.2146 >= 4.5), metrics:
	•	d_ratio = 6.2146 / 18.0 ≈ 0.345
	•	score_micro ≈ 6_214_600
	•	theta_share_micro = 4_500_000

Receipt hashes into proofsRoot with stable ordering.

⸻

Notation & hashing

Throughout:
	•	H(x) = SHA3-256, output 32 bytes.
	•	All CBOR uses canonical map ordering and minimal integer encodings.
	•	Fixed-point conversions follow consensus/math.py (µ-nats).

