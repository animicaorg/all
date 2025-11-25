# Reproducibility Guide

This note documents how to reproduce **exact verification results** across
machines and time. It covers **hashing inputs**, **lockfiles**, and **build
notes** for the zk subsystem.

Goals:
- Same inputs → same **decision** (`ok=True/False`) and same **meter units**.
- Verifier/VK/Envelope bytes are **canonically hashed** and **pinned**.
- Toolchains and environment are **version-locked** with exported manifests.

---

## 1) Canonical bytes & hashing

Our pipeline relies on **canonical JSON** (sorted keys, compact separators,
UTF-8) before hashing, sizing, or caching. The helpers live in
`zk/integration/types.py`.

### 1.1 Canonical JSON bytes

- Routine: `canonical_json_bytes(obj)`  
- Properties:
  - Deterministic key order
  - No extraneous whitespace
  - Ensures stable `len(bytes)` and `sha3-256(bytes)`

### 1.2 VK hash (`vk_hash`)

- Routine: `compute_vk_hash(kind, vk_format, vk, fri_params=None)`  
- Definition: `vk_hash = "sha3-256:<hex>"` over **canonical JSON** of
  `{ "kind", "vk_format", "vk", "fri_params" }`.

This value is stored in `zk/registry/vk_cache.json` and used to **pin VKs** in
production via `vk_ref`.

### 1.3 Envelope hash (optional, for audits)

For end-to-end audit bundles, hash a normalized projection:

env_hash = sha3-256(canonical_json_bytes({
“kind”: envelope.kind,
“public_inputs”: envelope.public_inputs,     # canonical hex Fr
“vk_ref”: envelope.vk_ref or null,
“vk_hash”: computed_or_cached,               # if vk embedded, derive as in 1.2
“proof_hash”: sha3-256(canonical_json_bytes(envelope.proof))
}))

> We do **not** bind `meta` into `env_hash` (operational metadata only).

---

## 2) Lockfiles & pinning

### 2.1 Python

- Pin interpreter and wheels:
  - `python --version` (e.g., `3.11.9`)
  - `pip freeze --require-hashes > requirements.lock.txt`  
    (Use `pip-tools` or `uv pip compile --generate-hashes` for transitive pins.)
- Build flags:
  - Record `OPENSSL_VERSION`, `CFLAGS`, and `PY_ECC`/backend versions if used.
- Example artifacts:
  - `zk.lock/python/requirements.lock.txt`
  - `zk.lock/python/interpreter.txt`

### 2.2 Node/TypeScript (if adapters/tools used)

- Pin with `package-lock.json` or `pnpm-lock.yaml`.
- Node runtime:
  - `node --version` and `npm --version`.

### 2.3 Rust (native backends, optional)

- `Cargo.lock` and `rustc -Vv` (include target triple).
- If using `ark-bn254`/`mcl` bindings, record crate versions and feature flags.

### 2.4 Pyodide/WASM (studio-wasm)

- `studio-wasm/pyodide.lock.json` pins upstream artifacts by checksum.

### 2.5 SRS / Trusted setup (Groth16/PLONK+KZG)

- Store **content hashes** for SRS artifacts:
  - `srs_hash = sha3-256(raw_srs_bytes)` or a structured hash over multi-file transcripts.
- Record ceremony provenance (URL, commit, transcript id) in
  `zk/registry/registry.yaml` under the circuit version notes.

---

## 3) Build & environment notes

Create a small **repro manifest** at build time:

```json
{
  "repro": 1,
  "git_commit": "abc1234",
  "timestamp_utc": "2025-10-08T14:00:00Z",
  "platform": { "os": "linux", "arch": "x86_64", "libc": "glibc-2.31" },
  "python": { "version": "3.11.9", "impl": "CPython", "hash_algo": "sha3_256" },
  "node": { "version": "20.11.1" },
  "rust": { "rustc": "1.80.1", "target": "x86_64-unknown-linux-gnu" },
  "zk": {
    "verifiers": {
      "pairing_bn254": "py_ecc|mcl",      // selected backend
      "poseidon": "consts@poseidon2-p128",
      "kzg": "bn254-kzg@v1"
    },
    "policy": "zk/integration/policy.py@sha3-256:…",
    "vk_cache": "zk/registry/vk_cache.json@sha3-256:…"
  }
}

Persist it as: zk.lock/repro.manifest.json.

3.1 Docker (suggested)

Use a pinned base image and export digest:

FROM python:3.11.9-slim@sha256:<digest>
RUN apt-get update && apt-get install -y build-essential
COPY requirements.lock.txt /app/
RUN pip install --require-hashes -r /app/requirements.lock.txt

Record the image digest in your repro manifest.

⸻

4) Deterministic metering
	•	Units are computed before crypto using canonical sizes and counts:
	•	proof_bytes, vk_bytes, public_inputs, kzg_openings.
	•	This makes metering independent of CPU speed or selected backend.

Check constants in zk/integration/policy.py and version changes together with
the registry to avoid economic drift.

⸻

5) Checklist (per circuit update)
	•	VK added via zk/registry/update_vk.py → produces vk_hash.
	•	vk_cache.json updated and committed.
	•	Policy allowlist includes the circuit_id@version.
	•	SRS hashes recorded (if applicable).
	•	Lockfiles updated:
	•	Python requirements.lock.txt (with hashes)
	•	Node package-lock.json / pnpm-lock.yaml (if used)
	•	Rust Cargo.lock (if native)
	•	Repro manifest regenerated with new git commit and digests.

⸻

6) Reproduction quickstart

6.1 Verify a payload deterministically

# 1) Ensure environment matches lockfiles
python --version
pip install --require-hashes -r zk.lock/python/requirements.lock.txt

# 2) Inspect pinned VKs
python -m zk.registry.list_circuits --format table

# 3) Meter-only dry run (pure parsing + size accounting)
python - <<'PY'
import json
from zk.integration.omni_hooks import zk_verify
env = json.load(open("envelope.json"))
print(zk_verify(env, meter_only=True))
PY

# 4) Full verify
python - <<'PY'
import json
from zk.integration.omni_hooks import zk_verify
env = json.load(open("envelope.json"))
print(zk_verify(env))
PY

6.2 Compute canonical hashes for audit

python - <<'PY'
import json, hashlib
from zk.integration.types import canonical_json_bytes
from zk.integration.types import compute_vk_hash

env = json.load(open("envelope.json"))
vk = env.get("vk")
vk_hash = None
if vk is not None:
    vk_hash = compute_vk_hash(env["kind"], env["vk_format"], vk, env.get("fri_params"))
proof_hash = hashlib.sha3_256(canonical_json_bytes(env["proof"])).hexdigest()
proj = {
  "kind": env["kind"],
  "public_inputs": env["public_inputs"],
  "vk_ref": env.get("vk_ref"),
  "vk_hash": vk_hash,
  "proof_hash": proof_hash
}
env_hash = hashlib.sha3_256(canonical_json_bytes(proj)).hexdigest()
print("vk_hash:", vk_hash)
print("proof_hash:", proof_hash)
print("env_hash:", env_hash)
PY


⸻

7) Common pitfalls
	•	Non-canonical numbers: decimal vs hex vs limb arrays — always normalize to
hex big-endian with 0x prefix before hashing/sizing.
	•	Whitespace/key order in JSON proofs/VKs — never hash raw tool output; always
canonicalize.
	•	SRS drift: switching ceremonies without bumping circuit_id breaks pinning.
	•	Backend ambiguity: record the selected pairing/MSM/hash backend (banner logs
or repro manifest).
	•	CI environment: ensure CI uses the same Python minor version and OpenSSL as
production when your hash libraries link dynamically.

⸻

8) File layout for reproducibility

zk.lock/
  python/
    requirements.lock.txt
    interpreter.txt
  node/
    package-lock.json
  rust/
    Cargo.lock
  repro.manifest.json


⸻

9) Versioning policy
	•	Bump @version in circuit_id@version when any of the following changes:
	•	VK bytes, Poseidon params, public-input order, domain size, SRS, or policy
constants that alter acceptance conditions.
	•	Keep prior versions pinned and allowlisted during migrations.

⸻

10) Appendix: Reference commands
	•	List circuits with pinned hashes:

python -m zk.registry.list_circuits --format json | jq .


	•	Update/add a VK:

python -m zk.registry.update_vk --kind groth16_bn254 \
  --vk-file vk.json --circuit-id counter_groth16_bn254@2


	•	Validate size caps (policy preview):

python - <<'PY'



import json
from zk.integration.policy import compute_units, get_limits
env = json.load(open(“envelope.json”))
print(get_limits(env[“kind”]))
print(compute_units(env))
PY

