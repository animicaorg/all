# HOWTO: Add a New zk Circuit & Verifying Key (VK)

This guide walks you through wiring a brand-new circuit into the Animica **zk/** stack:
verifier code → adapter/loader → VK cache → policy allowlist/metering → registry metadata → end-to-end check.

> TL;DR checklist is at the bottom.

---

## 0) Pick stable identifiers

- **Verifier kind** (implementation family):
  - Examples: `groth16_bn254`, `plonk_kzg_bn254`, `stark_fri_merkle`.
  - Must match a target in `zk/registry/__init__.py`.

- **Circuit ID** (unique, versioned):
  - Format: `<slug>_<kind>@<version>`
  - Examples: `counter_groth16_bn254@1`, `poseidon2_arity3_plonk_kzg_bn254@1`.

---

## 1) Implement the verifier

Create a module under `zk/verifiers/your_kind.py` that exposes **`verify(...) -> bool`**.

Minimal signature (keep kwargs names to align with adapters/bridge):

```python
def verify(
    *,
    proof: dict,
    vk: dict | None,
    public_inputs: list[str] | list[int] | None,
    **_kwargs
) -> bool:
    ...

Tips:
	•	Reuse primitives in zk/verifiers/ (e.g., pairing_bn254, kzg_bn254, poseidon, transcript_fs, merkle).
	•	Keep pure and deterministic: no I/O, no randomness, no clock.

⸻

2) (If needed) Add a loader/normalizer

If your toolchain emits a specific JSON shape, write a loader under zk/adapters/.

Examples already present:
	•	snarkjs_loader.py (Groth16),
	•	plonkjs_loader.py (PLONK+KZG),
	•	stark_loader.py (FRI/STARK).

Your loader should:
	•	Validate required keys/types.
	•	Normalize field elements and points to canonical internal forms.
	•	Raise precise exceptions on bad input (mapped by omni_hooks).

⸻

3) Register the verifier kind

Open zk/registry/__init__.py and add/ensure a mapping from kind → callable.

Example:

register(
    kind="your_kind",
    module_path="zk.verifiers.your_kind",
    func_name="verify",
    scheme="your-scheme",
    curve="bn254",   # or "prime", etc.
    hash_fn="poseidon2",  # if relevant
)

You can also self-register in your module at import time, but central registry is preferred.

⸻

4) Produce a VK record and add to the cache

4.1 Prepare VK material
	•	If your verifier needs a VK, collect it as a JSON file exactly as your loader expects.
	•	For STARK toy verifiers, vk may be minimal or fri_params-only.

4.2 Add/replace via the helper

Use zk/registry/update_vk.py to insert a normalized VkRecord and compute a canonical hash.

# Add (or replace) a VK
python -m zk.registry.update_vk add \
  --circuit-id myfeature_your_kind@1 \
  --kind your_kind \
  --vk-format snarkjs \
  --vk path/to/vk.json \
  --cache zk/registry/vk_cache.json \
  --sign ed25519:ops_keys/ed25519.sk --key-id "ops-2025q4"

Validate signatures and hashes:

python -m zk.registry.update_vk verify \
  --cache zk/registry/vk_cache.json

The tool computes vk_hash = "sha3-256:<hex>" using the same canonical JSON
routine as zk/integration/types.compute_vk_hash.

⸻

5) Update policy (allowlist, size limits, metering)

5.1 Allowlist your circuit

Edit zk/integration/policy.py or provide a policy override file used in ops.
	•	Add your circuit ID to the allowlist.
	•	Set (or reuse) limits for your kind if not present.

Example JSON override (optional file policy.json):

{
  "allowlist": ["myfeature_your_kind@1", "counter_groth16_bn254@1"],
  "limits": {
    "your_kind": { "max_proof_bytes": 262144, "max_vk_bytes": 1048576, "max_public_inputs": 64 }
  },
  "gas": {
    "your_kind": { "base": 450000, "per_public_input": 12000, "per_proof_byte": 2, "per_vk_byte": 0 }
  }
}

5.2 Sanity-check metering

The deterministic cost formula is implemented in check_and_meter(...). Use it via the plugin:

python -m zk.integration.omni_hooks payload.json --policy policy.json

Where payload.json contains an envelope (see §7).

⸻

6) Add registry metadata (human-readable)

Edit zk/registry/registry.yaml and describe your circuit:

circuits:
  myfeature_your_kind@1:
    title: MyFeature Circuit
    kind: your_kind
    version: 1
    description: >
      One-liner about what this circuit proves.
    public_inputs:
      - "x"
      - "y"
    vk_ref: myfeature_your_kind@1
    links:
      repo: https://example.com/...
      docs: https://example.com/...

List to verify it renders:

python -m zk.registry.list_circuits --format table


⸻

7) Build a ProofEnvelope and test end-to-end

Create payload.json:

{
  "envelope": {
    "kind": "your_kind",
    "proof": { "...": "toolchain-specific proof JSON" },
    "public_inputs": ["0x01", "0x02"],
    "vk_ref": "myfeature_your_kind@1",
    "vk_format": "snarkjs",
    "meta": { "circuit_id": "myfeature_your_kind@1" }
  },
  "meter_only": false
}

Run the plugin:

python -m zk.integration.omni_hooks payload.json

Expected shape:

{
  "ok": true,
  "units": 473112,
  "kind": "your_kind",
  "circuit_id": "myfeature_your_kind@1",
  "error": null,
  "meta": { "proof_bytes": 12345, "vk_bytes": 67890, "num_public_inputs": 2 }
}


⸻

8) Tests & vectors (recommended)
	•	Positive/negative proof vectors (tamper a byte to ensure failure).
	•	Size limit boundaries (max-1, max, max+1).
	•	Public input normalization (ints → hex, bytes → hex).
	•	VK hash stability (regenerate on different machines/Python versions).
	•	Meter-only path returns units even if verification would fail (no crypto run).

⸻

9) Versioning & rollout
	•	Bump circuit_id version (@2) when the circuit logic or public IO changes.
	•	Keep old VK entries until consumers migrate; allowlist can include both versions.
	•	Re-sign VK cache entries if signer keys rotate.

⸻

10) Common pitfalls
	•	Mismatched VK/proof shapes → ensure your adapter normalizes and validates.
	•	Oversized payloads rejected by LimitExceeded → adjust policy after benchmarking.
	•	Missing VK → provide either vk or vk_ref in the envelope.
	•	Not allowlisted → policy must include the circuit ID (or "*" for dev).

⸻

Reference snippets

ProofEnvelope (Python)

from zk.integration.types import ProofEnvelope
env = ProofEnvelope(
    kind="your_kind",
    proof=proof_json,
    public_inputs=["1a2b", "deadbeef"],
    vk_ref="myfeature_your_kind@1",
    vk_format="snarkjs",
    meta={"circuit_id": "myfeature_your_kind@1"},
)

Meter & verify (Python)

from zk.integration.policy import check_and_meter, DEFAULT_POLICY
from zk.integration import verify

units = check_and_meter(env, policy=DEFAULT_POLICY)
ok = verify(envelope=env.__dict__)  # or asdict(msgspec struct)


⸻

TL;DR Checklist
	•	Create zk/verifiers/your_kind.py with verify(...) -> bool.
	•	Add/confirm loader in zk/adapters/ (or reuse existing).
	•	Register kind in zk/registry/__init__.py.
	•	Insert VK via zk/registry/update_vk.py add ... (hash/sign).
	•	Update allowlist/limits/gas in zk/integration/policy.py (or override file).
	•	Describe circuit in zk/registry/registry.yaml.
	•	Build payload.json envelope; run zk.integration.omni_hooks.
	•	Add tests & vectors; verify failure modes and metering.
	•	If updating, bump circuit ID version (@2) and keep old until migrated.

Happy proving! 🚀
