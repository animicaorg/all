# ZKML: Embedding-Threshold Circuit & Extensions

This document describes a family of **ZKML gating circuits** that let a caller
prove properties about a private embedding vector (e.g., from an LLM or image
encoder) **without revealing the vector**. The canonical circuit proves a
**dot-product / cosine-similarity threshold**, enabling privacy-preserving
routing, safety checks, and access control.

The circuits integrate with the Animica zk stack via `ProofEnvelope` and are
compatible with Groth16/PLONK (BN254) verifiers already present in `zk/`.

---

## Use cases

- **Safety & policy routing**: prove “this embedding is close to a blocked class”
  or “above threshold for safe topic” without exposing the text/image.
- **Private search / retrieval**: prove “query embedding is within τ of index
  centroid Cᵢ” to unlock gated content tiers.
- **Rate-tier gating / eligibility**: prove membership in a semantic cohort.

---

## Bird’s-eye flow

```mermaid
flowchart LR
  A[Client] -- compute embedding x --> B[Quantize + Commit]
  B -- H = Poseidon(salt||x_q) --> C[Proof: ⟨x_q, c_q⟩ ≥ τ_q]
  C -- ProofEnvelope{kind,circuit_id,H,τ_q,c_ref} --> D[omni_hooks.zk_verify]
  D --> E[policy.check_and_meter]
  E --> F[registry → zk/verifiers/*]
  F -->|True/False| G[Result to Client/Contract]


⸻

Core circuit: dot-product threshold

We target a fixed embedding dimension d ∈ {128, 256, 384, 512, 768}.

Let:
	•	x_q ∈ Z^d be the quantized private embedding (witness).
	•	c_q ∈ Z^d be the reference centroid / weight (usually public, bound to VK).
	•	τ_q ∈ Z be the integer threshold (public).
	•	H = Poseidon(salt || x_q) be a binding commitment to x_q (public).
	•	Optional range bounds on x_q (e.g., int8/int16) enforced inside the circuit.

The circuit checks:
	1.	Commitment: recompute H' = Poseidon(salt || x_q) and enforce H' == H.
	2.	Range: x_q[i] ∈ [−B, B] (B=127 for int8, 32767 for int16).
	3.	Dot: s = Σᵢ x_q[i] * c_q[i].
	4.	Threshold: s ≥ τ_q.

Cosine similarity

If embeddings are pre-normalized (‖x‖≈1 and ‖c‖≈1), a dot-product threshold
corresponds to a cosine threshold. For fixed-point, define:
	•	Real vectors x, c ∈ ℝ^d with ‖x‖≈1, ‖c‖≈1.
	•	Quantization scale S (e.g., S=127 for int8).
	•	x_q = round(S * x), c_q = round(S * c).
	•	A desired cosine threshold τ ∈ [−1,1] maps to integer τ_q ≈ d * S² * τ
(adjust for rounding if you also store per-vector scales; see below).

For deployments that do not pre-normalize, include per-vector scales
sx, sc in public inputs and prove ⟨x_q, c_q⟩ ≥ τ * sx * sc. This slightly
increases constraints but keeps witness private.

⸻

Quantization & fixed-point

Recommended configurations:
	•	Int8 (Q0.7): x_q, c_q ∈ [−127,127]; compact, fast constraints.
	•	Int16 (Q0.15): tighter approximation; larger proof.

Options for scale handling:
	1.	Global S: fixed compile-time constant; simplest (preferred).
	2.	Per-vector Sx, Sc: pass scales as public inputs; circuit enforces
s ≥ τ_q = round(τ * Sx * Sc).
This avoids divisions in-circuit and keeps constraints linear.

⸻

Public I/O & witness (canonical)

Name	Visibility	Type	Notes
H	public	field	Poseidon(salt||x_q)
tau_q	public	int	integer threshold
c_ref	public	hash/ref	bound to VK (either embedded or vk_ref)
dim	public	int	e.g., 384, 768
S/Sx,Sc	public	int(s)	optional scales
x_q	witness	int[d]	private embedding (quantized)
salt	witness	bytes	randomizer to prevent dictionary attacks

In PLONK/Groth16 JSON, public inputs are serialized as hex field elements.
Adapters normalize int/hex to Fr before verification.

⸻

Circuit IDs & VK pinning

We version circuits with dimension and quantization:

zkml_dot_geq_d384_q8_groth16_bn254@1
zkml_dot_geq_d768_q8_plonk_kzg_bn254@1
zkml_dot_geq_d384_q16_plonk_kzg_bn254@1

	•	kind is groth16_bn254 or plonk_kzg_bn254.
	•	The VK includes (or references) the fixed c_q (or a Poseidon commitment to it),
the dimension d, and scale config, so the verifier binds to the intended model.
	•	Add VK via zk/registry/update_vk.py and allowlist the circuit_id in policy.

⸻

Envelope example

{
  "envelope": {
    "kind": "plonk_kzg_bn254",
    "vk_ref": "zkml_dot_geq_d384_q8_plonk_kzg_bn254@1",
    "vk_format": "plonkjs",
    "proof": { "...": "toolchain-specific PLONK proof JSON" },
    "public_inputs": [
      "0xH_poseidon",      // H
      "0x0000000000000180",// dim = 384
      "0x000000000000007f",// S = 127 (if used)
      "0x...tau_q..."      // threshold
    ],
    "meta": { "circuit_id": "zkml_dot_geq_d384_q8_plonk_kzg_bn254@1" }
  }
}

If c_q is not fully public, VK can carry a commitment to c_q and the
circuit recomputes & binds to it internally.

⸻

Policy & metering
	•	Add your circuit IDs to the allowlist.
	•	Suggested per-kind limits (ballparks; tune for your deployment):
	•	proof_bytes ≤ 128–192 KiB, vk_bytes ≤ 512 KiB, public_inputs ≤ 8.
	•	Metering is linear in proof/VK size and public inputs; PLONK adds a per-opening cost.

See zk/docs/PERFORMANCE.md for expected runtime and zk/integration/policy.py for
deterministic unit computation.

⸻

Security notes
	•	Malleability: all sizes and VK hashes use canonical JSON; public inputs are
normalized to field elements; loader enforces ranges.
	•	Dictionary attacks: include 128-bit salt in the commitment H; never
reuse salts across different embeddings if the preimage domain is small.
	•	VK pinning: the exact centroid(s), dimension, and scales must be bound in the
VK (or its commitment) and pinned via vk_hash in the registry.
	•	Curve: BN254 is adequate for on-chain verification; upgrade path to BLS12-381
should use a distinct kind and re-registered VKs.

⸻

Extensions

1) Multi-centroid OR-threshold

Prove ∨_{j=1..k} ⟨x_q, c_q^(j)⟩ ≥ τ_j for small k (e.g., k ≤ 4), using
Boolean selectors and range-checked dot-products; publish the selected j or keep
it hidden with a constraint that at least one holds. Circuit IDs encode k.

2) Top-1 class (argmax)

Prove that a specific class (j*) has the max score among a small set:
⟨x_q, c_q^(j*)⟩ ≥ ⟨x_q, c_q^(j)⟩ + δ for all j ≠ j* with a margin δ.
Useful for private moderation tags.

3) Hamming / binary embeddings

If the model outputs binary embeddings, reduce to Hamming distance threshold
by counting equal bits. This can be extremely small in constraints.

4) Private centroid with public binding

Keep c_q private but prove it matches a public commitment Hc; good for
rotating centroids without on-chain updates. VK pins Hc, not the raw centroid.

5) Range proofs for per-dimension scales

If using per-dimension scales or clipping, add range proofs and saturating-mul
gadgets; beware of constraint blow-up.

⸻

Implementation tips
	•	Gadgets: use plookup or bit-decomposition for comparisons (s ≥ τ_q).
	•	Poseidon: use the same parameters as in zk/verifiers/poseidon.py to ensure
cross-toolchain consistency.
	•	Public IO order: fix and document the order in the VK to avoid ambiguity.
	•	Adapters: extend plonkjs_loader.py or snarkjs_loader.py to map your
public input schema to canonical form (hex → Fr, integers → limb-packed).

⸻

Testing
	•	Build small, reproducible vectors with known results:
	•	Random x_q, known c_q, compute s, set τ_q = s (boundary), τ_q = s±1.
	•	Negative tests:
	•	Tamper one byte of proof.
	•	Change H to mismatched commitment.
	•	Push a coordinate outside allowed range.
	•	VK hash stability across Python versions (canonicalization).

⸻

Versioning
	•	Bump circuit version on any of:
	•	Dimension or scale changes.
	•	Centroid update (if not using committed Hc indirection).
	•	Poseidon parameters or public IO order changes.

Track metadata in zk/registry/registry.yaml and update pinned VKs in
zk/registry/vk_cache.json.

⸻

FAQ

Q: Why not prove exact cosine with division?
A: Divisions increase constraints and require careful rounding. Using pre-normalized
vectors or public scales avoids division while keeping semantics equivalent.

Q: Can I keep both x and c private?
A: Yes—commit to both in public inputs and prove the dot-threshold holds. This is
heavier and complicates policy; pin the commitments in the VK to prevent abuse.

Q: How big can d be?
A: Practical on BN254 up to ~768 with careful gadgetry and PLONK; test your curves.
Consider multi-proof aggregation if you need to amortize many gates.

⸻

References (internal)
	•	Verifier stack: zk/verifiers/*, adapters in zk/adapters/*.
	•	Policy/metering: zk/integration/policy.py, zk/docs/PERFORMANCE.md.
	•	Security: zk/docs/SECURITY.md.
	•	Integration path: zk/docs/ARCHITECTURE.md, zk/docs/HOWTO_add_circuit.md.

