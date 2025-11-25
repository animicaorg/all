# contracts/fixtures

Canonical, **deterministic** inputs and sample outputs used by the contract toolchain,
unit tests, local demos, and CI. These fixtures help you:

- Build reproducible deploy packages (manifest + IR/code hash).
- Smoke-test the VM and stdlib without writing new contracts.
- Exercise the SDK/CLI end-to-end against a devnet.
- Verify on-chain code via `studio-services` and compare hashes.

> All fixtures are intentionally small and use deterministic seeds so results are stable
> across machines and CI.

---

## What lives here

- **ABI JSON** snippets matching `contracts/schemas/abi.schema.json`.
- **Manifests** for ready-to-deploy examples (kept in sync with `contracts/schemas/manifest.schema.json`).
- **Inputs** (e.g., addresses, sample args) for scripted demos & tests.
- **Golden outputs** (optional): normalized/pretty JSON with stable fields only (no nonces, timestamps).

These are consumed by:

- `contracts/tools/build_package.py` – compile → IR → package (code hash).
- `contracts/tools/deploy.py` – deploy a packaged contract via the Python SDK.
- `contracts/tools/call.py` – run simple read/write calls locally or via RPC.
- `contracts/tools/verify.py` – verify source ↔ on-chain code hash with `studio-services`.
- Example flows in `contracts/examples/*/deploy_and_test.py`.

---

## Directory layout

contracts/
fixtures/
README.md                  ← this file
abi/                       ← minimal ABIs (used by codegen/tests)
manifests/                 ← ready-to-deploy manifests (no secrets)
inputs/                    ← canned arguments, addresses, bech32 samples
outputs/                   ← optional golden results for local tests

> **Note:** ABIs that are shared across the repo also appear under `tests/fixtures/abi/`.
It’s OK for this folder to duplicate small ABIs to keep contract workflows self-contained.

---

## Determinism & seeds

- All example flows derive keys from the **same mnemonic** and index range to ensure
  stable addresses. See `contracts/.env.example` and `sdk/python/examples/*`.
- Randomness inside local VM simulations uses the VM’s deterministic PRNG (seeded from tx hash).
- When examples depend on network state (e.g., gas price), scripts print the exact values used.

---

## Naming conventions

- ABI files: `name_lowercase.abi.json` (e.g., `counter.abi.json`).
- Manifests: `name_lowercase.manifest.json` (e.g., `counter.manifest.json`).
- Golden outputs: `name_lowercase.result.json`, `name_lowercase.events.json`.
- Inputs: `counter.args.json`, `token.mint.args.json`, etc.

---

## How to use

### 1) Prepare environment

```bash
# From repo root
cp contracts/.env.example contracts/.env
# Edit RPC_URL / CHAIN_ID if you’re targeting a running devnet

2) Build a package from source + manifest

python -m contracts.tools.build_package \
  --source contracts/examples/token/contract.py \
  --manifest contracts/examples/token/manifest.json \
  --out   contracts/build/token.pkg.json

This writes a content-addressed package (includes code hash) to contracts/build/.

3) Deploy with the Python SDK helper

python -m contracts.tools.deploy \
  --package contracts/build/token.pkg.json \
  --mnemonic "$(grep ^DEPLOYER_MNEMONIC contracts/.env | cut -d= -f2-)" \
  --rpc ${RPC_URL:-http://127.0.0.1:8545} \
  --chain-id ${CHAIN_ID:-1337}

The script prints the tx hash and the deployed address (bech32).

4) Call read/write methods

python -m contracts.tools.call \
  --address <bech32-address> \
  --abi contracts/fixtures/abi/token.abi.json \
  --func balanceOf --args '["<bech32-owner>"]'

5) Verify on-chain code

If you run studio-services, you can verify the code hash:

python -m contracts.tools.verify \
  --source contracts/examples/token/contract.py \
  --manifest contracts/examples/token/manifest.json \
  --address <bech32-address> \
  --services-url ${SERVICES_URL:-http://127.0.0.1:9080}


⸻

Keeping fixtures in sync
	•	Schemas: Validate ABIs and manifests before committing:

ruff check contracts
mypy contracts
# tools do schema checks at runtime; also:
python -m contracts.tools.build_package --check-only \
  --source contracts/examples/counter/contract.py \
  --manifest contracts/examples/counter/manifest.json


	•	Code hash drift: If a source file changes, its package hash will change.
Update any corresponding golden outputs or manifests referencing the old hash.
	•	Cross-repo references: If you copy ABIs to sdk/common/schemas/ or tests/fixtures/abi/,
keep them byte-for-byte identical (sorted keys, canonical formatting).

⸻

Best practices
	•	Keep fixtures small and human-readable.
	•	Avoid embedding volatile fields (timestamps, nonces) into goldens.
	•	Prefer canonical JSON (sorted keys, no trailing spaces). Tools here use the same
canonicalization as the core node and VM.
	•	Document any non-obvious assumptions at the top of each file.

⸻

FAQ

Q: Why do we keep ABIs both under tests/fixtures/abi/ and here?
A: Tests reference a centralized fixture set, while contract developers often stay within
the contracts/ subtree. Duplication for small files improves ergonomics; CI ensures they match.

Q: Can I add binary artifacts here?
A: Prefer storing compiled packages under contracts/build/. Only check in tiny, stable
binaries if they are true test goldens and documented.

⸻

License

Fixtures are distributed under the same license as the repository unless a file header states otherwise.

