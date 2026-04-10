# Fixtures — Capability Host

Canonical, deterministic fixtures used by unit/integration tests and examples
for the **capabilities** subsystem. These fixtures let tests run without any
external services while still exercising end-to-end plumbing (enqueue → resolve
→ read) and schema validation.

> All JSON files are UTF-8, LF line endings, sorted keys, and trailing newline.
> Hashes are computed over canonical bytes with the domain tag noted below.

---

## Contents

- `ai_prompt.json`  
  A tiny, deterministic AI job request used by `enqueue_ai` tests. Includes a
  short prompt, model id, and policy knobs (size caps).  
  _Schema_: `capabilities/schemas/job_request.cddl` (AI variant)

- `quantum_circuit.json`  
  Minimal trap-circuit spec for Quantum tests (depth × width × shots).  
  _Schema_: `capabilities/schemas/job_request.cddl` (Quantum variant)

- `result_example.json`  
  Example **ResultRecord** payload matching the digest in `jobs/types.py`
  comments. Used by result store / GC / RPC read tests.  
  _Schema_: `capabilities/schemas/result_record.cddl`

---

## Canonicalization & Digests

All result payload digests use:

result_digest = sha3_256(b”animica.cap.result.v1” || canonical_json_bytes)

Where `canonical_json_bytes` are produced by:
- UTF-8
- Sorted object keys
- No insignificant whitespace (pretty print is forbidden for hashing)
- Trailing newline allowed

Use the helper in `capabilities/cbor/codec.py` or a CLI like:

```bash
python - <<'PY'
import json, sys, hashlib
DOM=b"animica.cap.result.v1"
data=json.load(open("capabilities/fixtures/result_example.json","rb"))
cj=json.dumps(data, separators=(',',':'), sort_keys=True).encode()
print("0x"+hashlib.sha3_256(DOM+cj).hexdigest())
PY


⸻

Validation

Validate fixtures against their schemas:
	•	CDDL (via cddl tool) for CBOR-backed objects:

cddl capabilities/schemas/job_request.cddl validate capabilities/fixtures/ai_prompt.json
cddl capabilities/schemas/job_request.cddl validate capabilities/fixtures/quantum_circuit.json


	•	JSON-Schema (for zk/ABI-style docs when applicable):

ajv validate -s capabilities/schemas/zk_verify.schema.json -d some_input.json



⸻

Determinism Requirements
	•	Inputs must be small and fully deterministic (no timestamps, no random
salts). Any fields that would vary by environment must be removed or mocked.
	•	Text fields should be ASCII or well-formed UTF-8; avoid locale-specific
symbols unless explicitly tested.

⸻

Suggested Local Checks

# 1) Lint & sort keys (will fail if not canonical)
jq -S . capabilities/fixtures/ai_prompt.json >/dev/null

# 2) Print size (for inline vs DA thresholds)
wc -c capabilities/fixtures/ai_prompt.json

# 3) Recompute digest and compare to tests’ expected value
python -m capabilities.cbor.codec --digest capabilities/fixtures/result_example.json


⸻

Updating Fixtures
	1.	Edit the JSON file(s) while keeping keys sorted.
	2.	Recompute result_digest if the payload changed and update the expected
values in tests under capabilities/tests/.
	3.	Run:

make test  # or: pytest -q capabilities



Do not embed secrets, API keys, or real provider identifiers. Fixtures are
public and shipped with source.

⸻

Licensing

Fixtures are part of the Animica repository and are licensed under the project
license. Any third-party snippets must include proper attribution and be
reviewed for redistribution.

