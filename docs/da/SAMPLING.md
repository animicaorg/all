# Data Availability Sampling (DAS)
_Algorithm, guarantees, and how to size your samples_

This document specifies the **Data Availability Sampling (DAS)** procedure used by light clients and auditors to probabilistically detect unavailable data behind a block’s **DA root** (NMT commitment). It derives exact and approximate **failure probabilities** and gives guidance for configuring the sampler.

> Related specs & code  
> • Layout: `docs/da/ERASURE_LAYOUT.md`, `da/erasure/*`  
> • NMT: `da/specs/NMT.md`, `da/nmt/*`  
> • Sampling helpers: `da/sampling/{sampler.py,probability.py,queries.py,scheduler.py}`

---

## 1) Threat model & goal

- Blocks commit to an ordered sequence of **extended shares** (data + parity) via a Namespaced Merkle Tree (NMT).
- Adversary tries to **withhold shares** so the blob (or some blob stripe) cannot be reconstructed.
- A client samples a small number `q` of randomly chosen shares and verifies their inclusion proofs (and, when relevant, namespace-range proofs).
- **Goal:** With probability ≥ `1 − ε`, detect any withholding large enough to break decoding.

---

## 2) Notation

- Total leaves in the committed DA tree: `N`.  
- Block contains blobs split into `R` **stripes**; each stripe has `n` **coded shares** => total shares for a blob is `R·n`.  
- RS code: `RS(k, n)` with erasure radius `t = n − k`. To make a stripe undecodable, adversary must withhold `m ≥ t + 1` shares **in that stripe**.  
- Sampler draws `q` shares **without replacement** uniformly over the selected population (whole tree, per-namespace, or per-stripe).

---

## 3) The core probabilities

### 3.1 Global sampling over a population of size `N` with `w` withheld
Exact **hypergeometric** miss probability (no withheld share is sampled):
\[
p_{\text{miss}}(q; N, w) \;=\; \frac{\binom{N-w}{q}}{\binom{N}{q}}
\]
Common approximation (sampling is independent with replacement) for intuition:
\[
p_{\text{miss}} \;\approx\; (1 - \tfrac{w}{N})^{q}
\]
Thus for target failure `ε`, a back-of-the-envelope sizing is:
\[
q \;\gtrsim\; \frac{\ln(1/ε)}{w/N}
\]

> **Important:** If the adversary concentrates their withholding in a **single stripe** (cheapest attack), then `w/N` can be very small when sampling over the *entire* `N`, making global-uniform sampling inefficient. Stratification fixes this.

### 3.2 Per-stripe stratified sampling
If the sampler takes `q_s` samples **inside a given stripe** (population `n`) and the adversary withholds `m` in that stripe:
\[
p_{\text{miss, stripe}}(q_s; n, m) \;=\; \frac{\binom{n-m}{q_s}}{\binom{n}{q_s}}
\;\approx\; \left(1 - \frac{m}{n}\right)^{q_s}
\]
With minimal breaking `m = t + 1 = (n-k)+1`, this yields a tight per-stripe bound. For `R` independent stripes and a scheduler that ensures at least `q_s` samples per attacked stripe, a **union bound** gives:
\[
p_{\text{fail}} \;\le\; R \cdot p_{\text{miss, stripe}}(q_s; n, m)
\]
(Usually over-conservative; in practice, schedule to cover random stripes each round.)

---

## 4) Recommended sampling strategies

1. **Stratified-by-stripe (preferred):**  
   - Each round, select a set of stripes uniformly at random; within each selected stripe, sample `q_s` random columns.  
   - Rotate stripes across rounds to cover the matrix over time.  
   - Detects the **minimal-cost** attack (concentrated in one stripe) with small `q_s`.

2. **Namespace-scoped sampling:**  
   - When auditing a specific blob/namespace, sample only within its leaf interval.  
   - Use the per-stripe logic above with that blob’s `(R, n, k)`.

3. **Global uniform (fallback):**  
   - Simple to implement; use the `N, w` hypergeometric model.  
   - Requires larger `q` if withholding is concentrated.

The reference scheduler (`da/sampling/scheduler.py`) implements (1) and (2) with **query plans** from `da/sampling/queries.py`.

---

## 5) Worked examples (RS(8,12))

Let `k=8, n=12 ⇒ t=4`, so the attacker must withhold **`m = t+1 = 5`** shares in a stripe to break it.

### 5.1 Per-stripe samples needed (approximate, with replacement)
\[
p_{\text{miss, stripe}} \approx \left(1 - \frac{m}{n}\right)^{q_s} = \left(1 - \frac{5}{12}\right)^{q_s} = \left(\frac{7}{12}\right)^{q_s}
\]

| Target ε (per attacked stripe) | Required `q_s` |
|---:|---:|
| 1e-6  | ⌈ln(1e6)/ln(12/7)⌉ ≈ **26** |
| 1e-9  | ⌈ln(1e9)/ln(12/7)⌉ ≈ **39** |
| 2⁻⁴⁰ ≈ 9e-13 | ⌈(40·ln2)/ln(12/7)⌉ ≈ **52** |

If a client touches ~`q_s=40` *in the attacked stripe*, the chance of missing the attack is ≈ `1e-9`. A round scheduler that hits ~1/4 of stripes per round will amortize total queries linearly across time.

### 5.2 Global uniform sizing intuition
Suppose the block has `N = 1,000,000` leaves and attacker withholds only `w = 5` (focused in one stripe). Then
\[
p_{\text{miss}} \approx (1 - 5/10^6)^{q} \approx e^{-0.000005\,q}
\]
To push `p_miss ≤ 1e-9` would need `q ≈ 20.7 / 0.000005 ≈ 4.1M` global samples — impractical.
**Conclusion:** global uniform is poor against concentrated attacks; use stratification.

---

## 6) Canonical verification steps

For each sampled `(namespace, stripe r, column c)`:

1. **Fetch extended share** bytes and its **NMT inclusion proof**.  
2. Verify the proof against the block’s `da_root` and (if relevant) a **namespace-range proof** that the leaf belongs to the blob’s namespace interval.  
3. Record `(r,c)` as **present** if verification succeeds.  
4. **Flag unavailability** immediately when:  
   - Any inclusion proof fails, **or**  
   - A stripe accumulates `> t = n-k` **missing**/unverifiable positions (equivalently, fewer than `k` verifiably present).

> Implementations should cache verified columns per stripe to avoid redundant proofs in later rounds.

---

## 7) Choosing sampler budgets

- Let `ε_target` be your acceptable failure probability for **one attacked stripe** in a **single audit window**.  
- Choose per-stripe `q_s` from the table/formula in §5.  
- Decide **coverage fraction** `α ∈ (0,1]`: fraction of stripes you will visit per round (e.g., 25%).  
- Over `T` rounds, expected distinct stripes visited ≈ `R·(1-(1-α)^T)`. Set `T` to achieve desired coverage lag.

**Rule of thumb (RS(8,12))**
- **Light client:** `q_s ≈ 32`, `α ≈ 25%`, `T ≈ 4` ⇒ strong detection within a few rounds.  
- **Auditor/full light:** `q_s ≈ 48`, `α ≈ 50%`, `T ≈ 2` ⇒ sub-second detection at higher bandwidth.

---

## 8) Exact vs approximate math

- Use hypergeometric formulas for exact bounds (without replacement).  
- The `(1 − f)^q` approximation is accurate when `q ≪ population` and helps build intuition or do quick sizing.  
- The helper functions in `da/sampling/probability.py` expose **both** versions.

---

## 9) Multi-blob & namespace considerations

- If sampling by **namespace**, the population is that blob’s `R·n` leaves. Use per-stripe formulas.  
- If sampling across **many blobs**, prefer building a **stratified plan per namespace**, then interleave requests (batch proofs) to reduce overhead.

---

## 10) Implementation notes & pitfalls

- **Randomness:** Use a cryptographically secure RNG; seed with `(blockHash, clientSalt, roundCounter)` to avoid bias and enable replayable audits.  
- **Proof batching:** When the DA layer supports subtree proofs, coalesce sibling requests to cut bandwidth.  
- **Time outs / partial responses:** Treat timeouts as **missing** for the purpose of counting toward the `> t` threshold (DoS-robust).  
- **Eviction:** Cache recent proof successes until a reorg boundary; invalidate on reorg beyond the sampled block.

---

## 11) Checklist

- [ ] Stratified per-stripe sampling implemented (not only global uniform)  
- [ ] Proof verification against `da_root` with namespace-range checks  
- [ ] Correct `t = n−k` threshold logic per stripe  
- [ ] RNG is CSPRNG with block-bound seeding  
- [ ] Scheduler covers stripes over rounds with tunable `α` and `T`  
- [ ] Uses exact hypergeometric for reporting; approximation only for sizing

---

## 12) References

- RS layout & parameters: `docs/da/ERASURE_LAYOUT.md`  
- NMT proofs & ranges: `da/specs/NMT.md`  
- Sampler & probability helpers: `da/sampling/*`  
- Light client flow: `docs/spec/LIGHT_CLIENT.md`

