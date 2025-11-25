# VDF_BONUS — Optional VDF Credit, Parameters, and Verification

**Status:** Draft, production-intent  
**Scope:** Verifier-facing specification for *VDF bonus* proofs that contribute non-negative score \( \psi_{\text{VDF}} \) to PoIES, independently of (but consistent with) the randomness beacon’s VDF.  
**Variant:** Wesolowski VDF over an RSA modulus of unknown order (default), parameterized per-network.

**Consumes:** CBOR envelope per `proofs/schemas/proof_envelope.cddl`, body per `proofs/schemas/vdf.cddl`.  
**Produces:** `ProofMetrics.VDF` used by PoIES scorer and a compact **receipt** included in the `proofsRoot`.

> This document defines how properly-parameterized VDF proofs earn a *bonus* toward block acceptance while also remaining compatible with the beacon VDF used by `randomness/` (if enabled).

---

## 1) Motivation & model

- **Motivation.** Encourage participants to spend verifiable sequential time on an agreed computation—helpful either to *finalize* a commit–reveal beacon (see `randomness/vdf/*`) or, optionally, to harden anti-grinding. In networks where *miners* do not always run the beacon prover, VDF effort may still be credited as useful work via PoIES.
- **Model.** A prover commits to evaluating \( y = g^{2^T} \bmod N \) for difficulty parameter \(T\) over modulus \(N\) with short proof \( \pi \) (Wesolowski). The verifier runs a few modular exponentiations and a hash to confirm correctness. Work scales with \(T\) and wall-clock time, is **sequential** (no known parallel speedups), and verification is cheap.

---

## 2) Body — CBOR layout (informative)

Canonical encoding by `proofs/cbor.py`. Schema: `proofs/schemas/vdf.cddl`.

VdfProofV1 = {
“domain”:     “VDF_V1/Wesolowski”,
“chainId”:    uint,
“height”:     uint,            # the block height / epoch this proof binds to
“seed”:       bstr .size 32,   # input derived from the beacon commit phase or policy seed
“modulus_id”: tstr,            # which RSA modulus (policy-pinned) was used
“g”:          bstr,            # base (as big-endian bytes), policy may fix it (e.g., H(seed))
“y”:          bstr,            # result g^(2^T) mod N
“pi”:         bstr,            # Wesolowski proof
“T”:          uint,            # iterations (sequential steps)
“params”: {
“lambda”:  uint,            # security level indicator (e.g., 128)
“l_bits”:  uint             # challenge size for quotient (e.g., 256)
},
“provider”: {
“id”:      tstr,
“pubkey”:  bstr,
“nonce”:   bstr .size 16
},
“bind_sig”:   bstr             # signature over bind message (see §4)
}

Envelope fields:  
`type_id = 0x05` (VDF V1), `body = cbor(VdfProofV1)`, `nullifier = H("PROOF_NULLIFIER/VDF_V1" || body)[:32]`.

All `H()` below are **SHA3-256** unless stated. Integers are CBOR uints; big integers in `bstr` are big-endian without leading zeros.

---

## 3) Parameterization

Network policy (`randomness/vdf/params.py` & `zk/integration/policy.py`) must pin:

- **Modulus registry.** `modulus_id → N` (bytes) with provenance (see §11).  
  - Recommended: RSA-2048 or RSA-3072 *ceremony-generated* composite with at least one unknown prime factor.  
  - Alternative: class groups of unknown order (no trusted setup). Mapping differs but interface stays similar.
- **Difficulty \(T\).** Target wall time per proof (e.g., 5–30 seconds on commodity CPUs).  
- **Base \(g\).** Either constant generator or *derived from seed*: `g = OS2IP(H("VDF_BASE" || seed)) mod N` with cofactor fix (ensure `g` in \(\mathbb{Z}_N^*\)).  
- **Challenge size \(l\_bits\).** 128–256 bits (Wesolowski quotient challenge).  
- **Binding window.** Which `height` and `seed` the proof must reference (anti-precompute).
- **Caps/Weights.** PoIES weight `w_VDF`, max credit per-epoch `cap_VDF`, and per-prover **cooldown** to avoid spam.

---

## 4) Bindings & anti-replay

To prevent reusing a valid (seed, T) proof across epochs or chains:

BASE = H(“VDF_INPUT” || chainId || u64(height) || seed)

If policy uses derived base:

g = OS2IP(BASE) mod N, reduced into Z*_N

BIND_VDF = H(
“VDF_V1/BIND” ||
modulus_id || SER(g) || SER(y) || SER(pi) ||
u64(T) || u32(params.l_bits) || u32(params.lambda) ||
u64(height) || seed || provider.nonce
)

bind_sig = Sign_provider(BIND_VDF)

Verifier **must** recompute `g` when policy states so, and reject if the body’s `g` disagrees.

---

## 5) Verification algorithm (Wesolowski)

Given \( (N, g, y, \pi, T, l\_bits) \):

1. **Domain checks.** Validate envelope `type_id`, nullifier, chainId/height consistency, and policy-pinned `modulus_id`.
2. **Seed & base.** If policy mandates derived base, recompute `g` from `seed`; else accept provided `g` after checking `g ∈ \mathbb{Z}_N^*` (gcd\(g, N\)=1).
3. **Challenge.** Compute \( \ell = H_{\text{chal}}(N||g||y) \) as a \( l\_bits \)-bit integer (non-zero).  
   - Implementation note: Use SHA3-256 or SHA3-512 and truncate/expand to `l_bits`.
4. **Exponent split.** Compute \( r = 2^T \bmod \ell \) and \( q = \lfloor 2^T / \ell \rfloor \).  
   - Verification uses provided \( \pi = g^q \bmod N \).
5. **Check.** Compute \( \text{lhs} = g^r \cdot \pi^{\ell} \bmod N \). Accept iff \( \text{lhs} \equiv y \ (\bmod N) \).
6. **Signature.** Verify `bind_sig` under `provider.pubkey` over `BIND_VDF`.
7. **Window.** Ensure `height` is within the policy’s acceptance window for the referenced `seed` (anti-precompute / freshness).

**Complexity.** Verifier cost: ~2 modular exponentiations (one with exponent \(r\) and one with \(\ell\)) plus small ops; independent of \(T\).

**Failure modes.** `ProofError::{BadDomain, BadModulus, BadBase, ChalZero, CheckFail, BindingFail, WindowExpired}`.

---

## 6) Metrics emitted

Verifier produces:

ProofMetrics.VDF = {
iterations:    int,      # T
est_seconds:   float,    # policy-calibrated T / iters_per_sec_ref
modulus_bits:  int,      # |N|
l_bits:        int,
provider_id?:  str
}

The **est_seconds** uses a reference calibration (see `randomness/vdf/time_source.py`) or a chain constant. It is advisory; caps apply.

---

## 7) Mapping → ψ_VDF (PoIES)

Consensus maps metrics to score:

\[
\psi_{\text{VDF}} = \min\Big( w_{\text{VDF}} \cdot \text{est\_seconds},\ \text{cap}_{\text{VDF}} \Big)
\]

**Notes**
- **Monotonicity.** Larger \(T\) increases credit up to `cap_VDF`.  
- **Freshness.** Only proofs bound to the *current* window (height/seed) earn credit.  
- **Uniqueness.** Per-window **nullifier** prevents double-credit of identical bodies.  
- **Diversity.** Escort rules may limit total Γ from VDF bonus relative to other work kinds.

---

## 8) Interop with the Beacon

When `randomness/` is enabled:

- The **beacon** derives `seed` and expects *one* canonical VDF result \(y^*\) for the round; light clients verify via `randomness/beacon/light_proof.py`.
- **Bonus proofs** may:
  - (A) Reproduce the **same** parameters as beacon (preferred): then \(y\) must equal \(y^*\).  
  - (B) Use **alternate \(T\)** or *extra* evaluations chained from \(y^*\): credit is still valid if bound to the same seed/window and verified.
- Policy may award **extra weight** for proofs that match the network’s canonical beacon parameters (to encourage helpful work).

---

## 9) Security & pitfalls

- **Unknown order.** RSA-based Wesolowski requires at least one party not knowing \( \phi(N) \). Use a multi-party ceremony (discard factors). Consider class groups to avoid RSA setup.
- **Base malleability.** Always bind \(g\) to the **seed** or pin a constant per-network. Disallow weak bases (g=1 mod N).
- **Precompute.** Enforce height/epoch windows and seed binding; otherwise a prover could hoard valid tuples.
- **Side channels.** Exponentiations should be done in constant-time-ish code; however verification is public data.
- **Parameter drift.** Pin `l_bits`, `|N|`, and allowed `T` ranges in policy; reject out-of-range inputs.

---

## 10) Timing & windows

- **Window definition.** For height \(h\), let `seed = H("VDF_SEED" || chainId || h - Δ || beacon_commit)`.  
  - `Δ` (delay) ensures seed is not known too early.  
- **Acceptance.** Only accept VDF proofs for `height ∈ [h, h + W)` where `W` is a small window (e.g., 2–3 blocks).
- **Cool-down.** Throttle per-provider to one credited proof per window unless policy allows multiple with diminishing returns.

---

## 11) Modulus registry & provenance

- `modulus_id` **must** resolve to a pinned modulus blob and metadata (creation ceremony transcript, signers, hash).  
- Store in repository under `randomness/vdf/params.py` or `zk/registry/registry.yaml` with hashes mirrored in release notes.  
- Upgrades require a coordinated fork (or dual-acceptance period with both old/new ids).

---

## 12) Receipts & on-chain footprint

- The compact receipt includes: `(modulus_id, T, l_bits, height, seed_hash, y_hash)`.  
- Full \(y\) and \( \pi \) need not be in receipts if bodies are referenced elsewhere (policy choice).
- Receipt hash participates in `proofsRoot` for block headers.

---

## 13) Test vectors

- `proofs/test_vectors/vdf.json` — valid & invalid (bad π, wrong r, stale window, bad base).  
- `randomness/test_vectors/vdf.json` — beacon-specific vectors with the same modulus and seed rules.  
- Unit tests: `proofs/tests/test_vdf.py`.

---

## 14) Reference implementation notes

- Use `pow_mod(base, exp, N)` with sliding-window or Montgomery reduction.  
- Compute \(2^T \bmod \ell\) via repeated-doubling mod \( \ell \) (or fast exponent with bigints).  
- Hash-to-challenge: `ell = OS2IP( SHA3-256(N||g||y) ) & ((1<<l_bits)-1); if ell==0: ell=1` and optionally set top bit to ensure size.  
- Validate `gcd(g, N)=1`, `1 < g < N`, `1 < y < N`, `1 < π < N`.

---

## 15) Versioning

- `domain = "VDF_V1/Wesolowski"`, `type_id = 0x05`.  
- Future `VDF_V2` may:
  - Support **class group** VDFs (no RSA setup).  
  - Support **Pietrzak** proofs.  
  - Add batched verification hints.

---

## 16) Policy summary (per-network)

- `modulus_id` → modulus \(N\), size (bits), provenance hash.  
- `T_min`, `T_max`, `T_default`.  
- `l_bits` (challenge size), `λ` (security label).  
- Window: delay `Δ`, acceptance `W`.  
- PoIES: `w_VDF`, `cap_VDF`, per-provider cooldown, diversity caps.

---

### Notation

- `OS2IP` = octet-string to integer primitive (big-endian).  
- `SER(x)` = canonical serialization (CBOR for structs; big-endian for integers in bstr fields).  
- `H(x)` = SHA3-256 unless otherwise pinned.

