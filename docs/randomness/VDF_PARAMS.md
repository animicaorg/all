# VDF Parameters & Verification Costs (Wesolowski)

This note specifies the parameter profiles and expected **verification** costs for the randomness beacon’s VDF (Wesolowski). It complements `randomness/specs/VDF.md` and the implementation in `randomness/vdf/{params.py,verifier.py}`.

> TL;DR  
> - Choose **delay** by setting the iteration count `T`.  
> - Pick a **group of unknown order** and security level (modulus size).  
> - Use **l = 128**-bit soundness (challenge size) unless explicitly constrained.  
> - Verification is **very fast** (a couple big exponentiations + ~log₂(T) squarings); proof size ≈ one group element.

---

## 1) Construction (recap)

We compute

y = x^(2^T)  in a group of unknown order G

and produce a **Wesolowski proof** π with soundness parameter `l` (bits). The verifier recomputes a challenge `q = H_to_challenge(x, y, T, l)` and checks a short algebraic relation that avoids redoing `T` squarings. See `randomness/vdf/verifier.py` for the exact transcript and check.

- **Prover cost:** ~ `T` repeated squarings mod |G| (dominant).  
- **Verifier cost:** ≈ 1–2 exponentiations in |G| plus ~log₂(T) squarings; constant-size proof.

> We parameterize the group via **RSA moduli** by default (dev/test), and allow an alternative **class-group** backend (planned). RSA requires an appropriate trust model for modulus generation (see §7).

---

## 2) Parameters

### 2.1 Security & Group Size

| Profile   | Group (default) | |N| (bits) | Est. classical security* | Proof elem size |
|-----------|------------------|-----------|---------------------------|------------------|
| localnet  | RSA              | 2048      | ~112-bit                  | 256 bytes        |
| testnet   | RSA              | 3072      | ~128-bit                  | 384 bytes        |
| mainnet   | RSA / ClassGrp   | 3072–4096 | ≥128-bit (recommend 3072+) | 384–512 bytes    |

\*Rough NIST-style equivalence for factoring hardness.

**Soundness parameter (l):** `l = 128` (bits) by default. Raising to 192/256 slightly increases verification work (more squarings) for higher soundness margins.

### 2.2 Delay (Iterations)

Let:
- `target_delay_secs` — desired prover wall-clock time (e.g., 15s / 30s / 60s).
- `iters_per_sec_ref` — measured reference squarings/sec on the **intended prover class**.

Then:

T = floor(target_delay_secs * iters_per_sec_ref)

We provide calibration helpers in `randomness/vdf/time_source.py` to measure `iters_per_sec_ref`.

> **Guidance:** pick `T` so a typical prover finishes well within the finalize window `Tv` (see `rand.getParams`). Leave verification comfortably < 50 ms on commodity CPUs.

### 2.3 Recommended Profiles

| Network   | |N| bits | l (bits) | target_delay_secs | Example T (see note) |
|-----------|---------:|---------:|------------------:|---------------------:|
| localnet  | 2048     | 128      | 5                 | 5 * iters_per_sec    |
| testnet   | 3072     | 128      | 20                | 20 * iters_per_sec   |
| mainnet   | 3072–4096| 128–192  | 30                | 30 * iters_per_sec   |

> **Note:** `iters_per_sec` depends on prover hardware. For indicative values: a modern server CPU might sustain 50–150K mod-squarings/sec at 3072-bit; laptops will be lower. Always calibrate in your environment.

---

## 3) Verification Cost Model

Verification performs:

- **Challenge derivation:** hash-to-challenge (`l`-bit).
- **A few modular exponentiations** in |N| (one with a large exponent derived from the challenge; one more for a remainder term) and a small number of **squarings** (~log₂(T), usually < 64 for practical T ranges when using sliding windows / decomposition).
- **One proof element** (|N|-byte) and the output `y` (|N|-byte) are parsed.

### 3.1 Rough Latency Budget (x86-64, AVX2, single core)

| |N| bits | l | Expected verify (ms) | Notes |
|---------:|--:|--:|-------------------:|------|
| 2048     |128| ~ | 2–5                | CI laptops, dev boxes |
| 3072     |128| ~ | 5–12               | Target ≤ 15 ms budget |
| 4096     |128| ~ | 10–25              | Still OK for per-round |

Values include parsing and hashing; they assume tuned big-integer libs. ARM results are similar on big cores.

> **Gas/CPU limits:** We expose `vdf_verify_budget_ms` in chain params. Set it slightly above p95 of empirical measurements.

---

## 4) Transcript & Domains

All hashes are **domain-separated** (see `randomness/constants.py`) and bound to:
- `round_id`, `prev_beacon`, `agg` (commit–reveal aggregate),
- `vdf_params` (|N|, l, backend),
- `x` (seed base), `T`.

Changing any parameter causes `vdf_in` and verification to change; this prevents cross-parameter replay.

---

## 5) Sizes (wire / storage)

| Artifact  | Size (bytes) @2048 | @3072 | @4096 |
|-----------|--------------------:|------:|------:|
| `y`       | 256                 | 384   | 512   |
| `π`       | 256                 | 384   | 512   |
| Overhead (domains, T, params) | ~64–96 | ~64–96 | ~64–96 |

Proof size equals one group element for Wesolowski. The transcript metadata adds a small constant.

---

## 6) Calibrating T

1. Run `randomness/bench/vdf_verify_time.py` and a simple prover micro-bench in your environment.  
2. Choose `target_delay_secs` for the round finalize window (`Tv`).  
3. Compute `T = floor(target_delay_secs * iters_per_sec_ref)`.  
4. Backtest: ensure p95 prover finishes within `Tv`; p99 verifier is < `vdf_verify_budget_ms`.  
5. Record `(N_bits, l, T)` in `randomness/vdf/params.py` for the network.

> **Conservatism:** prefer slightly *lower* `T` with wider time window than pushing `T` to the edge of `Tv`.

---

## 7) Trust Model & Backends

- **RSA modulus (default):** Requires trustworthy generation (no known factorization). For dev/test, a deterministic RSA modulus is acceptable. For public networks, prefer:  
  - **MPC-generated RSA** (multi-party ceremony), or  
  - **Class groups** (no trapdoor) — roadmap item.
- **Attestations:** If a specific modulus is used, record its provenance hash in chain params and docs.

---

## 8) Failure & Fallback

- If no valid proof arrives within `Tv`, the round remains pending. Operators MAY allow late finalization as a policy (documented and visible in headers). Avoid silent fallback to PRNG; if you must degrade, require an explicit network flag and publish an alert.

---

## 9) Example Config Snippet

```python
# randomness/vdf/params.py (illustrative)
VDF_PARAMS = {
  "localnet":  {"group": "rsa", "n_bits": 2048, "l_bits": 128, "target_delay_secs": 5},
  "testnet":   {"group": "rsa", "n_bits": 3072, "l_bits": 128, "target_delay_secs": 20},
  "mainnet":   {"group": "rsa", "n_bits": 3072, "l_bits": 128, "target_delay_secs": 30},
}


⸻

10) Checklist
	•	Calibrate iters_per_sec_ref on representative hardware.
	•	Pick n_bits (≥3072 for public networks).
	•	Set l_bits = 128 (raise only with strong justification).
	•	Choose T from target_delay_secs.
	•	Set vdf_verify_budget_ms from p95 verify timings (+ margin).
	•	Lock domains & params; publish in chain params and docs.
	•	Add test vectors for (x, T) → (y, π) under chosen params.

⸻

References
	•	randomness/specs/VDF.md — algorithm & verification details
	•	randomness/vdf/{verifier.py,params.py,time_source.py} — code & helpers
	•	randomness/test_vectors/vdf.json — canonical vectors

