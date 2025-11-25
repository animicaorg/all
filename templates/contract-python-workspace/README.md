# Contract Python Workspace (Template)

A turnkey scaffold for building **Animica Python contracts** with a batteries-included workspace:
deterministic VM toolchain, standard library, fixtures, tests, and deploy/verify scripts.  
Use this template with the `templates/engine` renderer to generate a new repo you can build,
test, deploy to a devnet/testnet, and verify on-chain.

---

## What this template generates

A mono-repo structured for clarity and CI:

/
├─ contracts/                     # Source contracts & stdlib (pulled from repo)
│  ├─ examples/                   # Optional: token, escrow, AI agent, quantum RNG, registry
│  ├─ tools/                      # build/deploy/call/verify/lint helpers (CLI)
│  ├─ fixtures/                   # ABIs, manifests, vectors
│  ├─ tests/                      # Contract unit tests (pytest)
│  ├─ build/                      # Compiled IR & packages (gitignored)
│  ├─ CODESTYLE.md                # Deterministic Python subset rules
│  ├─ SECURITY.md                 # Audit checklist & invariants
│  ├─ pyproject.toml, requirements.txt, .env.example
│  └─ Makefile
├─ .github/workflows/             # (optional) CI for lint/test/build
├─ README.md                      # Workspace readme (rendered)
├─ .gitignore                     # Seeded, includes venv & artifacts
└─ LICENSE                        # From your chosen license

> **Determinism first.** Everything is wired to the `vm_py` interpreter and `sdk/python` for deploys.
Policies and lint rules keep contracts in a deterministic subset of Python.

---

## Prerequisites

- **Python 3.10+** (3.11 recommended)
- **pip** (or **pipx**) & **virtualenv** (recommended)
- **git**
- Optional: **Docker** (if you prefer containerized runs)  
- An Animica node endpoint (RPC/WS). Devnet: `http://127.0.0.1:8545`, chain id `1337`.

---

## Render this template

This folder ships with a variable schema in [`variables.json`](./variables.json).  
Render with the provided engine:

```bash
# From repo root
python -m templates.engine.cli render \
  --template templates/contract-python-workspace/template.json \
  --vars templates/contract-python-workspace/variables.json \
  --out ./animica-contracts \
  --set workspace_slug=animica-contracts \
  --set org_name="Animica Labs" \
  --set author_name="Your Name" \
  --set author_email="you@example.com" \
  --set license=MIT \
  --set rpc_url="http://127.0.0.1:8545" \
  --set chain_id=1337 \
  --set include_examples=true \
  --set init_git=true

Variable reference (from variables.json)
	•	workspace_slug — folder/repo name (e.g., animica-contracts)
	•	org_name — shown in docs and package metadata
	•	author_name, author_email — package authorship
	•	license — MIT | Apache-2.0 | BSD-3-Clause | UNLICENSED
	•	rpc_url — default node RPC URL used by helper scripts
	•	chain_id — CAIP-2 (1 mainnet, 2 testnet, 1337 devnet)
	•	include_examples — add well-documented sample contracts & tests
	•	init_git — initialize a Git repository with first commit

All variables can be set via --set flags or a custom vars file.

⸻

First steps in the generated workspace

cd animica-contracts

# 1) Create & activate a virtualenv
python -m venv .venv
. .venv/bin/activate

# 2) Install toolchain (vm_py, sdk, linters)
pip install -U pip wheel
pip install -r contracts/requirements.txt

# 3) Copy .env and set your RPC/ChainId if needed
cp contracts/.env.example contracts/.env
# Edit RPC_URL/CHAIN_ID as appropriate

# 4) Run lint & unit tests (examples enabled by default)
make -C contracts lint
make -C contracts test

Quick smoke test (Counter)

# Build IR & package for the Counter example
python contracts/tools/build_package.py \
  --source tests/fixtures/contracts/counter/contract.py \
  --manifest tests/fixtures/contracts/counter/manifest.json \
  --out contracts/build/counter.pkg.json

# Deploy to devnet (requires a funded test account in .env)
python contracts/tools/deploy.py \
  --package contracts/build/counter.pkg.json

# Call a method
python contracts/tools/call.py \
  --address <deployed_address> \
  --abi tests/fixtures/abi/counter.json \
  --fn inc --args '[]'


⸻

Build & Deploy flow (recommended)

Build
	•	contracts/tools/build_package.py compiles source → IR → code hash → package bundle.
	•	Packages land under contracts/build/ and include the ABI + code hash manifest.

Deploy
	•	contracts/tools/deploy.py uses sdk/python to sign & submit a deploy tx to the node.
	•	Requires .env with RPC_URL, CHAIN_ID, and a deployer mnemonic (or keystore).

Call / Read
	•	contracts/tools/call.py for local testing against RPC (read or write).

Verify
	•	contracts/tools/verify.py re-compiles source and compares the code hash via studio-services.
	•	Produces a persistent verification record that can be queried later.

Make targets for convenience:

make -C contracts build
make -C contracts deploy EX=token
make -C contracts verify EX=token


⸻

Testing
	•	Unit tests (pytest):
Located in contracts/tests/. These run the VM locally without network side effects.

make -C contracts test          # all tests
pytest -q contracts/tests/test_token_stdlib.py

	•	Vectors: Fixtures in contracts/fixtures/vectors/ cover transfers, escrow flows, AI/quantum mixes.
	•	Determinism checks:
contracts/tools/lint_contract.py enforces forbidden imports, recursion limits, and the approved builtins allowlist.

⸻

CI & project hygiene

If you opted into CI, you’ll get:
	•	Lint (ruff/mypy)
	•	Tests (pytest)
	•	Optional cache for wheels to speed up CI

The CI config leverages the shared template bits under templates/_common/.

⸻

Security & determinism

Read these before shipping production code:
	•	contracts/SECURITY.md — audit checklist, privilege boundaries
	•	contracts/CODESTYLE.md — allowed syntax, numeric limits, and “no-I/O” rules
	•	contracts/docs/INVARIANTS.md — formal invariants for stdlib components
	•	contracts/docs/PATTERNS.md — upgrade safety, proxy pinning, pausability, roles

⸻

Troubleshooting
	•	“OOG / Out Of Gas” during tests
Raise the gas limit in your test or optimize the function. Check vm_py gas estimator.
	•	“ChainId mismatch” on deploy
Ensure .env CHAIN_ID matches node chainId, and your manifest’s chainId if pinned.
	•	“Determinism violation” / lint error
Remove disallowed imports (os, time, network I/O, randomness without seed) and stay within the stdlib APIs.
	•	RPC connectivity issues
Verify RPC_URL, node health, and CORS if calling from a browser context.

⸻

License

The generated workspace includes your selected license identifier.
Template content © Animica contributors; third-party notices live in LICENSE-THIRD-PARTY.md where applicable.

⸻

Next steps
	•	Add your first contract under contracts/examples/ or start fresh in contracts/
	•	Extend tests with scenario vectors
	•	Wire into devnet via tests/devnet or ops/docker if you need a local cluster

Happy building! 🚀
