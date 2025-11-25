# Proof/VK Formats → ProofEnvelope Mapping

This guide shows how common toolchain outputs (**SnarkJS**, **PlonkJS**, and a
generic **STARK/FRI** JSON) are **normalized** and packed into the canonical
`ProofEnvelope` used by Animica’s zk stack.

The loaders in `zk/adapters/*` perform all decoding/validation/normalization:
- `snarkjs_loader.py` → Groth16 (BN254)
- `plonkjs_loader.py` → PLONK+KZG (BN254)
- `stark_loader.py`   → STARK (FRI + Merkle), toy mapping

Downstream, `zk/integration/omni_hooks.py` applies **policy** (limits/allowlist),
**metering**, and calls the appropriate **verifier**.

---

## Envelope schema (canonical)

Minimal structure (keys shown in stable order):

```json
{
  "kind": "groth16_bn254 | plonk_kzg_bn254 | stark_fri_merkle",
  "proof": { "...": "toolchain-specific proof JSON" },
  "public_inputs": ["0x...", "0x..."],
  "vk_format": "snarkjs | plonkjs | fri",
  "vk": { "...": "toolchain-specific VK JSON (optional if vk_ref used)" },
  "vk_ref": "circuit_id@version (optional if vk embedded)",
  "meta": { "circuit_id": "..." }
}

Notes
	•	Provide either vk or vk_ref. In production we prefer vk_ref
pointing to a pinned entry in zk/registry/vk_cache.json.
	•	All sizes & the vk_hash use canonical JSON (sorted keys, compact separators).

⸻

Normalization rules (all toolchains)

Loaders apply the following:
	1.	Big integers / field elements
	•	Accept decimal strings, hex (0x…), or arrays (limbs).
	•	Normalize to hex big-endian in the envelope (0x-prefixed).
	•	Reduce modulo the field (BN254 Fr/Fq) where appropriate and reject out-of-range encodings.
	2.	Elliptic-curve points
	•	BN254 G1: (x, y) in Fq; G2: ((x0,x1),(y0,y1)) over Fq2.
	•	Enforce curve membership and subgroup checks.
	•	Normalize coordinates to hex strings; reject non-canonical lengths and the point-at-infinity unless allowed by the scheme.
	3.	Public inputs
	•	Envelope public_inputs is an ordered array of field elements (hex).
	•	Loaders map toolchain-specific publicSignals/pi order → this array.
	•	If integers are supplied, they are converted to canonical hex.
	4.	VK hashing
	•	vk_hash = "sha3-256:<hex>" over canonical JSON of {kind, vk_format, vk, fri_params}.
	•	The cache (zk/registry/vk_cache.json) stores this hash and optional signatures.

⸻

SnarkJS (Groth16, BN254)

Accepted shapes

Proof (any of the common shapes):
	•	Circom/SnarkJS v0.x:

{
  "pi_a": ["0x...", "0x...", "1"],
  "pi_b": [["0x...","0x..."],["0x...","0x..."],["1","0"]],
  "pi_c": ["0x...", "0x...", "1"],
  "protocol": "groth16",
  "curve": "bn128"
}


	•	Or the “flat” A/B/C representation with decimal strings; both are accepted.

VK:

{
  "vk_alpha_1": ["0x...", "0x..."],
  "vk_beta_2": [["0x...","0x..."],["0x...","0x..."]],
  "vk_gamma_2": [["0x...","0x..."],["0x...","0x..."]],
  "vk_delta_2": [["0x...","0x..."],["0x...","0x..."]],
  "vk_ic": [["0x...","0x..."], ["0x...","0x..."], "..."],
  "protocol": "groth16",
  "curve": "bn128"
}

Loader accepts decimal or hex; normalizes to hex and enforces subgroup checks.

Envelope example

{
  "kind": "groth16_bn254",
  "vk_format": "snarkjs",
  "vk_ref": "counter_groth16_bn254@1",
  "proof": {
    "pi_a": ["0x1f...", "0x2a...", "1"],
    "pi_b": [["0x..","0x.."],["0x..","0x.."],["1","0"]],
    "pi_c": ["0x3b...", "0x4c...", "1"]
  },
  "public_inputs": [
    "0x0000000000000000000000000000000000000000000000000000000000000042",
    "0x0f..."
  ],
  "meta": { "circuit_id": "counter_groth16_bn254@1" }
}


⸻

PlonkJS (PLONK + KZG, BN254)

Accepted shapes

Proof (PlonkJS JSON form—structure varies slightly by version):

{
  "A": ["0x...", "0x..."],            // commitment(s) / group elements
  "B": ["0x...", "0x..."],
  "C": ["0x...", "0x..."],
  "Z": ["0x...", "0x..."],
  "T1": ["0x...", "0x..."],
  "T2": ["0x...", "0x..."],
  "T3": ["0x...", "0x..."],
  "Wxi": ["0x...", "0x..."],          // opening proof at x
  "Wxiw": ["0x...", "0x..."],         // opening proof at x*omega
  "evals": {
    "a": "0x...", "b": "0x...", "c": "0x...", "s1": "0x...", "s2": "0x...", "z": "0x...", "t": "0x..."
  },
  "publicSignals": ["0x...", "0x..."]
}

VK (abbrev; loader extracts domain, selectors, commitments, srs hash):

{
  "nPublic": 2,
  "domainSize": 131072,
  "kzg": { "g1": "...", "g2": "..." },           // may be omitted if pinned in VK cache
  "commitments": {
    "QL": ["0x...","0x..."], "QR": ["0x...","0x..."], "QM": ["0x...","0x..."],
    "QO": ["0x...","0x..."], "QC": ["0x...","0x..."], "S1": ["0x...","0x..."],
    "S2": ["0x...","0x..."], "S3": ["0x...","0x..."]
  },
  "protocol": "plonk",
  "curve": "bn128"
}

Loader ensures all G1/G2 points are valid; binds the degree/domain and permutation
commitments; extracts number/order of public inputs.

Envelope example

{
  "kind": "plonk_kzg_bn254",
  "vk_format": "plonkjs",
  "vk_ref": "zkml_dot_geq_d384_q8_plonk_kzg_bn254@1",
  "proof": {
    "A": ["0x..","0x.."], "B": ["0x..","0x.."], "C": ["0x..","0x.."],
    "Z": ["0x..","0x.."],
    "T1": ["0x..","0x.."], "T2": ["0x..","0x.."], "T3": ["0x..","0x.."],
    "Wxi": ["0x..","0x.."], "Wxiw": ["0x..","0x.."],
    "evals": { "a":"0x..","b":"0x..","c":"0x..","s1":"0x..","s2":"0x..","z":"0x..","t":"0x.." },
    "publicSignals": ["0xH_poseidon", "0x0180", "0x007f", "0x...tau_q..."]
  },
  "public_inputs": ["0xH_poseidon", "0x0180", "0x007f", "0x...tau_q..."],
  "meta": { "circuit_id": "zkml_dot_geq_d384_q8_plonk_kzg_bn254@1" }
}


⸻

STARK / FRI (toy mapping)

The loader expects a structured JSON with:
	•	fri_params: domain size, expansion factors, number of queries, hash kind.
	•	commitments: Merkle roots for polynomial commitments (trace/constraint).
	•	queries: list of decommitments (positions + Merkle branches + leaf values).
	•	public_inputs: small set of field elements bound to the AIR (e.g., root, index).
	•	Optional vk (or vk_ref) describing AIR parameters; for the demo this can be minimal.

Proof (illustrative):

{
  "fri_params": {
    "n": 1,
    "log_n": 16,
    "num_rounds": 4,
    "expansion_factor": 8,
    "hash": "keccak"
  },
  "commitments": {
    "trace_root": "0x...",
    "constraint_root": "0x..."
  },
  "queries": [
    {
      "round": 0,
      "positions": [1234, 5678],
      "trace_leaves": ["0x...", "0x..."],
      "trace_branches": [["0x..","0x..", "..."], ["0x..","0x.."]],
      "constraint_leaves": ["0x...", "0x..."],
      "constraint_branches": [["0x..","0x.."], ["0x..","0x.."]]
    }
  ],
  "public_inputs": ["0xroot", "0xindex"]
}

VK (demo-grade):

{
  "air": "merkle_membership_v1",
  "field": "bn254_fr",
  "hash": "keccak",
  "domain_log2": 16,
  "num_queries": 30
}

Envelope example

{
  "kind": "stark_fri_merkle",
  "vk_format": "fri",
  "vk_ref": "merkle_membership_stark_demo@1",
  "proof": { "...": "see above" },
  "public_inputs": ["0xroot", "0xindex"],
  "meta": { "circuit_id": "merkle_membership_stark_demo@1" }
}


⸻

Mapping summary

Toolchain	kind	vk_format	Loader	Public inputs source
SnarkJS	groth16_bn254	snarkjs	snarkjs_loader.py	publicSignals or external
PlonkJS	plonk_kzg_bn254	plonkjs	plonkjs_loader.py	publicSignals or external
STARK/FRI	stark_fri_merkle	fri	stark_loader.py	proof.public_inputs (explicit)

When publicSignals isn’t included in the tool output, pass them separately in
the envelope public_inputs array.

⸻

Validation & errors

Loaders raise precise exceptions mapped by omni_hooks to stable error codes:
	•	BAD_ARGUMENTS: missing keys/fields, wrong types, decode failures.
	•	IMPORT_FAILURE: invalid curve points, subgroup check failed.
	•	ADAPTER_ERROR: shape mismatches (e.g., wrong evals set for vk).
	•	VERIFY_FAILED: cryptographic check failed (post-adapter).
	•	LIMIT_EXCEEDED: policy byte/inputs caps hit.
	•	NOT_ALLOWED: circuit not on allowlist.

⸻

Best practices
	•	Prefer vk_ref → VK from zk/registry/vk_cache.json (pinned by vk_hash).
	•	Keep public_inputs short and ordered, document order in your circuit docs.
	•	For BN254: always ensure subgroup checks in the loader (already enforced).
	•	Use hex everywhere in envelopes (field/coords) for determinism.
	•	Include meta.circuit_id to bind policy/allowlist even when vk is embedded.

⸻

Worked end-to-end sanity
	1.	Build envelope (envelope.json) using your tool’s proof + vk_ref.
	2.	Dry run meter-only:

python -m zk.integration.omni_hooks envelope.json --meter-only


	3.	Full verify:

python -m zk.integration.omni_hooks envelope.json


	4.	Inspect VK cache state:

python -m zk.registry.list_circuits --format table



⸻

See also
	•	zk/docs/ARCHITECTURE.md — end-to-end diagram & call sequence
	•	zk/docs/HOWTO_add_circuit.md — adding circuits & VKs
	•	zk/docs/SECURITY.md — pinning, malleability, setup caveats
	•	zk/docs/PERFORMANCE.md — expected costs & native speedups
