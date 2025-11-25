# zk/ SECURITY.md

Security notes for the Animica **zk/** subsystem: trusted setup caveats, VK pinning,
malleability concerns, and operational guidance. This document is for engineers,
auditors, and operators integrating/verifying zero-knowledge proofs via
`zk/integration/*`, `zk/registry/*`, and `zk/verifiers/*`.

---

## Scope & model

- **Verify-only**: Code under `zk/verifiers/*` performs *verification only* (no proving).
- **Deterministic**: All checks are pure functions without I/O or non-determinism.
- **Defense-in-depth**: Policy gating (sizes/allowlist), strict decoding/normalization,
  and registry VK pinning protect higher layers from DoS and malleability risks.

---

## Trusted setup caveats (Groth16 / PLONK+KZG)

### KZG / SRS (“toxic waste”)
- PLONK+KZG (and Groth16) rely on a **Structured Reference String** (SRS).
- If the SRS secret is leaked or the ceremony was dishonest, **forgeries are possible**.
- **Mitigations:**
  - Pin the exact SRS/VK and its **hash** via `vk_hash` in `zk/registry/vk_cache.json`.
  - Track provenance of the SRS (public ceremony transcript, multi-party ceremony).
  - Treat SRS changes as **breaking changes**: bump circuit IDs and re-allowlist.

### Groth16
- Requires a per-circuit trusted setup.
- VKs must be **versioned and pinned**; never accept arbitrary or user-supplied VKs
  without going through the registry (see “VK pinning”).

---

## Verifying Key (VK) pinning & integrity

- Every VK entry in `zk/registry/vk_cache.json` includes:
  - `vk_hash = "sha3-256:<hex>"` over canonical JSON of `{kind,vk_format,vk,fri_params}`.
  - Optional `sig` (e.g., Ed25519) binding `(circuit_id, kind, vk_format, vk_hash)`.
- **Do not** trust VKs embedded in envelopes unless:
  - Their `compute_vk_hash` equals a whitelisted entry, or
  - Policy explicitly allows ad-hoc VKs (dev-only).
- Operators update VKs via `zk/registry/update_vk.py` which:
  - Normalizes VKs, computes hashes, and (optionally) verifies signatures.
- **Policy allowlist** (see `zk/integration/policy.py`) must include the **circuit_id**
  (usually identical to `vk_ref`) or `"*"` for dev/testing.

---

## Malleability & canonicalization

### JSON canonicalization
- All size accounting and VK hashing uses **canonical JSON**:
  `sort_keys=True`, compact separators, UTF-8 bytes.
- Prevents attacker-controlled whitespace/key ordering affecting size/gas or hashes.

### Field & point normalization
- Loaders (`zk/adapters/*`) must:
  - Reduce integers **mod p** for field elements; reject out-of-range encodings.
  - Enforce **subgroup checks** and curve membership for BN254 points (G1/G2).
  - Reject non-canonical encodings (leading zeros, wrong length, NaNs in strings).
- Public inputs must be normalized to hex/integers and validated against field size
  where required by the verifier.

### Transcript domain separation
- Fiat–Shamir transcripts (`zk/verifiers/transcript_fs.py`) include:
  - **Protocol name**, **circuit id**, **kind**, **round tags**, and **challenge labels**.
- Avoids cross-protocol transcript collisions and replays.

---

## Curve & commitment caveats

### BN254 specifics
- Security ~100–110 bits; acceptable for many on-chain verifiers, but weaker than
  BLS12-381. If long-term (~20y) confidentiality/soundness is needed, prefer
  stronger curves in future circuits.
- **Always enforce subgroup checks** on G1/G2; cofactor clearing or explicit checks
  are performed in the loaders/verifiers.
- Pairing equation checks must reject identity/zero points.

### KZG details
- Our minimal KZG verifier binds:
  - Commitment `C = [p(X)]_1`
  - Opening claim `(x, y)` and proof `π`
  - Fiat–Shamir challenge where applicable (e.g., batch aggregation)
- Risks to avoid:
  - **Degree bound**: Verifier assumes circuit-specific degree/selector structure
    enforced by VK; do not accept unbounded degree claims.
  - **Malleable transcript**: Challenges must be derived *inside* the verifier from
    domain-separated transcripts (never from user input).

### Poseidon parameters
- Poseidon constants/rate/capacity are **pinned** for each circuit family and stored
  alongside VK metadata; do not mix parameter sets across circuits.

### STARK/FRI toy verifier
- The `stark_fri.py` path is **demonstration-grade** (e.g., Merkle membership AIR).
- Use only for examples/devnet; production circuits must supply hardened AIR/VK and
  hash functions (Keccak or domain-separated Poseidon) with adequate security margins.

---

## DoS resistance & metering

- **Policy limits** (`max_proof_bytes`, `max_vk_bytes`, `max_public_inputs`) reject
  oversized payloads before crypto.
- **Deterministic units** (aka “gas”) are computed *before* verification; callers can
  price/charge even if the proof later fails.
- Loaders should short-circuit on structure errors **early**.

---

## Side channels & constant time

- Python implementations are **not constant time**; however, verifiers run on public
  data with no secrets in the verifier runtime. For defense in depth:
  - Avoid timing/logging that reveals index positions in Merkle/FRI unless necessary.
  - Redact or hash large proof blobs in logs; never log secrets.

---

## Supply chain & versioning

- Pin cryptographic libraries (e.g., `py_ecc`) and hash internal constants (VK/SRS).
- Treat changes to:
  - VK content
  - Poseidon/KZG parameters
  - Transcript labels
  as **breaking**; bump circuit IDs (`@2`, `@3`, …) and maintain allowlist entries for
  co-existence during migration.

---

## Error handling & result surface

- `omni_hooks.zk_verify` always returns a stable dict with:
  - `ok`, `units`, `kind`, `circuit_id`, `error{code,message}`, `meta{sizes}`.
- Sensitive internals (raw points/field elements) are not leaked in error messages.
- Error codes are bounded: `BAD_ARGUMENTS`, `NOT_ALLOWED`, `LIMIT_EXCEEDED`,
  `REGISTRY_ERROR`, `IMPORT_FAILURE`, `ADAPTER_ERROR`, `VERIFY_FAILED`, `UNKNOWN`.

---

## Operational guidance

1. **Pin VKs** in `vk_cache.json` and sign entries (SigRecord) from an ops key.
2. **Restrict policy allowlist** to known circuit IDs in production.
3. **Review meters** after perf testing; adjust `base`/`per_byte`/`per_input` conservatively.
4. **Rotate keys**: when signer keys change, re-sign VK entries and commit together.
5. **Audit adapters** whenever upgrading external toolchains (SnarkJS/PlonkJS/FRI).
6. **CI checks**:
   - Verify `vk_hash` stability (`compute_vk_hash`) and signature validity.
   - Negative test vectors (tampered proof/VK/public inputs).
   - Size limit boundaries and meter-only path.

---

## Checklist (engineering)

- [ ] Subgroup checks enabled for all elliptic-curve points.
- [ ] Field elements reduced mod p and validated (no non-canonical encodings).
- [ ] Transcript domain separation covers protocol/circuit/kind/round labels.
- [ ] Canonical JSON used for size accounting and VK hashing.
- [ ] Policy allowlist contains the intended circuit IDs only.
- [ ] VK pinned with `vk_hash` and (optionally) signed.
- [ ] KZG degree bounds and challenge derivations enforced by VK/transcript.
- [ ] Logs do not leak raw proof material; errors are succinct.
- [ ] Test suite includes invalid/malleated proofs and boundary sizes.

---

## Appendix: Why canonical JSON?

- Prevents attackers from inflating/deflating meterable sizes via whitespace or
  key reordering.
- Ensures **VK hash** is stable across runtimes and Python versions.
- See `zk/integration/types.py::canonical_json_bytes` for the exact routine.

