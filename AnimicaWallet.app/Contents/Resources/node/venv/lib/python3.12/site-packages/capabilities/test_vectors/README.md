# Capabilities — Test Vectors

Deterministic, hand-curated vectors used by unit tests and cross-module checks for the **capabilities** subsystem (syscalls ABI, AI/Quantum enqueue → result lifecycle, zk verification).

These vectors are designed to be:
- **Stable** across platforms and time,
- **Minimal** yet representative,
- **Schema-valid** against files in `capabilities/schemas/`.

## Contents

- `enqueue_and_read.json` — Canonical vectors covering:
  - deterministic `task_id` derivation,
  - enqueue receipts,
  - result availability on the next block,
  - error cases (too-large payload, unknown namespace, etc.).

> Additional vectors may be added, but must follow the **canonicalization rules** below.

## Canonicalization Rules (JSON)

1. **Encoding:** UTF-8, Unix newlines (`\n`), **newline at EOF**.
2. **Object key order:** Lexicographic sort by key (stable).  
   - When generating, ensure keys are sorted before writing.
3. **Numbers:** Base-10 integers unless a fractional value is semantically required.
4. **Booleans/null:** Lowercase JSON literals.
5. **Hex strings:** Lowercase, `0x`-prefixed, even-length nybbles (e.g., `0x00ab…`).
6. **Timestamps:** Milliseconds since Unix epoch as integer (int64 range).
7. **Addresses:** Bech32m (prefix `anim1…`), all lowercase, no mixed-case HRP.
8. **IDs / Enums:** Use the exact spellings from the schemas (e.g., `"AI"`, `"Quantum"`).
9. **Arrays:** Order is **semantically significant**; do not resort.
10. **No secrets:** Inputs are non-sensitive, synthetic, or previously public fixtures.

## Determinism & Derivations

- **Task IDs:** `task_id = H(chainId | height | txHash | caller | payload)`  
  See `capabilities/jobs/id.py` for the precise domain tag and encoding.
- **Receipts / Records:** Signaling fields like `available_height` must reflect
  “**available next block**” semantics used by the tests.
- **Digests:** `sha3_256` or `sha3_512` as indicated in schemas; lowercase hex.

## Validating Schemas

Vectors must validate against:
- `capabilities/schemas/syscalls_abi.schema.json`
- `capabilities/schemas/job_request.cddl`
- `capabilities/schemas/job_receipt.cddl`
- `capabilities/schemas/result_record.cddl`

A lightweight local check (Python example):

```bash
python - <<'PY'
import json, pathlib
from capabilities.cbor.codec import validate_json  # helper wraps jsonschema/cddl checks

root = pathlib.Path("capabilities/test_vectors")
for p in root.glob("*.json"):
    data = json.loads(p.read_text())
    validate_json(data)  # raises on failure
    print("OK", p)
PY

Running Tests

Run the module tests that consume these vectors:

pytest -q capabilities/tests -k "enqueue or vector or rpc_mount"

(Full suite lives across modules; this folder only houses the data vectors.)

Contributing New Vectors
	1.	Start from the smallest representative case.
	2.	Ensure canonical formatting (see rules above).
	3.	Keep payloads tiny; prefer previews + digests over full blobs.
	4.	Add a short comment block at the top (JSON "_comment" field) explaining scenario & intent.
	5.	Run schema validation and the test suite.
	6.	Submit with a clear commit message: capabilities: add vector <name> (covers <case>).

License

Unless otherwise noted, vectors in this directory inherit the repository license and are intended to be freely used for interoperability testing.

