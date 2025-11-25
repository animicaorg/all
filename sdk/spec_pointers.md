# Spec pointers for SDKs

The SDKs (Python, TypeScript, Rust) **must** stay in lockstep with the canonical
RPC and ABI specifications. These are the single sources of truth that codegen,
validators, and examples rely on.

## Canonical artifacts

- **OpenRPC schema** (full node RPC interface)  
  `spec/openrpc.json` → [../spec/openrpc.json](../spec/openrpc.json)

- **ABI schema** (contract ABI types & encoding rules)  
  `spec/abi.schema.json` → [../spec/abi.schema.json](../spec/abi.schema.json)

> These files are copied into `sdk/common/schemas/` during development so that
> each language package can depend on a stable, versioned path.

## In-repo copies (kept in sync)

- `sdk/common/schemas/openrpc.json`  ← copy of `spec/openrpc.json`
- `sdk/common/schemas/abi.schema.json` ← copy of `spec/abi.schema.json`

A small header comment with the **source commit** is kept at the top of each
copied file (a JSON `"$origin"` field) so diffs remain auditable.

## Refresh workflow

When the specs change, refresh the local copies and run language tests:

```bash
# From repo root
cp spec/openrpc.json sdk/common/schemas/openrpc.json
cp spec/abi.schema.json sdk/common/schemas/abi.schema.json

# Optionally stamp the origin commit
commit=$(git rev-parse --short HEAD)
jq --arg c "$commit" '. + {"$origin":{"repo":"root","commit":$c,"path":"spec/openrpc.json"}}' \
  spec/openrpc.json > sdk/common/schemas/openrpc.json.tmp && \
  mv sdk/common/schemas/openrpc.json.tmp sdk/common/schemas/openrpc.json

jq --arg c "$commit" '. + {"$origin":{"repo":"root","commit":$c,"path":"spec/abi.schema.json"}}' \
  spec/abi.schema.json > sdk/common/schemas/abi.schema.json.tmp && \
  mv sdk/common/schemas/abi.schema.json.tmp sdk/common/schemas/abi.schema.json

# Run SDK builds/tests
make py ts rs

Sanity checks
	•	Schema validity

jq empty sdk/common/schemas/openrpc.json
jq empty sdk/common/schemas/abi.schema.json


	•	No unintended drift (expect only the $origin stamp and intentional edits)

git diff -- sdk/common/schemas



Downstream references
	•	ABI encoding rules: see vm_py/specs/ABI.md → ../vm_py/specs/ABI.md
	•	Runtime gas model (for examples/tests): vm_py/specs/GAS.md → ../vm_py/specs/GAS.md

If these pointers move, update this file and the refresh scriptlets above.
