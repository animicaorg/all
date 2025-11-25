# {{project_slug}} — Animica Python Contracts Workspace

A batteries-included workspace for writing **deterministic Python smart contracts** for the Animica VM, running them locally, deploying to devnet/testnet, and verifying source on-chain.

This project is generated from the **Contract Python Workspace** template and wires together:

- `vm_py` – deterministic interpreter & toolchain
- `sdk/python` – RPC, wallet, deploy/call helpers
- Standard library contracts (token, escrow, capabilities, registry, multisig)
- Test fixtures & vectors
- CLI tools for build → deploy → verify
- Lints & determinism checks

> 📌 Ownership/metadata: {{org_name}} — {{author_name}} <{{author_email}}> • License: {{license}}

---

## Quick Start

### 1) Create a virtual environment & install tools

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip wheel
pip install -r contracts/requirements.txt

2) Configure network & keys

cp contracts/.env.example contracts/.env
# Edit values as needed:
# RPC_URL=http://127.0.0.1:8545
# CHAIN_ID=1337
# DEPLOYER_MNEMONIC="sketch tomato ... 24 words ..."

Tip: Use your local devnet (see repo tests/devnet or ops/docker) or point to a shared testnet RPC.

3) Lint & test

make -C contracts lint
make -C contracts test

4) Build, deploy, and call an example

# Build a sample package (Animica-20 token)
make -C contracts build EX=token

# Deploy to the configured network (uses DEPLOYER_MNEMONIC)
make -C contracts deploy EX=token

# Verify source ↔ code hash via studio-services (optional)
make -C contracts verify EX=token

# Or call directly with the helper (read/write)
python contracts/tools/call.py \
  --address <deployed_address> \
  --abi contracts/fixtures/abi/token20.json \
  --fn balanceOf --args '["anim1xyz..."]'


⸻

Repository Layout

{{project_slug}}/
├─ contracts/
│  ├─ examples/                 # Token, Escrow, AI Agent, Quantum RNG, Registry, Multisig
│  ├─ stdlib/                   # Reusable building blocks (access, control, token, treasury…)
│  ├─ interfaces/               # Canonical ABIs (JSON)
│  ├─ tools/                    # CLI tools: build_package, deploy, call, verify, lint
│  ├─ fixtures/                 # ABIs, manifests, vectors used by tests & demos
│  ├─ tests/                    # Pytest suite (deterministic VM)
│  ├─ build/                    # Compiled IR & packages (gitignored)
│  ├─ CODESTYLE.md              # Deterministic Python subset rules
│  ├─ SECURITY.md               # Audit checklist & invariants
│  ├─ pyproject.toml            # Lint/type configs for contract sources & tools
│  ├─ requirements.txt          # Toolchain pins (vm_py, sdk, linters)
│  ├─ .env.example              # RPC_URL / CHAIN_ID / mnemonic scaffold
│  └─ Makefile                  # Convenience targets (lint/test/build/deploy/verify)
├─ README.md                    # (this file)
└─ .gitignore                   # venv & build artifacts


⸻

Build → Deploy → Verify: Deeper Dive

Build a package

The build step validates, compiles to IR, computes the code hash, and assembles a package (manifest + code blob):

python contracts/tools/build_package.py \
  --source contracts/examples/token/contract.py \
  --manifest contracts/examples/token/manifest.json \
  --out contracts/build/token.pkg.json

Artifacts are deterministic and suitable for verification and reproducible builds.

Deploy

The deploy tool crafts a CBOR transaction, signs it with a PQ key derived from your mnemonic, submits via RPC, and prints the contract address:

python contracts/tools/deploy.py \
  --package contracts/build/token.pkg.json

Uses RPC_URL and CHAIN_ID from contracts/.env. For devnet, 1337 is the default chainId.

Call / Interact

Use ABI-driven calls for both read and write methods:

python contracts/tools/call.py \
  --address <addr> \
  --abi contracts/fixtures/abi/token20.json \
  --fn transfer --args '["anim1recipient...", 1000]'

Verify (source ↔ on-chain code hash)

Recompiles your source, re-derives the code hash, and matches it against chain records via the studio-services verification API:

python contracts/tools/verify.py \
  --source contracts/examples/token/contract.py \
  --manifest contracts/examples/token/manifest.json


⸻

Environment & Configuration

contracts/.env keys:
	•	RPC_URL – HTTP(s) URL of the node (e.g., http://127.0.0.1:8545)
	•	CHAIN_ID – CAIP-2 (1 mainnet, 2 testnet, 1337 devnet)
	•	DEPLOYER_MNEMONIC – development mnemonic only (never commit real keys)
	•	(optional) SERVICES_URL – studio-services base URL if you use verify endpoints

Keep .env out of version control. The template’s .gitignore already excludes it.

⸻

Development Workflow
	1.	Author contract code under contracts/examples/<your_contract>/contract.py
Provide a matching manifest.json (ABI + metadata).
	2.	Lint for determinism
Run make -C contracts lint to ensure you follow the deterministic subset:
	•	No filesystem/network I/O
	•	No unseeded randomness
	•	Controlled numeric bounds
	•	Allowed builtins only
See contracts/CODESTYLE.md.
	3.	Unit test locally
Add tests to contracts/examples/<your_contract>/tests_local.py or contracts/tests/.
Run make -C contracts test.
	4.	Build → Deploy
make -C contracts build EX=<name> then make -C contracts deploy EX=<name>.
	5.	Verify
make -C contracts verify EX=<name> (optional but recommended).

⸻

Make Targets (Convenience)

make -C contracts lint                  # ruff + mypy
make -C contracts test                  # pytest (unit/determinism)
make -C contracts build EX=token        # build selected example
make -C contracts deploy EX=token       # deploy selected example
make -C contracts verify EX=token       # verify source ↔ code hash

Examples you can pass as EX=:
	•	token, escrow, ai_agent, quantum_rng, registry, multisig

⸻

Determinism & Security

Before shipping, review:
	•	Deterministic subset rules: contracts/CODESTYLE.md
	•	Audit checklist & invariants: contracts/SECURITY.md and contracts/docs/INVARIANTS.md
	•	Patterns: upgrade safety, proxy pinning, pausability, roles in contracts/docs/PATTERNS.md
	•	Capabilities: AI/Quantum/DA/Randomness usage in contracts/docs/CAPABILITIES.md

⸻

Troubleshooting
	•	OOG (Out of Gas)
Inspect gas estimator outputs (vm_py/compiler/gas_estimator.py) and reduce runtime work; increase tx gas in your call.
	•	ChainId mismatch
Ensure CHAIN_ID in .env matches the node’s chain id and the contract manifest if pinned.
	•	“Determinism violation / forbidden import”
Remove imports like os, time, random (non-seeded), or network libraries; stick to the provided stdlib.
	•	RPC connectivity
Verify node is up, CORS is correct (if calling from a browser), and RPC_URL is reachable.
	•	Verification fails
Ensure you built the exact source+manifest pair; code hash must be identical byte-for-byte.

⸻

Contributing
	•	Keep builds reproducible: pin versions in contracts/requirements.txt.
	•	Add tests with clear vectors under contracts/fixtures/vectors/.
	•	Run make -C contracts lint test before pushing.

⸻

License

{{license}} (see LICENSE if present).
Third-party notices may be listed under LICENSE-THIRD-PARTY.md.

⸻

Acknowledgements

Built with ❤ by {{org_name}}.
Animica VM & SDK tooling power the deterministic Python contract experience.

