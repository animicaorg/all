# Verify Contract Source ↔ Code Hash

This guide explains how Animica verifies that a deployed contract’s **on-chain code hash** matches a given **source + manifest**. The process is **fully reproducible**: anyone can recompile the source with the pinned toolchain and obtain the same code hash.

> See also
> - Encoding & hashing rules: `docs/spec/ENCODING.md`
> - VM compiler & determinism: `docs/vm/COMPILER.md`, `docs/vm/SANDBOX.md`
> - Studio Services verification API: `studio-services/README.md`
> - State model (code hash field): `execution/state/accounts.py`, `docs/spec/OBJECTS.md`

---

## What is verified?

1. **Canonicalization**
   - Manifest JSON is normalized (UTF-8, sorted keys, canonical numbers/booleans, no extraneous whitespace).
   - Source is UTF-8 with LF newlines (`\n`) and no BOM.

2. **Deterministic compile**
   - The VM(Py) compiler lowers Python → IR and produces **canonical encoded bytes** (msgspec/CBOR).
   - A **code hash** is computed over those exact code bytes.

3. **On-chain match**
   - The node stores the contract account’s `code_hash`.
   - Verification compares **computed code hash** vs **on-chain code hash** for the address (or inferred from a deploy tx).

---

## Hashes & digests

### Code hash (consensus-relevant)

code_bytes = CanonicalEncode(IR)        # stable msgspec/CBOR encoding
code_hash  = SHA3-256(code_bytes)

- Used by execution/state to reference immutable code.
- Must be identical across platforms and toolchains (given the same inputs).

### Artifact digest (supply-chain provenance)

manifest_cjson = CanonicalJSON(manifest)         # keys sorted, UTF-8, stable formatting
artifact_digest = SHA3-256( manifest_cjson || 0x00 || source_bytes )

- Useful for registries, artifact stores, and reproducible builds.
- Not consensus-critical; used in verification reports and catalogs.

> Both functions are implemented in the repo:
> - Canonical JSON: `core/utils/serialization.py`
> - IR encode: `vm_py/compiler/encode.py`
> - Hash helpers: `core/utils/hash.py` (or language SDK wrappers)

---

## Option A — Verify with Studio Services (recommended)

Studio Services exposes a stateless `/verify` endpoint that:
1) canonicalizes + compiles your inputs,  
2) computes `code_hash` & `artifact_digest`, and  
3) looks up the on-chain `code_hash` to compare.

### POST `/verify`

**Body (JSON)**:
```json
{
  "address": "anim1…",          // or "txHash": "0x…"
  "manifest": { "...": "..." },
  "source": "base64-encoded-or-UTF8-string"
}

Response (JSON):

{
  "status": "ok",
  "address": "anim1…",
  "chainId": 1,
  "codeHashComputed": "0x…",
  "codeHashOnChain": "0x…",
  "match": true,
  "artifactDigest": "0x…",
  "compiler": {
    "vm_py_version": "x.y.z",
    "gas_table_rev": "…",
    "encoder": "msgspec-cbor@rev"
  },
  "timestamps": { "received": "...", "compiled": "..." }
}

Endpoints & models: studio-services/studio_services/routers/verify.py, studio_services/models/verify.py.

You can later fetch results:
	•	GET /verify/{address}
	•	GET /verify/{txHash}

⸻

Option B — Verify locally (CLI/snippets)

1) Compute code hash locally

Python snippet

import json
from vm_py.runtime.loader import load
from vm_py.compiler.encode import encode
from core.utils.hash import sha3_256   # or vm_py/runtime/hash_api for a pure-Py wrapper

manifest = json.load(open("counter/manifest.json", "r", encoding="utf-8"))
source   = open("counter/contract.py", "rb").read()

module = load(manifest=manifest, source_py=source)  # validates & compiles deterministically
ir_bytes = encode(module.ir)                        # canonical IR encoding
code_hash_local = "0x" + sha3_256(ir_bytes).hex()
print("local code hash:", code_hash_local)

Tip: If you prefer a tool, python -m vm_py.cli.compile can emit IR bytes; then hash the file with SHA3-256.

2) Fetch on-chain code hash

Use Studio Services GET /verify/{address} or the SDK helper if available. (Direct state reads may be exposed in future RPCs; Studio Services handles this today.)

3) Compare

local == on_chain ? "MATCH ✅" : "MISMATCH ❌"


⸻

Option C — Browser-only (Studio Web)
	1.	Open Studio Web and import your manifest.json + contract.py.
	2.	Click Verify (or Compile then Verify).
	3.	Paste the contract address (or a tx hash).
	4.	The app recompiles in a pinned Pyodide VM and queries Studio Services to compare.
The report shows codeHashComputed, codeHashOnChain, and match.

Pyodide assets & pinning: studio-wasm/pyodide.lock.json, studio-wasm/scripts/fetch_pyodide.mjs.

⸻

Reproducibility checklist
	•	Pin toolchain
	•	VM(Py) version, gas table (vm_py/gas_table.json), and msgspec/CBOR encoder build.
	•	For browser, use the pinned Pyodide version & lockfile.
	•	Canonical inputs
	•	manifest.json must adhere to canonical JSON (sorting, UTF-8, stable numbers).
	•	Source must be UTF-8, LF newlines, no BOM.
	•	Deterministic features only
	•	No forbidden imports; no nondeterministic behavior.
	•	Compiler & validator enforce this (see vm_py/validate.py).
	•	Stable environment
	•	Ensure identical feature flags (e.g., vm_py/config.py strict mode) when reproducing.

⸻

Common failure modes

Symptom	Likely cause	Fix
codeHashComputed != codeHashOnChain	Different compiler version or gas table	Re-pin versions / use Studio Services
Verification rejects manifest	Non-canonical JSON (key order, floats)	Use canonical JSON serializer
Determinism error during compile	Disallowed import or syntax	Remove/replace; see docs/vm/SANDBOX.md
“Source compiled but address has empty code”	Address is not a contract	Verify the correct address or tx hash
Mismatch persists with pinned toolchain	Uploaded source doesn’t match deployed artifact	Rebuild from the exact artifact used to deploy


⸻

FAQ

Q: Where is the on-chain code hash stored?
A: In the account’s code_hash field (see execution/state/accounts.py). Studio Services queries the node to read it.

Q: What exactly is hashed?
A: The canonical IR bytes produced by the compiler. Not the raw Python source.

Q: Can I verify by tx hash?
A: Yes. Studio Services can trace the deploy tx → derived address → on-chain code_hash, then compare.

Q: Is the artifact digest used on-chain?
A: No. It’s for registries, provenance, and human-facing catalogs; code_hash is the consensus identifier.

⸻

Minimal cURL examples

Submit verification

curl -sS -X POST "$SERVICES_URL/verify" \
  -H "Content-Type: application/json" \
  --data-binary @<(jq -n --arg addr "anim1..." \
     --argjson manifest "$(cat counter/manifest.json)" \
     --arg source "$(cat counter/contract.py)" \
     '{address:$addr, manifest:$manifest, source:$source}')

Fetch last result

curl -sS "$SERVICES_URL/verify/anim1..."


⸻

Outputs to save
	•	codeHashComputed — what you (re)built
	•	codeHashOnChain — what the node has for that address
	•	artifactDigest — reproducible supply-chain fingerprint
	•	Toolchain versions (compiler, encoder, gas table rev)
	•	The exact manifest/source blobs used

Store these with release artifacts for long-term auditability.

⸻

Security notes
	•	No server-side signing in verification — inputs are public and deterministic.
	•	Pin & audit VKs/circuit IDs only if you use zk.verify syscalls; unrelated to code hash verification.
	•	Keep build pipelines hermetic and record artifactDigest.

With these steps, anyone can independently confirm that a deployed contract address corresponds exactly to your published source and manifest.
