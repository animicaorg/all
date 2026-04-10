# VDF — Wesolowski Parameters, Security Level, Verification Algorithm

This note specifies the **consensus-relevant** shape and verification of the Wesolowski VDF used by Animica’s randomness beacon (see `randomness/vdf/*`). Proving is **off-chain** (miners/workers); verification is **on-chain/consensus**, so every step and parameter here is binding.

---

## 1) Construction (RSA / unknown-order group)

We instantiate a sequential function over a group of unknown order. The reference implementation targets **RSA groups** modulo `N = p·q` (safe generation procedure), with a production path that also supports **class groups** (no trusted setup). Verification is identical up to the base/encoding; only the group arithmetic differs.

- **Input seed**: `X ∈ {0,1}*` derived from the commit–reveal aggregate and previous beacon  
  `X = H("rand/vdf-input" | aggregate | prev_beacon)` (SHA3-256).
- **Delay parameter**: integer `T ≥ 1` (number of squarings), calibrated to a wall-clock target (§4).
- **Group**:
  - *Devnet / testnets*: RSA-2048 (≈112-bit classical security).  
  - *Production recommended*: RSA-3072 (≈128-bit) **or** Class Group (parameters chosen for ≥128-bit security).

---

## 2) Hash-to-base and transcript domains

All hashes use **SHA3-256** with domain separation:

- **Base element** `x ∈ 𝔾*`:

x = H_to_Zn_star(“rand/vdf/base” | X) mod N

If `gcd(x, N) ≠ 1`, increment `x ← x+1 (mod N)` until `gcd(x, N) = 1`.  
(For class groups, map via standard hash-to-classgroup.)

- **Challenge prime** `ℓ` is derived from a **hash-to-prime** function:

ℓ = HashToPrime(“rand/vdf/chal” | N || x || y || T, k_bits)

where `y = x^(2^T) (mod N)` and default `k_bits = 128`. The algorithm must produce a prime of exactly `k_bits` with rejection sampling (deterministic from the transcript).

**Consensus note:** Verifiers MUST recompute both `x` and `ℓ` from the transcript; proofs must not carry mutable inputs for these.

---

## 3) Wesolowski proof

Compute `y = x^(2^T) (mod N)` via repeated squaring. Let:
- `ℓ` be the 128-bit prime challenge from above,
- `q = ⌊2^T / ℓ⌋`,
- `r = 2^T mod ℓ` (equivalently `r = pow(2, T, ℓ)`).

The **succinct proof** is:

π = x^q (mod N)

The prover outputs `(y, π)`. The verifier recomputes `ℓ` and `r` from the transcript.

---

## 4) Verification algorithm (consensus-critical)

**Inputs:** `(N, X, T, y, π)`.

1. Derive base `x = H_to_Zn_star("rand/vdf/base" | X) (mod N)`, ensuring `gcd(x, N)=1` (reject if not).
2. Recompute challenge prime `ℓ = HashToPrime("rand/vdf/chal" | N || x || y || T, 128)`.
3. Compute `r = pow(2, T, ℓ)`.
4. Check the Wesolowski equation:

y  ==  (π^ℓ * x^r) mod N

5. Optional sanity: reject trivial `x ∈ {0,1}` and `y ∈ {0,1}` unless `T=0` (which we never allow on-chain).

**Complexity.** Verification is `O(log ℓ + log T)` exponentiations (one large exp with exponent `ℓ`, one with exponent `r < ℓ`) — orders of magnitude cheaper than proving.

---

## 5) Parameters & security levels

### Security parameter (group hardness)
- **RSA-2048**: ~112-bit security (classical). Suitable for devnets and early testnets.  
- **RSA-3072**: ~128-bit security. Recommended minimum for mainnet if using RSA.  
- **Class Groups**: choose discriminant sizes yielding ≥128-bit security; avoids trusted setup.

### Challenge size
- `k_bits = 128` for `ℓ`. This offers negligible probability that an adversary can cheat by grinding the transcript to a composite `ℓ` or mount a root-extraction trick.

### Delay `T`
- Let `τ_hw` be the empirically measured time per squaring (seconds/squaring) for reference hardware.
- Target per-round sequential delay `Δ` (e.g., 2–10 seconds). Then:

T = floor(Δ / τ_hw)

- The helper in `randomness/vdf/time_source.py` performs this calibration; networks may fix `T` directly in `params`.

**Soundness intuition.** Without knowledge of the group order (or factorization of `N`), computing `x^(2^T)` requires `T` sequential squarings. A valid `(y, π)` binds to that unique value via the prime challenge.

---

## 6) Serialization (wire format)

Consensus objects are CBOR-encoded (see `randomness/types/core.py`):

- `VDFInput`: `{ x_seed: bytes, T: uint }` where `x_seed = X` (the preimage seed).
- `VDFProof`: `{ y: bstr, pi: bstr }` — `ℓ` is not sent; verifiers recompute from the transcript.
- `BeaconOut`: includes `X`, `T`, `y`, and `VDFProof` (and optional QRNG mix tag).

All integers use canonical CBOR encodings; byte strings are fixed-length for `y`/`π` according to `|N|` (big-endian, left-padded).

---

## 7) Edge cases & validation rules

- **Non-invertible base**: if `gcd(x, N) ≠ 1`, verifier MUST reject (indicates bad hashing or malformed `N`).
- **Zero/one trap**: reject `x ∈ {0,1}` at admission; these collapse the sequence.
- **Transcript binding**: `(N, X, T, y)` must be exactly the values in the block’s randomness record. Any mismatch invalidates the proof.
- **Domain separation**: audit tags `"rand/vdf-input"`, `"rand/vdf/base"`, `"rand/vdf/chal"`; they must never collide with other subsystems.

---

## 8) Optional features (non-consensus)

- **Batch verification**: multiple `(y_i, π_i)` over the same `N` can be batched (random linear combination of equations). This is an optimization only; MUST NOT change acceptance semantics.
- **Pietrzak variant**: Not used in consensus; allowed only as a prover-side optimization if it yields the same `(y, π)` pair as Wesolowski for given transcript (generally it won’t), so consensus sticks to Wesolowski.

---

## 9) RSA modulus generation & trust

- **Dev/test**: modulus may be embedded (known-origin). **Do not** reuse for mainnet.
- **Mainnet options**:
1) **Multi-party RSA ceremony** (no single party learns factors); include transcript hash in genesis.  
2) **Class group** to avoid trusted setup entirely; select parameters to match ≥128-bit security and document the mapping for `H_to_base`.

If an entity knows `φ(N)`, they can compute `x^(2^T)` faster (via Euler reduction), breaking sequentiality. Hence modulus governance is critical.

---

## 10) Pseudocode (verifier)

```text
verify_vdf(N, X, T, y, pi):
x = hash_to_base("rand/vdf/base", X, N)         // in Z_N^*
if gcd(x, N) != 1: return False
ell = hash_to_prime("rand/vdf/chal", N || x || y || T, 128)
r = pow(2, T, ell)
lhs = y % N
rhs = (pow(pi, ell, N) * pow(x, r, N)) % N
return lhs == rhs


⸻

11) Testing checklist
	•	Vectors cover multiple T (including large T), multiple moduli sizes.
	•	Negative vectors: wrong π, wrong y, wrong T, wrong transcript binding, composite ℓ.
	•	Timing-safe exponentiation paths (no branches on secrets).
	•	Cross-impl compatibility (Python verifier vs any auxiliary prover).

⸻

12) Defaults (suggested)
	•	k_bits: 128
	•	RSA modulus: 3072-bit for mainnet; 2048-bit for devnet
	•	Delay: Δ = 4 s (tune per network → implies T via calibration)

