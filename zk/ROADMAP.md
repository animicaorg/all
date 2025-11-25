# ZK Roadmap — Planned Circuits & Aggregation

This living roadmap details the near-term and mid-term plans for:
1) **Reference circuits** (with stable public-input layouts) intended for common on-chain use,
2) **Aggregation & recursion** strategies to reduce verification costs (pairing SNARKs, PLONK-KZG, STARK compression),
3) **Acceptance criteria**, benchmarking targets, and migration strategy.

> Status keywords: **DONE**, **IN-PROGRESS**, **PLANNED**, **RESEARCH**  
> Consensus profile (default): **BLS12-381**, KZG SRS `bls12-381-kzg-v1`, hash set `{keccak256, sha256, blake3}` for STARK transcripts/Merkle. See `zk/README.md` for verifier capabilities and the envelope format.

---

## 0. Goals & Non-Goals

**Goals**
- Ship **stable, reuse-friendly circuits** with clear, versioned ABI for their public inputs.
- Enable **cost-effective verification** via batching/aggregation/recursion where safe.
- Maintain **determinism** and strict **resource caps** at the syscall boundary.

**Non-Goals**
- Turnkey prover stacks for every circuit (we provide references + test vectors; apps may optimize).
- Novel cryptographic primitives; we prioritize well-deployed constructions.

---

## 1) Reference Circuit Catalog

Each circuit below includes a mnemonic ID, proof system focus, public inputs ABI sketch, and acceptance tests. Gate counts and proof sizes are **order-of-magnitude** estimates (not binding), assuming standard libraries.

### A. Merkle Membership & Consistency

**ID**: `merkle_inclusion_v1` / `merkle_consistency_v1`  
**Systems**: Groth16(BLS12-381), PLONK-KZG; STARK reference later  
**Hash options**: keccak256, sha256, blake3, poseidon (field-native)  
**Public inputs (example)**:

root: bytes32
leaf: bytes32
index: uint64          # path index
hash_id: uint8         # enum of allowed hashes

**Estimates**:  
- Groth16: ~20–60k constraints (Keccak is larger; Poseidon is smallest)  
- Proof size: ~256–384 B (Groth16), ~1–15 KiB (PLONK-KZG)  
**Status**: IN-PROGRESS  
**Acceptance**:
- Vectors for 2^d trees (d ∈ {16, 20, 24}) with randomized branches.
- Mismatched path detection, out-of-range `index`, and wrong `hash_id`.

---

### B. Range Proof (Unsigned, 32/64/128-bit)

**ID**: `range_u32_v1`, `range_u64_v1`, `range_u128_v1`  
**Systems**: PLONK-KZG primary (via lookup tables); Groth16 optional  
**Public inputs**:

value_commitment: bytes32  # commitment to value
upper_bound: uint128       # optional; default = 2^k - 1

**Estimates**:
- PLONK-KZG with lookups: low-mid 10k constraints  
- Proof size: ~5–12 KiB  
**Status**: PLANNED  
**Acceptance**:
- Correctness against bit-decomposition baseline.
- Negative tests: values outside range, malformed commitment.

---

### C. EdDSA / ECDSA Signature Verification (Statement Binding)

**ID**: `eddsa_ed25519_v1`, `ecdsa_secp256k1_v1`  
**Systems**: Groth16 (legacy compatibility), PLONK-KZG (preferred)  
**Public inputs**:

pubkey: bytes(32 | 33 | 64) # compressed/uncompressed as specified by profile
msg_digest: bytes32
signature: bytes(64 | 65 | 70) # per scheme profile

**Estimates**:
- Ed25519: ~50–120k constraints; secp256k1: ~80–200k  
- Proofs: SNARK ~0.3–20 KiB (system dependent)  
**Status**: RESEARCH → PLANNED (impl gated on standardized arithmetization choice)  
**Acceptance**:
- Test vectors (RFC/compliance suites), invalid subgroup points, edge-case `s=0`/high-`s`.

---

### D. Set Membership (Poseidon-Based, Small Domain)

**ID**: `set_membership_poseidon_v1`  
**Systems**: PLONK-KZG  
**Public inputs**:

set_root: bytes32
element: field_element

**Estimates**:
- Constraints: ~10–30k (depends on arity & tree depth)  
**Status**: PLANNED  
**Acceptance**:
- Inclusion/exclusion vectors; arity parameterization documented.

---

### E. KZG Opening Check (Off-Chain Polynomial Evaluation Proof)

**ID**: `kzg_opening_v1`  
**Systems**: Groth16 & PLONK-KZG (SNARK that verifies a KZG opening)  
**Public inputs**:

commitment: G1_compressed
z: field_element
y: field_element
srs_id: string (must match network)

**Estimates**:
- Constraints: dominated by pairing or IPA emulation (~100–300k)  
**Status**: RESEARCH  
**Acceptance**:
- Cross-check against on-chain native KZG verifier for the same SRS.

---

### F. STARK-to-SNARK Compression Wrapper

**ID**: `stark_wrap_v1`  
**Systems**: PLONK-KZG (wrapping a STARK verifier circuit)  
**Public inputs**: *profile-dependent descriptor* (program id, root, claimed outputs)  
**Estimates**:
- Constraints: high (0.5M–5M) → used off-chain, result verified once on-chain  
**Status**: RESEARCH  
**Acceptance**:
- Equivalence tests vs native STARK verification off-chain.

---

### G. Join-Split Skeleton (Confidential Transfer Primitive)

**ID**: `joinsplit_skeleton_v1`  
**Systems**: PLONK-KZG  
**Public inputs**:

in_commitments[2], out_commitments[2], anchor, nullifiers[2], memo_hash

**Estimates**:  
- Constraints: 1–5M (sparse variant, no full note encryption stack)  
**Status**: RESEARCH (long-horizon; security review gated)  
**Acceptance**:
- Conservation and no-double-spend invariants; simulated audits.

---

## 2) Aggregation & Recursion Roadmap

Aggregation reduces the **number of on-chain verifies** or their per-verify cost.

### Stage 1 — Pairing SNARK Batch (Same System, Same Curve)
**Scope**: Aggregate/batch verify Groth16 (BLS12-381) with shared VK.  
**Approach**: Random-linear combination of pairings; single pairing product check.  
**Target**: Reduce N verifies to ~1–2 pairing checks.  
**Status**: IN-PROGRESS  
**Acceptance**:
- For N ∈ {2, 4, 8, 16}: correctness vs individual verification; failure isolation strategy.

### Stage 2 — PLONK-KZG Multi-Proof Amortization
**Scope**: Batch MSMs and pairing checks for multiple proofs with same SRS/VK.  
**Approach**: Pippenger MSM batching + multi-pairing.  
**Target**: 2–5× wall-clock reduction vs sequential.  
**Status**: PLANNED  
**Acceptance**:
- Benchmarks at sizes {2, 8, 32} with CPU caps; determinism preserved.

### Stage 3 — Recursive SNARKs (Proof-Carrying Proofs)
**Scope**: Off-chain recursion; on-chain verifies **one** recursive proof.  
**Candidates**: Halo2-KZG recursion, Nova/SuperNova (IPA), or equivalent.  
**Target**: Weekly rollups of 10^3–10^5 subproofs.  
**Status**: RESEARCH  
**Acceptance**:
- Soundness alignment with upstream libs; cost model documented; bounded depth profile.

### Stage 4 — STARK Compression (Wrap STARK in SNARK)
**Scope**: Turn a large STARK into a small pairing SNARK for on-chain.  
**Target**: 10–50× size reduction; verification → few pairings.  
**Status**: RESEARCH  
**Acceptance**:
- Equivalence harness; adversarial fuzz of transcript/FRI layers.

> **Policy**: Aggregators/recursors **never** run in consensus directly; they are off-chain tools. The chain verifies only the final artifact with a deterministic verifier.

---

## 3) Costs & Targets (Non-Binding, For Planning)

| Item                          | Unit Verify (now) | Aggregated Target | Notes |
|------------------------------|-------------------|-------------------|------|
| Groth16 (single)             | 1–3 pairings      | —                 | Already small |
| Groth16 (N=8 batch)          | 8× pairings       | ~1–2 pairings     | Stage 1 |
| PLONK-KZG (single)           | 8–40 ms           | —                 | CPU-bound MSM |
| PLONK-KZG (N=8 batch)        | ~8× time          | 2–4× time         | Stage 2 |
| STARK (1 MiB)                | 80–150 ms         | —                 | Bandwidth bound |
| STARK→SNARK wrapped          | —                 | Groth16-like      | Stage 4 |

*Latency numbers are approximate on modern x86/ARM with pairing accel; see `tests/bench` for methodology.*

---

## 4) Versioning & Migration

- Circuit IDs are suffixed with **`_vX`**. Any change to public-input order/meaning bumps the version.
- Verifying keys (VKs) are mapped via **registry** entries (on-chain), enabling smooth rotation.
- **Deprecation** policy: announce `vN` sunset when `vN+1` is stable; maintain both for one release train.

---

## 5) Acceptance Tests & Artifacts

For each circuit:
- **Golden vectors** under `tests/fixtures/proofs/<circuit>/`:
  - `ok/*.json` (valid proofs), `bad/*.json` (structural & semantic failures)
- **Property tests**:
  - Input domain boundaries, malformed encodings, subgroup/curve violations.
- **Benchmarks**:
  - Verify time distribution (p50/p90), memory peak, and envelope size.
- **Docs**:
  - Public-input ABI table, constraints & hash choices, VK derivation steps.

---

## 6) Risks & Mitigations

- **SRS trust & lifecycle** (SNARK/KZG): mitigate with **updatable SRS**, governance around `srs_id`, automated conformance checks.
- **Determinism drift** across platforms: CI gates with cross-arch vectors (x86_64 AVX2/SHA, AArch64 NEON/SHA3).
- **DoS via huge proofs**: strict byte/shape caps and gas/weight schedule per syscall.
- **Library churn**: pin versions; add conformance harnesses; avoid experimental forks in consensus context.

---

## 7) Milestones

- **M1**: Merkle Inclusion v1 (Groth16 + PLONK-KZG), vectors & benches — *IN-PROGRESS*
- **M2**: Groth16 batch verify (N up to 16), deterministic schedule — *IN-PROGRESS*
- **M3**: Range u64 v1 (PLONK-KZG + lookups), vectors — *PLANNED*
- **M4**: PLONK-KZG multi-proof amortization (shared VK) — *PLANNED*
- **M5**: Ed25519 verify v1 — *PLANNED*
- **M6**: STARK→SNARK wrap prototype — *RESEARCH*

---

## 8) Contribution Guide (Circuits)

- Prefer **BLS12-381** and **PLONK-KZG** for new circuits (universal SRS).
- Provide:
  - Public-input ABI (field order, endianness, domain semantics).
  - VK generation script + reproducibility notes.
  - Vectors (ok/bad) and a minimal prover script.
- Ensure compliance with the **envelope** schema in `zk/README.md`.

---

## 9) FAQ

**Q: Can I submit circuits using exotic hashes?**  
A: Yes if widely reviewed and with clear security level. For field-native hashing, **Poseidon** is preferred.

**Q: Will recursion be verified on-chain?**  
A: On-chain verifies the **final** recursive proof only; recursion happens off-chain for cost and safety.

**Q: Are STARK verifiers on-chain planned?**  
A: The default path is **STARK compression to SNARK** for on-chain verification. Native STARK verification on-chain is not currently targeted.

---

## 10) Pointers

- Verifier envelope & security model: `zk/README.md`
- Fuzz harness: `tests/fuzz/fuzz_proof_envelopes.py`
- Proof fixtures: `tests/fixtures/proofs/`

*This roadmap is updated as milestones ship; changes are recorded in repository release notes.*
