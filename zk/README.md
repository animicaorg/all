# Zero-Knowledge (ZK) Subsystem

This document describes the scope, security model, and proof systems supported by Animica’s verification stack. It is the canonical overview for contributors integrating or extending verifiers and for application developers who need to understand constraints around proof formats, costs, and trust assumptions.

---

## Scope & Goals

**What this covers**
- **In-protocol verification** interfaces (node & VM syscall boundary) used by contracts via `capabilities/zkverify`.
- **Proof envelope** expectations (types, fields, canonical encoding).
- **Verifier backends** and their cryptographic assumptions.
- **Determinism and performance** goals relevant to consensus/security.

**What this does _not_ cover**
- Prover circuits/toolchains (authoring R1CS/PLONKish/STARK programs, witness generation).
- Off-chain aggregation/recursion frameworks (may be added later).
- Cryptographic research; we stick to widely-deployed constructions.

**Design goals**
- **Simple, stable envelope** across systems (Groth16, PLONK-KZG, STARK).
- **Deterministic** results across platforms/CPU features.
- **DoS-resistant** verification with tight input validation.
- **Upgrade-friendly**: backends are pluggable; formats are versioned.

---

## Threat Model

**Adversary capabilities**
- Sends **malformed proofs** / keys / public inputs (type confusion, length mismatches).
- Attempts **resource exhaustion** (huge vectors, pathological domains, invalid points forcing subgroup checks).
- Tries **consensus splits** via platform-dependent behavior or non-deterministic math.
- Exploits **trusted setup** weaknesses (for SNARKs using SRS).

**Assumptions**
- Pairing and field arithmetic are implemented correctly and deterministically.
- Fiat-Shamir is modeled as a random oracle (for non-interactive proofs).
- For KZG, the **SRS is well-formed** and (if toxic waste exists) is not controlled by the adversary.

**Non-goals**
- Side-channel eradication at a micro-architectural level across all platforms (we use constant-time primitives where available, but Python/host runtimes are not SC-hardened TCBs).
- Prover security; only verification is in scope.

**Mitigations**
- Strict **size/shape limits** and **field/curve checks** before expensive work.
- Canonical **deserialization**: subgroup checks, is-in-curve, and coordinate bounds.
- Hard **gas/weight caps** per verification syscall.
- Feature-gated backends; consensus uses a single well-specified path.

---

## Supported Proof Systems

We support three verification families behind a uniform envelope:

1) **Groth16 (Pairing SNARK)**
- Curves: **BLS12-381** (primary), **BN254** (optional for legacy).
- Pros: Extremely fast verification; tiny proofs (~192 bytes on BN254, ~256–384 on BLS12-381).
- Cons: Requires **trusted setup** per circuit; circuit updates need new SRS.

2) **PLONK-KZG (PLONKish with KZG commitments)**
- Curve: **BLS12-381**; KZG commitments over G1 with G2 SRS (EIP-4844 compatible parameters when possible).
- Pros: **Universal/updatable SRS** (no per-circuit ceremony), flexible arithmetization.
- Cons: Still relies on **SRS** and pairing; proofs larger than Groth16; verification cost ~log(n) queries but dominated by a handful of pairings.

3) **STARK (FRI-based)**
- Fields: prime fields supported by the verifier profile (e.g., “Goldilocks” 2^64 − 2^32 + 1, or 64-bit-friendly primes) and standard Merkle+FRI layers.
- Pros: **Transparent** (no trusted setup), post-quantum assumptions (hash security).
- Cons: Proofs are **larger**, verification cost involves multiple Merkle openings and FRI rounds; bandwidth heavy.

> **Consensus profile:** BLS12-381 is the default pairing curve across Groth16 and PLONK-KZG to simplify key management and avoid BN254’s tighter security margins. STARKs use Keccak/SHA-256/BLAKE2/3 Merkle hashing (exact choice is part of the proof descriptor).

---

## Curves, Fields & Hashing

- **Pairing curve:** BLS12-381 (G1/G2 subgroup checks enforced).
- **Field arithmetic:** Constant-time where possible; canonical Montgomery/Weierstrass encodings.
- **Hashing:** Keccak-256 / SHA-256 / BLAKE3 selectable for transcript/Merkle in STARKs; the proof declares which is used and the verifier enforces allowed sets.
- **KZG SRS:** Supports structured power-of-tau. SRS metadata (domain size, powers) is **not** embedded in proofs; it is referenced by **SRS ID** resolved by the node from configuration.

---

## Proof Envelope

All verifications pass a **versioned envelope** (CBOR or JSON). Canonical schema:

```json
{
  "version": 1,
  "system": "groth16" | "plonk_kzg" | "stark",
  "curve": "bls12_381" | "bn254",
  "hash": "keccak256" | "sha256" | "blake3",              // STARK transcripts/Merkle
  "srs_id": "bls12-381-kzg-v1",                            // for KZG/PLONK; null for STARK
  "vk": { "format": "compressed", "bytes": "<hex>" },      // optional if vk is pre-registered
  "public_inputs": ["<hex>", "..."],                       // big-endian field elements or system-defined scalars
  "proof": { "format": "compressed", "bytes": "<hex>" },
  "hints": { "domain_bits": 20, "fri_layers": 5 }          // optional, system-specific verification hints
}

Notes
	•	vk may be omitted if a pre-registered verifying key (VK) is referenced by a contract address or registry handle; in that case pass vk_ref instead (string).
	•	public_inputs are fixed-order and must match the circuit/air binding used by the VK. The VM ABI passes them as raw bytes; conversion to field elements is part of the verifier.
	•	All hex are lowercase, even length, without 0x.

⸻

Contract & Node Interface

Contract side

Contracts call the VM syscall exposed via capabilities/zkverify:

# Pseudocode (contract)
ok = zkverify.verify(envelope_bytes)  # returns bool
if not ok:
    revert("INVALID_PROOF")

	•	The syscall charges deterministic cost based on system, proof size, and expected curve operations.
	•	The syscall does no SRS download; it only uses SRS/VK already resident or embedded in the envelope.
	•	If the envelope references unknown srs_id or mismatched curve/system, verification fails.

Node side
	•	Deserializes, validates, and dispatches to the selected backend (Groth16/PLONK-KZG/STARK).
	•	Enforces caps:
	•	Max proof size (e.g., STARK ≤ 1.5 MiB)
	•	Max public input count
	•	Max domain bits (FRI) as configured per network profile.
	•	Emits metrics: verify time, outcome, system, curve, and input sizes.

⸻

Security & Validation Checklist

Before any expensive pairing/FRI steps:
	•	Envelope
	•	version supported and not deprecated
	•	system ∈ {groth16, plonk_kzg, stark}
	•	curve consistent with system
	•	Hex fields decode; lengths sane
	•	Curve/KZG
	•	Points parse and lie in the correct subgroup
	•	srs_id resolves to a local SRS with sufficient powers and matching curve
	•	Pairing inputs are non-identity unless allowed by the scheme
	•	STARK
	•	Merkle roots and openings sizes consistent with hints
	•	Domain parameters (logN) within configured bounds
	•	Hash function is in the allowed set for the network

Consensus determinism
	•	Use fixed scalar deserialization (big-endian, mod p reduction only when specified).
	•	Avoid variable-time code paths based on platform features.

⸻

Performance Guidance

Approximate verification characteristics (order-of-magnitude; actual values depend on CPU and configuration):

System	Proof size	Verify time (ms)	Notes
Groth16	~192–384 bytes	2–10	Few pairings; tiny memory footprint
PLONK-KZG	~1–20 KiB	8–40	Pairings + MSMs; benefits from fast KZG
STARK	100 KiB–1.5 MiB	20–200+	Merkle/FRI heavy; bandwidth bound

Batching
	•	Groth16: limited gains unless common inputs allow pairing re-use.
	•	PLONK-KZG: potential MSM amortization with common VK/SRS.
	•	STARK: batching Merkle verifications helps; subject to memory caps.

⸻

Operational Concerns
	•	SRS management: srs_id is configured per network; rotations require governance and migration tooling.
	•	VK registry: (optional) on-chain registry keyed by contract/circuit ID to avoid passing large vk blobs each call.
	•	Upgrades: New verifiers or parameterizations are added behind new version or system tags; old ones can be disabled via feature flags and governance.

⸻

Testing & Fuzzing
	•	Vectors: Place known-good proofs and malicious cases under tests/fixtures/proofs/.
	•	Property tests: malformed group elements, zero/identity tweaks, domain overflows.
	•	Fuzzers: target envelope parser and deserializers (fuzz_proof_envelopes.py) to ensure robust error handling and resource limits.

⸻

Roadmap
	•	IPA/EC-based commitments (KZG-free PLONKish verification).
	•	Recursive proofs (SNARK-verifies-SNARK), with cost modeling for on-chain feasibility.
	•	Aggregation APIs (multi-proof aggregation where soundness is preserved).
	•	Compressed STARKs (PCD or folding-based plumbing when mature).

⸻

Appendix: Minimal Examples

Groth16 (BLS12-381) envelope (JSON)

{
  "version": 1,
  "system": "groth16",
  "curve": "bls12_381",
  "public_inputs": ["12ab...00", "00ff...39"],
  "proof": { "format": "compressed", "bytes": "a1b2...ff" },
  "vk_ref": "registry:zk/groth16/my-circuit@v3"
}

PLONK-KZG (BLS12-381) envelope (CBOR)

Fields mirror JSON; srs_id is required unless vk embeds the commitment scheme metadata.

STARK envelope (JSON)

{
  "version": 1,
  "system": "stark",
  "hash": "keccak256",
  "public_inputs": ["00..."],
  "proof": { "format": "compressed", "bytes": "f0e1...aa" },
  "hints": { "domain_bits": 20, "fri_layers": 5 }
}


⸻

FAQs

Q: Can I mix BN254 and BLS12-381 on the same network?
A: Technically yes, but we recommend standardizing on BLS12-381 for new deployments. Mixing curves increases complexity and attack surface.

Q: Do you support EIP-4844 KZG directly?
A: The KZG verifier is compatible with BLS12-381 KZG commitments; SRS management and exact serialization (e.g., G1/G2 encodings) follow the network’s srs_id profile.

Q: Are proofs cached?
A: No. Verification is stateless. Caching can be built in higher-level services with replay protection.

Q: How large can STARK proofs be?
A: Configurable per network; defaults target ≤ 1.5 MiB per call with strict gas/weight accounting.

⸻

For implementation details and integration hooks, see:
	•	contracts/stdlib/capabilities/zkverify.py (contract syscall helper)
	•	tests/fuzz/fuzz_proof_envelopes.py (envelope parser fuzzing)
	•	tests/fixtures/proofs/* (sample OK/invalid proofs)

