# zk/ PERFORMANCE.md

This note summarizes **expected verification costs** for the built-in verifiers
and where **native backends** can accelerate execution. It also shows how our
**deterministic metering** (units) relates to real wall-clock time.

> TL;DR:
> - Metering is **input-size based**, not time-based. Units are stable across
>   machines and do not depend on CPU/GPU.
> - Real latency depends on your backend: pure-Python is fine for dev, native
>   libraries give **10×–100×** speedups for pairing/MSM heavy paths.

---

## Baselines & terminology

- **Kinds**:
  - `groth16_bn254` → pairing heavy (≈3 pairings + MSM on G1).
  - `plonk_kzg_bn254` → MSM + KZG check (≈2 pairings for single opening).
  - `stark_fri_merkle` → hash/Merkle heavy (no pairings).
- **Backends**:
  - **Python**: reference implementations (e.g., `py_ecc` for BN254 arithmetic).
  - **Native** (optional): bindings to mature libs (e.g., `mcl` / Arkworks) for EC/MSM/pairing,
    and optimized SHA3/Poseidon.
- **Units (meter)**: computed **before** cryptographic checks from proof/VK sizes
  and public input count. See `zk/integration/policy.py`.

---

## Expected costs (order-of-magnitude)

The table below gives **illustrative** vCPU-millisecond ranges for a single proof
on a mid-range x86_64 core (3–4 GHz), single thread, warm cache.

| Kind                | Python ref (ms) | Native (ms) | Dominant work                 |
|---------------------|-----------------|-------------|-------------------------------|
| Groth16 BN254       | 300–1200        | 2–10        | 3 pairings + G1 MSM           |
| PLONK+KZG BN254 (1 opening) | 500–2000 | 5–20        | MSM + 2 pairings + transcript |
| STARK FRI (toy Merkle AIR)  | 50–200   | 5–30        | Keccak/Poseidon + Merkle      |

> Ranges vary with circuit/public-inputs/proof size. **Metering units do not**.

---

## How metering maps to performance

We use a linear, deterministic cost model:

units = base
+ per_public_input * num_public_inputs
+ per_proof_byte   * proof_bytes
+ per_vk_byte      * vk_bytes
+ per_opening      * kzg_openings   # PLONK/KZG only

- This captures what actually dominates runtime:
  - **Bytes parsed/hashed** (proof/VK).
  - **Number of public inputs** (scalar ops / MSM).
  - **KZG openings** (pairings count).
- Operators can tune the constants per-kind without affecting correctness.

---

## Where native speedups come from

### Pairings & MSM (Groth16 / PLONK+KZG)
- **Techniques**: windowed/Pippenger MSM, multi-pairing reducers, endomorphism, subgroup checks.
- **Libraries** (BN254):
  - **mcl** (C++), **Arkworks (ark-bn254)** (Rust).  
  - Python bindings (CFFI/CPython ext) can yield **50×–200×** speedups vs pure Python.

### Hashes (STARK / transcripts)
- Use SHA3/Keccak implementations in C (or Rust) for **~10×** over Python.
- **Poseidon**: precompute round constants, unroll S-box; SIMD helps on CPUs.

### KZG
- Single-opening verification is ~**2 pairings** + a few MSMs. With native EC arithmetic,
  KZG verify generally lands in the **low single-digit ms** range.

---

## Practical guidance

### 1) Pick your backend
- Dev & CI: Python refs are OK (deterministic, easy to debug).
- Prod: enable native bindings for EC/MSM/pairing and SHA3/Poseidon.

> Check your logs: verifiers should announce the selected backend
> (e.g., `pairing_bn254: backend=mcl`, else `backend=py_ecc`).

### 2) Keep JSON small & canonical
- Proof/VK encoded size impacts both **metering** and **parse time**.
- Avoid cosmetic whitespace/keys; the pipeline canonicalizes anyway.

### 3) Cache what you can
- **VK**: keep it in-memory; hashing is cheap but JSON parse isn’t.
- **IC MSM (Groth16)**: pre-compute linear-combination structure from VK if your
  deployment runs repeated verifications for the same circuit.

### 4) Threading & batching
- The reference path is **single-threaded** (deterministic ordering).
- If you batch verifications **outside** the verifier (application layer),
  you amortize JSON parse & VK decode.

---

## Micro-benchmark recipes

### Quick wall-clock timing for a single payload
```bash
PYTHONOPTIMIZE=1 python - <<'PY'
import json, time
from zk.integration.omni_hooks import zk_verify

payload = json.load(open("payload.json"))
t0 = time.perf_counter()
res = zk_verify(payload)  # full verify
dt = (time.perf_counter()-t0)*1e3
print(f"verify_ms={dt:.2f}", "ok=", res["ok"], "units=", res["units"])
PY

Measure pure-Python vs native

Run the same payload twice: once with native disabled (e.g., force fallback) and once
with native enabled. Capture verify_ms and compare.

Ensure the backend switch actually took effect (see logs/backends banner).

⸻

What to expect by kind

Groth16 (BN254)
	•	~3 pairings + MSM over IC points.
	•	Native libraries reduce pairing to ~1–3 ms each and MSM to sub-millisecond, so
2–10 ms end-to-end is common with warm caches.
	•	Python ref may sit in hundreds of ms per proof.

PLONK + KZG (BN254, single opening)
	•	Transcript hashing + MSM (commitment combos) + ~2 pairings.
	•	With native EC: 5–20 ms typical; Python: 0.5–2 s on larger proofs.

STARK FRI (toy Merkle AIR)
	•	Dominated by hash + Merkle branch verifications; scales with
num_queries * (log2(domain) + branch_length).
	•	With optimized SHA3/Poseidon: 5–30 ms; Python: 50–200 ms.

⸻

Profiling checklist
	•	Separate parse/normalize time from crypto time.
	•	Count pairings/MSM/hash calls (expose counters in debug builds).
	•	Check VK cache hit-rate.
	•	Validate that meter units grow linearly with proof/VK sizes as expected.

⸻

Caveats
	•	These numbers are guidelines, not SLAs; real hardware, compiler flags,
and the specific circuit matter.
	•	Units are not a proxy for milliseconds; they’re a stable pricing metric.

⸻

Appendix: knobs you can tune (deployment)
	•	Backend selection: prefer native EC/hashes when available; fall back to Python.
	•	Policy constants: adjust base, per_* to match your economics.
	•	Parser: a faster JSON library (e.g., orjson) can help if proof blobs are large
(keep canonicalization semantics consistent for hashing).
	•	Warm-up: import verifiers early to JIT/hit caches before hot paths.
