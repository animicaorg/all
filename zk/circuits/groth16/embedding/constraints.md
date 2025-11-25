# Embedding Threshold Circuit (Groth16)
**Circuit goal:** Prove knowledge of an embedding vector **x** and model weights **w** such that the (quantized, fixed-point) dot product  
\[
S \;=\; \sum_{i=0}^{d-1} x_i \cdot w_i
\]
satisfies a public threshold test \(S \ge \tau\) without revealing **x** (and optionally without revealing **w**). Commitments are Poseidon hashes inside the circuit.

This circuit is intentionally **small and didactic** for test/demo purposes. It is not meant to validate production ML models as-is; see the safety notes below before using in the wild.

---

## Public / Private I/O

### Public signals (nPublic = 4)
1. `cx` – Poseidon commitment to the prover’s private embedding vector (and salt).  
2. `cw_meta` – Poseidon commitment to model metadata (weights and quantization parameters).  
3. `tau` – Threshold scalar \(\tau\) (already quantized).  
4. `y` – Boolean result bit: `1` iff \(S \ge \tau\); `0` otherwise.

> **Fixture mapping:** Our tiny test vectors use placeholders for commitments and `tau = 800`, `y = 1`. The order and count of public signals matches the verifying key in `vk.json`.

### Private witnesses
- `x[0..d-1]` – Quantized signed embedding components (integers).  
- `w[0..d-1]` – Quantized signed weight components (integers).  
- `salt_x` – Per-proof 254-bit blinding for the input commitment.  
- `meta` – Model metadata tuple embedded into `cw_meta` (see below).

---

## Commitment scheme (in-circuit)
We use **Poseidon** over BN254 with domain-separation tags (DST) for clarity and collision partitioning:

- Input commitment:
  \[
  cx = \text{Poseidon}\big(\texttt{"emb:v1"}, d, q\_scale, x_0,\ldots,x_{d-1}, \text{salt\_x}\big)
  \]
- Model commitment (exposed publicly as `cw_meta`):
  \[
  cw\_meta = \text{Poseidon}\big(\texttt{"mdl:v1"}, d, q\_scale, w\_0,\ldots,w_{d-1}, \tau\_policy\big)
  \]
  `tau_policy` is an application-level field encoding how `tau` is derived (e.g., fixed public `tau`, or per-deployment constant). In our tests, it’s a placeholder constant.

**Why commit inside the circuit?**  
It binds the arithmetic relation to the exact vectors used while hiding the private components; it also prevents reusing a valid proof with a different `(x, w, τ)` tuple.

---

## Quantization model
To stay in finite field arithmetic, floats are quantized to signed integers:

- Each real \( \tilde{x}_i \) is scaled by a constant `q_scale` (e.g., \(2^{10}\)) and rounded to an integer \( x_i \).
- Similarly, \( \tilde{w}_i \mapsto w_i \).
- The threshold is scaled once: \( \tilde{\tau} \mapsto \tau \).

`q_scale` is included in both commitments so verifiers (off-chain) can interpret units consistently.

---

## Constraints (high-level)

Let the field prime be \(p = 2188824287…95617\) (BN254). All range bounds below must ensure **no wraparound mod \(p\)**.

1. **Signal ranges.**  
   For each component we enforce:
   - `x_i = sxi * mxi` with `sxi ∈ {−1, +1}` and `mxi ∈ [0, Bx]`.  
   - `w_i = swi * mwi` with `swi ∈ {−1, +1}` and `mwi ∈ [0, Bw]`.  
   In Circom this is encoded with boolean sign bits and non-negative magnitudes with bit-decomposition range checks.

2. **Dot product.**  
   Multiply‐accumulate with controlled width:
   \[
   prod_i = (sxi \cdot swi) \cdot (mxi \cdot mwi),\quad
   S = \sum_i prod_i
   \]
   A partial-sum accumulator uses safe limb sizes so that \( S < p \) (see “No overflow” below).

3. **Comparator: \(S \ge \tau\).**  
   Use a LessThan gadget on the same fixed bit-width \(k\):  
   - `lt = LessThan(S, tau, k)`  
   - Constrain the public bit: `y = 1 - lt` and booleanity `y*(1-y)=0`.

4. **Poseidon commitments.**  
   Recompute `cx` and `cw_meta` with DSTs and constrain equality to the public signals.

5. **Booleanity & fixed sizes.**  
   - All sign bits and `y` are boolean.  
   - Arrays have fixed length `d`. No dynamic tails are allowed.

---

## Parameterization & safe bounds

Let:
- \(d\) be the vector length,
- \(B_x\) and \(B_w\) be the **magnitude** bounds for `x_i` and `w_i`,
- Comparator width \(k\) bits s.t. \(2^k > \max(S, \tau)\).

**Overflow guard:**  
\[
S_{\max} \le d \cdot B_x \cdot B_w \quad\text{must satisfy}\quad S_{\max} < p
\]
A practical safe choice for demos/tests is, for example:
- \(d \le 64\),
- \(B_x \le 2^{10}\), \(B_w \le 2^{10}\),
- Then \(S_{\max} \le 64 \cdot 2^{20} = 67{,}108{,}864 \ll p\).  
Set comparator width \(k=32\) or \(k=40\) for headroom.

> **Test fixtures:** Our sample proofs use small \(d\) (e.g., 8–16), modest bounds, and `tau = 800`. The commitments in the sample JSON files are placeholders to keep the artifacts tiny and human-readable.

---

## Constraint count (back-of-the-envelope)

For each dimension \(i\):
- 2 range checks (magnitudes) + 2 boolean sign bits,
- 1 multiplication for `mxi * mwi`,
- A few gates to fold signed product into accumulator.

Comparator (LessThan) of width \(k\) typically costs \(\mathcal{O}(k)\) constraints (implementation-dependent). Poseidon costs are per permutation (~ few hundred constraints per hash; Circom Poseidon ~ a few thousands for two hashes across all inputs).

**Order-of-magnitude:**  
For small demos (e.g., \(d \le 32\), \(k \le 40\)), total constraints are easily in the **low tens of thousands**, which is brisk for Groth16.

---

## Soundness & safety notes

1. **No modulo wraparound.**  
   The biggest pitfall for linear algebra in SNARKs is silent mod-\(p\) wrap. Use the bound above and fix the comparator bit-width \(k\) so that both \(S\) and \(\tau\) fit.

2. **Quantization integrity.**  
   Always commit to `q_scale` and enforce it inside both commitments. Otherwise, adversaries can rescale vectors to game the threshold.

3. **Domain separation.**  
   The Poseidon inputs include DSTs (`"emb:v1"`, `"mdl:v1"`) and fixed arities so different objects cannot collide across domains.

4. **Fixed dimension.**  
   The circuit **must** fix \(d\) at compile time (or commit to a fixed `d` inside both hashes and then recheck `d` inside the circuit). Variable-length vectors are disallowed.

5. **Boolean outputs only.**  
   Enforce `y ∈ {0,1}`. If you need a margin (e.g., “≥ τ by at least Δ”), include it in the comparator logic or the public policy, not off-circuit.

6. **Model secrecy vs transparency.**  
   - If weights must remain private, keep `cw_meta` as a commitment only and do not expose `w`.  
   - If transparency is desired, publish `w`, `q_scale`, and `d` off-chain, and verify `cw_meta` matches that data.

7. **Replay & binding to application domain.**  
   Include an **application tag** or **deployment ID** in both commitments (e.g., a chain ID, contract address, or “AICF:embedding:bn128:v1”) to prevent cross-context proof replay.

8. **Threshold provenance.**  
   `tau` is public; document whether it’s:
   - globally fixed at deployment,
   - or request-specific (e.g., on-chain parameter).  
   If it’s derived from `cw_meta`, enforce that relation in-circuit or keep it purely public but consistent with your policy.

---

## Example walkthrough (toy)

- \(d = 8\), `q_scale = 2^6`, small int vectors.  
- Prover commits to `x` with a random `salt_x`; model publisher commits to `(w, q_scale, d, tau_policy)` as `cw_meta`.  
- Circuit computes \(S\), compares against public `tau = 800`, emits `y = 1`.  
- Verifier checks Groth16 proof with the public tuple \((cx, cw\_meta, \tau, y)\).

This matches the ordering expected by our `vk.json` and small JSON fixtures (`public.json`, `proof.json`).

---

## Interop notes

- **Curve/Protocol:** BN128 + Groth16 (snarkjs layout).  
- **Hasher:** Poseidon (same parameters as the Circom standard library used by snarkjs).  
- **Public signal order:** `[cx, cw_meta, tau, y]` (do not reorder).  
- **VK IC length:** Must be `nPublic + 1 = 5`.

---

## Audit checklist

- [ ] Bounds \(d, B_x, B_w\) prove \(S_{\max} < p\).  
- [ ] Comparator width \(k\) exceeds `max(S, tau)` by comfortable margin.  
- [ ] `y` constrained boolean; `LessThan` wired as `y = 1 - lt`.  
- [ ] Commitments include DSTs and all necessary parameters (`d`, `q_scale`, and policy fields).  
- [ ] No dynamic array lengths; indices are range-checked or fixed.  
- [ ] Order of public signals matches VK/fixtures.  
- [ ] Test vectors include both paths (`y=0` and `y=1`) and edge cases (`S = τ`, `S = τ-1`).

---

## Known limitations

- Fixed-point rounding is done off-circuit. If adversarial rescaling is a concern, include an in-circuit check that ties `tau` and `q_scale` to `cw_meta`.  
- The toy fixture commitments are zero-like placeholders for parser/IO testing; they will **not** validate meaningful real-world instances without regenerating aligned artifacts (R1CS, zkey, vk, proof, and public JSON) from the actual Circom code.

