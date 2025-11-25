# Contracts

This directory holds **application contracts** written for the Animica Python-VM. It is the home for
reusable examples, your project packages (source + manifest), build artifacts (IR), and helper scripts
to **compile → simulate → deploy → verify** consistently across devnet/testnet/mainnet.

---

## What lives here

- `examples/` – reference contracts you can copy from (e.g., `counter`, `escrow`, `ai_agent`).
- `packages/` – your own packages: each package is a folder with a **manifest** and **source files**.
- `build/` – compiler outputs per package (IR bytes, ABI copy, code hash, summary).
- `scripts/` – optional helpers to lint/format/validate/package.

> The authoritative schemas for ABIs and manifests are kept under `spec/abi.schema.json` and
> `spec/manifest.schema.json`. Keep packages conformant to ensure tooling interop.

---

## Prerequisites

- **Python 3.11+** with `pip` (or uv/poetry if you prefer)  
- Repo modules available on your `PYTHONPATH` (or install editable):  
  ```bash
  pip install -e ./vm_py ./sdk/python ./proofs ./core

	•	(Optional) jq and jsonschema CLI for quick schema checks:

pip install jsonschema



⸻

Package layout

A contract package is a folder (under contracts/packages/<name>/) that contains:

packages/<name>/
├─ manifest.json        # REQUIRED: contract metadata & ABI (follows spec/manifest.schema.json)
├─ contract.py          # REQUIRED: primary source module
├─ abi.json             # OPTIONAL: extracted ABI (if not embedded in manifest)
└─ README.md            # OPTIONAL: notes, usage, changelog

Minimal manifest example

{
  "$schema": "../../spec/manifest.schema.json",
  "name": "counter",
  "version": "0.1.0",
  "description": "Deterministic counter demo",
  "entry": "contract.py",
  "abi": {
    "functions": [
      { "name": "get", "inputs": [], "outputs": [{ "type": "int" }] },
      { "name": "inc", "inputs": [{ "name": "delta", "type": "int" }], "outputs": [] }
    ],
    "events": [
      { "name": "Increased", "args": [{ "name": "by", "type": "int" }, { "name": "new", "type": "int" }] }
    ]
  },
  "resources": {
    "storage": { "keys_max": 1024, "value_max_bytes": 4096 }
  },
  "capabilities": {
    "syscalls": ["events.emit", "storage.get", "storage.set", "hash.sha3_256"]
  }
}

Validate your manifest against the schema:

python - <<'PY'
import json, sys, pathlib
from jsonschema import validate, Draft202012Validator
manifest = json.load(open(sys.argv[1]))
schema   = json.load(open("spec/manifest.schema.json"))
Draft202012Validator.check_schema(schema)
validate(manifest, schema)
print("OK:", sys.argv[1])
PY contracts/packages/counter/manifest.json


⸻

Build (compile to IR + code hash)

The Python-VM compiler lives in vm_py/cli/compile.py.

Compile a package to canonical IR:

# Outputs: build/<name>/<name>.ir (binary), <name>.abi.json, summary.json
python -m vm_py.cli.compile \
  --manifest contracts/packages/counter/manifest.json \
  --out-dir contracts/build/counter

Compute a code hash (canonical identifier used by explorer/verification):

python - <<'PY'
from pathlib import Path
from hashlib import sha3_256
ir = Path("contracts/build/counter/counter.ir").read_bytes()
print("code_hash:", "0x" + sha3_256(ir).hexdigest())
PY

Determinism: IR encoding is stable and canonical; the same source+manifest produce the same IR
and thus the same code hash across machines, provided the compiler version is pinned.

⸻

Simulate locally (no node required)

Use the in-repo interpreter to run calls against an ephemeral state:

# Run a read method
python -m vm_py.cli.run \
  --manifest contracts/packages/counter/manifest.json \
  --call get

# Run a state-changing method
python -m vm_py.cli.run \
  --manifest contracts/packages/counter/manifest.json \
  --call inc \
  --args '{"delta": 3}'

For browser-based simulation (no Python), see studio-wasm. It loads a trimmed VM in Pyodide and
mirrors the same ABI and gas rules.

⸻

Deploy

Option A: Python SDK CLI (omni_sdk)
	1.	Create/import a key and unlock the keystore (uses PQ signatures under the hood via pq):

# Create a keystore (you'll be prompted for a passphrase)
python -m omni_sdk.wallet.keystore init --path ~/.animica/keystore.json

	2.	Build a deploy transaction and send it:

python -m omni_sdk.cli.deploy \
  --rpc http://127.0.0.1:8545 \
  --chain-id 1337 \
  --keystore ~/.animica/keystore.json \
  --manifest contracts/packages/counter/manifest.json \
  --ir contracts/build/counter/counter.ir

The CLI prints:
	•	txHash
	•	receipt (status/gasUsed/logs)
	•	Deployed address (bech32m anim1…)

Option B: Studio Web (browser) + Wallet Extension
	•	Open studio-web (from this repo) → “Deploy”.
	•	Connect the wallet-extension.
	•	Select your package (manifest + IR), review gas/fees, sign, and broadcast via node RPC.
	•	The deploy page streams the receipt and deployed address.

Option C: Studio Services (FastAPI proxy)

You can deploy via studio-services REST if running that component:

curl -sS -X POST "$SERVICES_URL/deploy" \
  -H "Authorization: Bearer $API_KEY" \
  -F "manifest=@contracts/packages/counter/manifest.json" \
  -F "ir=@contracts/build/counter/counter.ir"


⸻

Verify

Verification proves that an on-chain address corresponds to this source/IR.

Option A: Services /verify

curl -sS -X POST "$SERVICES_URL/verify" \
  -H "Authorization: Bearer $API_KEY" \
  -F "address=anim1xyz..." \
  -F "manifest=@contracts/packages/counter/manifest.json" \
  -F "ir=@contracts/build/counter/counter.ir"

	•	The service recompiles, computes the code hash, compares with chain metadata,
and stores a verification record. Query later via:

curl "$SERVICES_URL/verify/anim1xyz..."



Option B: Offline hash → Explorer
	1.	Compute code_hash from your IR (see Build step).
	2.	In explorer-web, open the contract page and compare the recorded hash.
	3.	If your explorer supports uploading artifacts, submit the manifest/IR pair.

⸻

ABI & Encoding
	•	ABIs must pass spec/abi.schema.json.
	•	Canonical ABI encoding is defined in vm_py/abi and mirrored in SDKs (Python/TS/Rust).
	•	For programmatic calls:
	•	Python: omni_sdk.contracts.client encodes dispatch and decodes returns/events.
	•	TypeScript: @animica/sdk offers the same under contracts/client.

⸻

Reproducible builds

To avoid “works on my machine”:
	•	Pin versions: record vm_py and SDK commits/tags used to build.
	•	Canonicalize: don’t rely on filesystem mtimes or non-deterministic code generation.
	•	Deterministic inputs only inside contracts (no wall-clock, random, I/O).
	•	Track code_hash alongside your package version in your app repo.

⸻

Testing
	•	Unit & property tests for the VM live under vm_py/tests/ and top-level tests/property/.
	•	Add contract-specific tests (e.g., scenario calls) under contracts/<yourpkg>/tests/
using vm_py.cli.run or the Python SDK.
	•	For end-to-end deploy/call on devnet, see tests/integration/test_vm_deploy_and_call.py.

⸻

Tips & gotchas
	•	Keep storage keys/values within the resource caps declared in the manifest.
	•	Gas costs are enforced: consult vm_py/gas_table.json and spec/opcodes_vm_py.yaml.
	•	For off-chain capabilities (AI/Quantum/DA), prefer view methods or
the capabilities bridge via on-chain receipts; direct non-deterministic I/O is not allowed.
	•	If verification fails, re-compile with the exact same vm_py version that the explorer shows,
and ensure your manifest hasn’t changed (even whitespace in embedded ABI can affect IR).

⸻

Quickstart (copy/paste)

# 1) Create a package
mkdir -p contracts/packages/counter
cp vm_py/examples/counter/contract.py contracts/packages/counter/
cp vm_py/examples/counter/manifest.json contracts/packages/counter/

# 2) Compile
python -m vm_py.cli.compile \
  --manifest contracts/packages/counter/manifest.json \
  --out-dir contracts/build/counter

# 3) Get the code hash
python - <<'PY'
from pathlib import Path; from hashlib import sha3_256
ir = Path("contracts/build/counter/counter.ir").read_bytes()
print("code_hash:", "0x"+sha3_256(ir).hexdigest())
PY

# 4) Deploy (Python SDK)
python -m omni_sdk.cli.deploy \
  --rpc http://127.0.0.1:8545 --chain-id 1337 \
  --keystore ~/.animica/keystore.json \
  --manifest contracts/packages/counter/manifest.json \
  --ir contracts/build/counter/counter.ir

# 5) Verify (services)
curl -sS -X POST "$SERVICES_URL/verify" \
  -H "Authorization: Bearer $API_KEY" \
  -F "address=anim1..." \
  -F "manifest=@contracts/packages/counter/manifest.json" \
  -F "ir=@contracts/build/counter/counter.ir"


⸻

See also
	•	vm_py/README.md – determinism, compiler, runtime
	•	sdk/ – Python/TypeScript/Rust SDKs (RPC, wallet, contracts)
	•	studio-web/ – in-browser IDE to edit/simulate/deploy/verify
	•	studio-services/ – REST proxy for deploy/verify/artifacts
	•	explorer-web/ – explorer that surfaces code hashes and verification
