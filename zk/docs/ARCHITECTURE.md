# Animica zk/ Architecture

This document explains how the **zk** subsystem fits together: from a user’s
`ProofEnvelope` to verifier dispatch, policy enforcement, metering, and results.
It’s written to be actionable for implementers and reviewers (security, ops).

---

## Bird’s-eye view

```mermaid
flowchart LR
    A[Caller / Contract Host] -->|payload.envelope| B[omni_hooks.zk_verify]
    B -->|size+allowlist| C[policy.check_and_meter]
    C -->|OK + units| D[verify_envelope]
    D --> E[registry.resolve(kind)]
    E --> F{{verifier func}}
    F -->|True/False| G[zk_verify result]

    subgraph zk.integration
      D
      C
    end

    subgraph zk.registry
      E
    end

    subgraph Verifiers
      F1[groth16_bn254.verify]
      F2[plonk_kzg_bn254.verify]
      F3[stark_fri_merkle.verify]
    end

Key properties
	•	Deterministic metering happens before cryptographic checks.
	•	Dispatcher chooses a verifier by envelope.kind.
	•	VK material may come embedded (envelope.vk) or from a cache reference (envelope.vk_ref).
	•	Policy guards resources (sizes, public inputs) and an allowlist of circuit IDs.

⸻

Call sequence

sequenceDiagram
    autonumber
    participant Caller
    participant Hook as omni_hooks.zk_verify
    participant Policy as integration.policy
    participant Bridge as integration.verify_envelope
    participant Registry as registry.resolve
    participant Verifier as {kind}.verify

    Caller->>Hook: payload = { envelope, meter_only? }
    Hook->>Policy: check_and_meter(envelope)
    Policy-->>Hook: units (gas-equivalent)

    alt meter_only = true
      Hook-->>Caller: { ok: true, units, kind, circuit_id }
    else
      Hook->>Bridge: verify(envelope)
      Bridge->>Registry: resolve(kind)
      Registry-->>Bridge: callable verify(...)
      Bridge->>Verifier: verify(proof, vk, public_inputs)
      Verifier-->>Bridge: bool
      Hook-->>Caller: { ok: bool, units, ... }
    end


⸻

Data contracts (canonical)

ProofEnvelope

{
  "kind": "groth16_bn254",
  "proof": { "...": "snarkjs-compatible" },
  "public_inputs": ["0x1234", "0xdeadbeef"],
  "vk": { "...": "optional embedded verifying key" },
  "vk_format": "snarkjs",
  "vk_ref": "counter_groth16_bn254@1",
  "meta": { "circuit_id": "counter_groth16_bn254@1" }
}

VkRecord (entry in zk/registry/vk_cache.json)

{
  "kind": "groth16_bn254",
  "vk_format": "snarkjs",
  "vk": { "...": "toolchain-specific" },
  "fri_params": null,
  "vk_hash": "sha3-256:abcd1234...",
  "meta": { "circuit": "counter", "version": "1" },
  "sig": { "alg": "ed25519", "key_id": "ops@2025-06", "signature": "..." }
}

Hashing of VKs is canonical JSON over {"kind","vk_format","vk","fri_params"}.
See zk/integration/types.py::compute_vk_hash and registry tools for bit-for-bit parity.

⸻

Modules and responsibilities

graph TD
  subgraph "zk/integration"
    P1[types.py<br/>ProofEnvelope, VkRecord]:::code
    P2[policy.py<br/>allowlist, limits, metering]:::code
    P3[integration.__init__<br/>verify(envelope|kind=...)]:::code
    P4[omni_hooks.py<br/>stable plugin API]:::code
  end

  subgraph "zk/registry"
    R1[__init__.py<br/>register/resolve]:::code
    R2[vk_cache.json]:::data
    R3[registry.yaml]:::data
    R4[update_vk.py / list_circuits.py]:::tool
  end

  subgraph "zk/verifiers"
    V1[groth16_bn254.py]:::code
    V2[plonk_kzg_bn254.py]:::code
    V3[stark_fri.py]:::code
    V4[merkle.py / poseidon.py / transcript_fs.py / pairing_bn254.py / kzg_bn254.py]:::code
  end

  P4 --> P2
  P4 --> P3
  P3 --> R1
  R1 --> V1 & V2 & V3
  R1 --> R2
  R1 --> R3

  classDef code fill:#1f2937,stroke:#0ea5e9,color:#e5e7eb,stroke-width:1px
  classDef data fill:#0f766e,stroke:#22d3ee,color:#ecfeff
  classDef tool fill:#78350f,stroke:#f59e0b,color:#fff7ed


⸻

Policy and metering

Resource limits are enforced per verifier kind:

kind	max_proof_bytes	max_vk_bytes	max_public_inputs	base	per_pi	per_proof_byte	per_vk_byte	per_opening
groth16_bn254	128k	256k	64	250k	12k	2	0	—
plonk_kzg_bn254	256k	1MiB	128	420k	14k	2	0	95k
stark_fri_merkle	512k	256k	16	300k	2k	1	0	—

Cost formula

units = base
      + per_public_input * num_public_inputs
      + per_proof_byte   * proof_bytes
      + per_vk_byte      * vk_bytes
      + per_opening      * kzg_openings   # PLONK/KZG only (default=1)

Implementation: zk/integration/policy.py::estimate_units and check_and_meter.

Allowlist

Only circuit IDs present in the policy allowlist (or "*") are accepted.
The circuit id is taken from envelope.meta.circuit_id or, if absent, vk_ref.

Violations raise:
	•	NotAllowedCircuit
	•	LimitExceeded

⸻

Verifier compatibility

Kind	Curve/Field	Commitment	Transcript	VK format	Loader
groth16_bn254	BN254 (Fq, Fr)	—	Fiat–Shamir	snarkjs	adapters/snarkjs_loader.py
plonk_kzg_bn254	BN254 (Fr)	KZG	Fiat–Shamir	plonkjs	adapters/plonkjs_loader.py
stark_fri_merkle	Prime field	Merkle+FRI	Fiat–Shamir	fri_params	adapters/stark_loader.py

Internals (pairing, KZG, Poseidon, Merkle, transcript) live under zk/verifiers/.

⸻

Error model (stable for plugin consumers)

omni_hooks.zk_verify(..) always returns a dictionary:

{
  "ok": false,
  "units": 0,
  "kind": "groth16_bn254",
  "circuit_id": "counter_groth16_bn254@1",
  "error": { "code": "LIMIT_EXCEEDED", "message": "proof too large ..." },
  "meta": { "proof_bytes": 1234, "vk_bytes": 5678, "num_public_inputs": 2 }
}

Error codes:
	•	BAD_ARGUMENTS, NOT_ALLOWED, LIMIT_EXCEEDED,
	•	REGISTRY_ERROR, IMPORT_FAILURE, ADAPTER_ERROR,
	•	VERIFY_FAILED, UNKNOWN.

⸻

Security notes
	•	VK integrity: vk_cache.json entries include vk_hash and optional signatures.
Updater (zk/registry/update_vk.py) verifies and writes hashes. Runtime verification
trusts that cache or embedded VK.
	•	Domain separation: transcript helpers, hash functions (Poseidon/Keccak/SHA3),
and KZG/BN254 primitives are namespaced; see zk/verifiers/*.
	•	Determinism: JSON canonicalization (sorted keys, compact separators) is used
for size accounting and hashing to avoid environment drift.
	•	Limits: strict byte and input limits mitigate DoS via oversized payloads.

⸻

Extending with a new circuit
	1.	Implement zk/verifiers/<your_kind>.py exposing verify(**kwargs) -> bool.
	2.	Add a loader in zk/adapters if your toolchain has a specific proof/VK schema.
	3.	Register the kind in zk/registry/__init__.py (or at init-time in your module).
	4.	Add VK material to zk/registry/vk_cache.json via update_vk.py.
	5.	Append your circuit id to the policy allowlist and set limits/gas.
	6.	Document it in zk/registry/registry.yaml and re-run list_circuits.py.

Minimal verifier signature

def verify(*, proof: dict, vk: dict | None, public_inputs: list[str] | None, **_kwargs) -> bool:
    ...


⸻

Worked example (Groth16)
	1.	Wallet/API assembles ProofEnvelope (SnarkJS proof + VK or vk_ref).
	2.	omni_hooks.zk_verify:
	•	computes sizes, enforces policy, returns units.
	•	resolves groth16_bn254 verifier via registry.
	3.	Verifier:
	•	decodes points via loader, checks pairing product equation.
	4.	Result returned with the same units.

⸻

CLI / tooling
	•	List registered circuits and VK health:

python -m zk.registry.list_circuits --format table


	•	Show policy:

python -m zk.integration.policy --format json


	•	Dry-run a payload:

python -m zk.integration.omni_hooks payload.json --policy policy.json



⸻

Testing checklist
	•	Positive/negative vectors per verifier kind (valid/invalid proofs).
	•	Size limit boundaries (max-1, max, max+1).
	•	Allowlist enforcement (present/absent circuit IDs).
	•	Meter-only path returns units without crypto checks.
	•	VK hash stability across Python versions (JSON canonicalization).
	•	Registry resolution errors mapped to plugin error codes.

⸻

Appendix A — Canonical JSON

All sizes and hashes are computed over:

json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

This is defined centrally in zk/integration/types.py::canonical_json_bytes.

