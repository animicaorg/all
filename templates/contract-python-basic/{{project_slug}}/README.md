# {{project_name}} ({{project_slug}})

> Deterministic Python smart contract for the **Animica VM** (vm_py).  
> License: **{{license}}** • Contract class: **{{contract_class}}** • Default init: **{{init_value}}**

This project was scaffolded from the `contract-python-basic` template. It provides a minimal but production-minded starting point with a tiny stateful contract, a canonical manifest/ABI, local unit tests that run entirely on the Python VM, and an end-to-end script to deploy and interact with a devnet node via the Python SDK.

---

## Table of contents

- [What’s inside](#whats-inside)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Quickstart](#quickstart)
- [Local testing (no node)](#local-testing-no-node)
- [Deploy to a running devnet](#deploy-to-a-running-devnet)
- [Manifest & ABI](#manifest--abi)
- [Determinism rules](#determinism-rules)
- [Customizing the contract](#customizing-the-contract)
- [Linting & typing](#linting--typing)
- [Troubleshooting](#troubleshooting)
- [Security & audit checklist](#security--audit-checklist)
- [License](#license)

---

## What’s inside

{{project_slug}}/
├─ contract.py           # Contract code (class {{contract_class}})
├─ manifest.json         # ABI + metadata (kept in sync with spec schema)
├─ tests_local.py        # Local unit tests on vm_py (no node)
└─ deploy_and_test.py    # Build+deploy+call against a node RPC

**Contract behavior (default Counter)**

- Stores a single integer value at a fixed storage key.
- `get() -> int` returns the current counter value.
- `inc(delta: int = 1) -> int` increases the value (delta ≥ 0), emits an event, and returns the new value.

---

## Prerequisites

- **Python** ≥ 3.11
- **Pip** and **virtualenv** (or `uv`/`pipx`; use your preference)
- If deploying: a running node RPC (devnet/testnet)
  - Default examples assume: `RPC_URL=http://127.0.0.1:8545` and `CHAIN_ID=1337`

> If you’re using this inside the Animica monorepo, the shared tooling and pins are in `contracts/requirements.txt` and common docs in `contracts/`.

---

## Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
. .venv/bin/activate

# Option A: using monorepo pins (recommended if inside repo)
# Adjust relative path if this template was rendered elsewhere.
pip install -r ../../../contracts/requirements.txt

# Option B: standalone (outside the repo):
# pip install vm_py animica-sdk  # (Exact versions per your project policy)

Tip: If you prefer uv:

uv venv
. .venv/bin/activate
uv pip install -r ../../../contracts/requirements.txt



⸻

Quickstart

From this directory:
	1.	Run the local tests (no node needed)

python tests_local.py


	2.	Start (or ensure) a devnet node is running
If you’re in the monorepo, see tests/devnet or ops/docker/docker-compose.devnet.yml for one-shot devnet.
	3.	Deploy and interact
Set environment (override as needed):

export RPC_URL=${RPC_URL:-http://127.0.0.1:8545}
export CHAIN_ID=${CHAIN_ID:-1337}
# Optional: provide a funded mnemonic for deployment
export DEPLOYER_MNEMONIC="${DEPLOYER_MNEMONIC:-test test test test test test test test test test test junk}"

Then:

python deploy_and_test.py --rpc "$RPC_URL" --chain-id "$CHAIN_ID"



⸻

Local testing (no node)

tests_local.py executes the contract inside the vm_py interpreter using the deterministic stdlib. This path is fast and hermetic, ideal for unit tests and CI.

python tests_local.py

You can extend these tests to cover:
	•	Event emission and argument encoding
	•	Error conditions (e.g., invalid deltas)
	•	Storage invariants across multiple calls

⸻

Deploy to a running devnet

deploy_and_test.py demonstrates a minimal build→deploy→call loop using the Python SDK. It will:
	1.	Build the package (manifest + code hash) if needed (depending on tooling present).
	2.	Construct and sign a transaction (PQ signer depending on your SDK config).
	3.	Submit to a node RPC and wait for the receipt.
	4.	Make follow-up calls (e.g., inc then get) to verify state.

Usage:

python deploy_and_test.py --rpc "$RPC_URL" --chain-id "$CHAIN_ID"

Environment variables (optional):
	•	DEPLOYER_MNEMONIC — funded mnemonic used by the SDK signer.
	•	GAS_PRICE / MAX_FEE — if your network requires explicit fee settings.
	•	TIMEOUT_SECS — RPC request timeout.

⸻

Manifest & ABI
	•	manifest.json is the canonical ABI + metadata for this contract and should remain in sync with the source.
	•	If you change the public method signatures or event shapes, update the manifest accordingly.
	•	In the monorepo, prefer using the helper in contracts/tools/build_package.py to (re)build the package deterministically:

python ../../../contracts/tools/build_package.py \
  --src contract.py \
  --out ../build/{{project_slug}} \
  --name "{{project_name}}" \
  --class {{contract_class}}



This keeps the code hash that studio-services uses for verification aligned with your source.

⸻

Determinism rules

To ensure reproducible execution on-chain:
	•	Only import from the VM stdlib (from stdlib import storage, events, hash, abi, treasury, syscalls) and other deterministic helpers provided by the platform.
	•	Do not use wall clock, random, OS/environment, I/O, or networking in contract code.
	•	Keep numeric bounds within the platform’s documented limits; use checked/saturating helpers when appropriate.
	•	Emit stable, well-typed events; avoid schema changes unless you plan a migration.

See contracts/CODESTYLE.md for the deterministic subset and patterns.

⸻

Customizing the contract
	•	Rename class: change class {{contract_class}}: and references in tests and scripts.
	•	Change storage layout: use fixed keys or documented key-derivation helpers; avoid dynamic schema drift.
	•	Add methods: document with clear docstrings (ABI doc) and update manifest.json.
	•	Add events: extend stdlib.events usage with canonical event names.
	•	Initialization: if you adopt an explicit initializer pattern, keep it idempotent.

When in doubt, follow examples in contracts/stdlib/* and example projects.

⸻

Linting & typing

If you’re inside the monorepo:

ruff check .
mypy --config-file ../../../contracts/mypy.ini .

For standalone usage, adopt equivalent configs suitable for your project. Favor strict typing for public ABI surfaces.

⸻

Troubleshooting
	•	ModuleNotFoundError: vm_py
Ensure the environment installed the required packages (see Setup).
	•	CHAIN_ID mismatch
Your node’s chainId must match the transaction’s. Confirm via RPC or configuration.
	•	Deployment fails with insufficient funds
Fund the deployer derived from DEPLOYER_MNEMONIC in your devnet, or use a faucet if available.
	•	Verification mismatch (studio-services)
Rebuild your package to refresh the code hash, then retry verification.

⸻

Security & audit checklist
	•	Public methods validate inputs (types, ranges).
	•	No unchecked overflows/underflows; use safe math where relevant.
	•	Event parameters are canonical and do not leak secret material.
	•	Storage keys are stable and conflict-free.
	•	Upgrades and governance (if any) are explicit and tested.
	•	Avoid reentrancy patterns unless using a known-safe design.

For deeper guidance, see contracts/SECURITY.md and contracts/docs/INVARIANTS.md.

⸻

License

This project is provided under {{license}}. See the repository’s LICENSE file or your organization’s policy for full terms.

