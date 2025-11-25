# VDF Security: Hardness Assumptions & Parameterization

This note documents the security foundations and practical parameter choices for the VDF used in Animica’s randomness beacon (commit → reveal → **VDF** → mix). We target a **Wesolowski-style** VDF in an **unknown-order group** (RSA or class groups), chosen for: (1) strong *sequentiality*, (2) **unique** output, and (3) **succinct** proofs verified in \(O(\log T)\) group ops.

---

## 1. Threat Model & Goals

**Adversary goals.**
1. Compute the VDF output faster than honest time \(T\) (break sequentiality).
2. Produce a valid proof for a *wrong* output (soundness failure).
3. Grind inputs to bias the beacon or gain parallel/ASIC advantage.
4. DoS verifiers (excessive verification cost) or exploit trapdoors.

**Defenses.**
- Use an unknown-order group with no exploitable structure (RSA \(\mathbb{Z}_N^\*\) with hidden factorization or class groups).
- Derive bases from unpredictable transcripts; enforce **unique** VDF outputs.
- Size parameters to resist precomputation and parallel shortcuts.
- Keep verification significantly cheaper than evaluation.

---

## 2. Scheme Snapshot (Wesolowski)

Let \(G\) be an unknown-order group and \(x \in G\) the base derived from the beacon transcript. Set time parameter \(T\) (number of squarings).

- **Eval:** \(y = x^{2^T}\).
- **Challenge prime:** \( \ell \leftarrow \mathrm{HashToPrime}(\text{transcript} \parallel x \parallel y)\) (≈128-bit).
- **Proof:** \( \pi = x^{\left\lfloor 2^T / \ell \right\rfloor} \).
- **Verify:** Check \( y \stackrel{?}{=} \pi^\ell \cdot x^{2^T \bmod \ell} \).

**Costs.**  
Prover: \(T\) squarings.  
Verifier: a handful of exponentiations with exponents of size \(\log \ell = O(128)\) and \(\log T\). Proof size ≈ size of one group element (e.g., 256–384 bytes for 2048–3072-bit RSA).

---

## 3. Hardness Assumptions

1. **Sequential Squaring in Unknown-Order Groups.**  
   No classical adversary can compute \(x^{2^T}\) in \(o(T)\) group operations (up to polylog factors), even with massive parallelism, without trapdoor information.

2. **Unknown Order / Trapdoorlessness.**
   - **RSA group:** order \(\phi(N)\) is hidden; factoring \(N = pq\) yields a trapdoor that *breaks* sequentiality. Hence modulus generation must ensure no party learns \(p,q\).
   - **Class group:** the group order is unknown with **no trapdoor** (preferred when avoiding any trusted setup). Security may rely on standard number-theory assumptions (sometimes with GRH in analyses).

3. **(Strong) RSA / Adaptive Root-like Assumptions.**  
   Security of quotient proofs in Wesolowski relies on the difficulty of extracting roots relative to random primes \(\ell\) in unknown-order groups.

4. **Low-Order Assumption (LOA).**  
   Prevent adversaries from landing in small-order subgroups. We hash to a subgroup (e.g., quadratic residues) and reject trivial elements.

5. **Hash-to-Prime Unpredictability.**  
   The challenge \(\ell\) must be derived via a collision-resistant, bias-resistant **hash-to-prime** from the transcript that includes \(y\).

---

## 4. Group Choices

### RSA \(\mathbb{Z}_N^\*\) (with \(N = pq\))
- **Pros:** Mature libraries, small proofs, fast verification.
- **Cons:** Requires a **multi-party RSA ceremony** to ensure *no one* knows \(p,q\). If anyone keeps a factorization, they can accelerate the VDF (break sequentiality).

### Class Groups (imaginary quadratic orders)
- **Pros:** **No trusted setup/trapdoor**; unknown order is inherent.
- **Cons:** Heavier constants and engineering complexity; proof sizes similar to RSA element size.

**Recommendation.**  
- **Mainnet (no trust in ceremonies):** Class group or a publicly audited multi-party RSA where at least one participant is demonstrably honest (robust, widely verified transcript).  
- **Testnets/devnets:** RSA-2048 acceptable for speed; do **not** reuse modulus across deployments.

---

## 5. Security Levels & Parameter Tiers

We target ~**128-bit** classical security margins.

- **RSA modulus size \( |N| \):**
  - **Level-1 (mainnet default):** **3072-bit** (\~128-bit NIST strength).
  - **Dev/Test:** 2048-bit (faster; lower margin).

- **Challenge prime \( \ell \):** 128-bit (e.g., prime near \(2^{128}\)).  
  Cheating probability then \(\lesssim 2^{-128}\) assuming soundness of the quotient check.

- **Hashing:** SHA3-256 into group + hash-to-prime (try-and-increment or randomized FNV over 128-bit window), domain separated.

- **Time parameter \(T\):** chosen to yield wall-clock \(t_\mathrm{eval}\) per round on commodity hardware.
  - Mainnet guidance: **12–30 s** per VDF (tune by calibration).
  - Derive \(T = \left\lfloor t_\mathrm{target} \cdot R \right\rfloor\) where \(R\) is measured squarings/s of the *reference* implementation on target class hardware.  
  - Re-calibrate per network upgrade (see Ops §8).

---

## 6. Adversarial Considerations

### 6.1 Parallelism & Precomputation
- Best-known attacks yield **sublinear** speedups with massive memory or precomputation and do **not** asymptotically beat sequential squaring.  
- **Many-target precomputation** (tables over bases) is mitigated by:
  - Deriving \(x\) from **unpredictable** transcript inputs (aggregate reveals + previous beacon).
  - Hashing **into a fixed subgroup** (e.g., QR) with per-round domain separation.

### 6.2 Trapdoor Risks (RSA)
- Anyone who learns \(p,q\) can compute discrete logs/root extractions and shortcut the VDF.  
- Use an **MPC modulus ceremony** with reliable entropy sources; **destroy all shares**; publish transcripts; require a minimum set of independent organizers.

### 6.3 Low-Order Elements
- Ensure \(x \neq 1\) and lies in a large-order subgroup. In RSA, set \(x = h^2 \bmod N\) with \(h\) from hash; in class groups, use cofactor clearing/validation appropriate to the representation.

### 6.4 Grinding & Bias
- Miners cannot anticipate \(x\) before reveals close: the seed binds to **aggregate reveals** and previous beacon output.  
- Use fixed \(T\) per round (or narrow band) to prevent **free parameter** grinding.

### 6.5 DoS on Verifiers
- Cap \(T\) and proof size on input.  
- Verification is \(O(\log T)\); reject out-of-policy inputs at the RPC/consensus layer.  
- Rate-limit proof submissions; cache partial exponentiations of \(x^{2^k \bmod \ell}\) per round if needed.

---

## 7. Implementation Notes

### 7.1 Hash to Group (RSA)

u = SHA3-256(domain || seed)  -> integer in [0, N-1]
x = (u * u) mod N              # quadratic residue subgroup
if x == 1: rehash with counter

### 7.2 Hash to Prime (128-bit)

c = SHA3-256(domain || x || y)
ell = NextPrime( c[0:16] as 128-bit integer, with try+increment )

### 7.3 Verification Equation
Given \(q = \left\lfloor 2^T / \ell \right\rfloor\) and \(r = 2^T \bmod \ell\):
- Check \( y \stackrel{?}{=} \pi^\ell \cdot x^r \).
- Reject if any element is not in the expected subgroup or if sizes exceed policy.

### 7.4 Determinism & Uniqueness
- Transcript includes: network id, round id, \(T\), commitments, prior beacon, hash suite, group params version.  
- **Unique output** ensures no ambiguity for fork choice & light clients.

---

## 8. Parameter Calibration & Ops

1. **Measure** reference squarings/s on commodity CPU (and optionally GPU/ASIC baselines if public).  
2. **Set \(t_\mathrm{target}\)** (e.g., 20 s) \(\Rightarrow\) compute \(T\).  
3. **Bench verify time** on light clients; keep verify \(\ll 100\) ms on typical hardware.  
4. **Rotate** only with a governed upgrade: bump group params version, keep **backward-compatible verification** for historic rounds.  
5. **Monitor:** log evaluation time distributions; alert on anomalies (unexpectedly fast proofs could signal trapdoor compromise).

---

## 9. Suggested Defaults (Animica)

| Network | Group | \(|N|\)/Disc. | \(\ell\) bits | \(t_\mathrm{target}\) | Notes |
|---|---|---:|---:|---:|---|
| **Mainnet** | Class group **or** RSA-MPC | 3072 | 128 | 20 s | No-trapdoor preference; audited ceremony if RSA |
| **Testnet** | RSA | 2048 | 128 | 10 s | Faster iteration; *not* reused for mainnet |
| **Devnet** | RSA | 2048 | 64–128 | 3–5 s | Developer convenience only |

---

## 10. Side-Channel & Implementation Hygiene

- Constant-time modular arithmetic where feasible (even though inputs are public, code reuse and shared libraries warrant caution).  
- Validate all inputs; reject malformed or boundary cases.  
- Keep **domain separation** strings versioned; pin hash suites (e.g., `SHA3-256`).

---

## 11. Security Checklist

- [ ] Unknown-order group params pinned, documented, and versioned  
- [ ] If RSA: public MPC transcript; independent audits; no modulus reuse  
- [ ] Hash-to-group avoids small subgroups; low-order checks present  
- [ ] \(\ell\) from hash-to-prime (≥128-bit); transcript binds \(x,y\)  
- [ ] \(T\) bounded; verify work \( \ll \) eval work; RPC limits in place  
- [ ] Reproducible builds; deterministic arithmetic flags; test vectors  
- [ ] Monitoring for anomaly detection (too-fast proofs, verify failures)

---

## 12. References (informal pointers)

- Wesolowski VDF, Pietrzak VDF, class-group VDF constructions  
- RSA groups and MPC modulus ceremonies  
- Hash-to-prime techniques; low-order subgroup checks in unknown-order groups

*These are high-level reminders; consult the primary papers/specs in the repository docs and formal notes.*

---

**Summary.** Using Wesolowski in an unknown-order group with 128-bit challenge primes and 3072-bit mainnet parameters provides robust sequentiality and succinct verification. Proper input binding, ceremony hygiene (if RSA), and operational limits preserve unpredictability and DoS-resilience for the beacon.
