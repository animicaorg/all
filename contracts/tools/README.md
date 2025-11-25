# contracts/tools

Developer-facing tools for building, linting, packaging, deploying, calling, and verifying Animica Python contracts (the deterministic **vm_py** runtime). Everything here is designed to be **repeatable**, **scriptable**, and friendly to CI.

> TL;DR:
> - **Build** → IR + code-hash + manifest → package artifact in `contracts/build/`
> - **Deploy** → send signed CBOR tx to your node (or studio-services proxy)
> - **Call** → read-only simulation or write call with gas/fees
> - **Verify** → recompile source and match on-chain code hash
> - **Lint** → enforce deterministic Python subset rules
> - **Fixtures** → tiny utilities used across tools

The tools are simple Python CLIs; you can run them with `python -m ...` or `python path/to/script.py`.

---

## Prerequisites

- Python **3.10+** (recommended 3.11+)
- Install repo-local tool deps:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r contracts/requirements.txt

	•	A running node (RPC URL), or a devnet via the provided compose/k8s/helm profiles.
	•	For deployments, a funded dev account mnemonic (see tests/devnet/seed_wallets.json).

⸻

Environment & Defaults

Most CLIs accept flags for all parameters, and also read optional environment variables:
	•	RPC_URL – JSON-RPC endpoint of your node (e.g. http://127.0.0.1:8545)
	•	CHAIN_ID – numeric chain id (e.g. 1 for mainnet, 1337 for devnet)
	•	DEPLOYER_MNEMONIC – 12/24-word mnemonic for deploy/sign (devnets only)
	•	SERVICES_URL – studio-services base URL (e.g. http://127.0.0.1:8787)
	•	ANIMICA_REPO_ROOT – manual override for repo root discovery (rare)
	•	ANIMICA_DETERMINISTIC=1 – enable extra determinism guards in some tools

You can copy and edit contracts/.env.example, then export:

set -a; . contracts/.env.example; set +a


⸻

What’s in this directory?
	•	build_package.py — compile & package a contract:
	•	Reads Python source + manifest
	•	Produces IR bytes, computes code hash, writes a package directory with:
	•	*.ir — canonical IR
	•	*.code.bin — compiled bytes (if applicable)
	•	*.abi.json — ABI (either from source via abi_gen or from manifest)
	•	*.manifest.json — normalized manifest (with code_hash filled)
	•	*.pkg.json — machine-readable package descriptor (pointer to all of the above)
	•	abi_gen.py — generate ABI JSON from source (docstrings/decorators).
	•	deploy.py — deploy a compiled package to the chain via RPC (or studio-services relay).
	•	call.py — call contract functions (read-only or write). Encodes args, manages sign bytes, decodes return.
	•	verify.py — recompile & ensure code_hash(source) == on_chain_code_hash(address). Optionally uses studio-services.
	•	lint_contract.py — static checks against the deterministic Python subset rules.
	•	fixtures.py — small helpers and sample payloads used by tools/tests:
	•	Repo root detection, canonical JSON writer, deterministic byte generator
	•	Minimal ABIs & manifests for Counter and Escrow
	•	Sample AI/Quantum job payloads; DA blob generator
	•	Seed wallet loader (devnet) and bech32-ish sample addresses

All tools strive to print structured JSON on success (to stdout) and human logs to stderr. Add --help to any command for the authoritative flags.

⸻

Quickstart: Build → Deploy → Call → Verify

This example uses the canonical Counter from tests/fixtures/contracts/counter/.

1) Build a package

python -m contracts.tools.build_package \
  --source tests/fixtures/contracts/counter/contract.py \
  --manifest tests/fixtures/contracts/counter/manifest.json \
  --out-dir contracts/build/counter

Outputs (example):

contracts/build/counter/
  Counter.ir
  Counter.code.bin
  Counter.abi.json
  Counter.manifest.json     # code_hash filled in
  Counter.pkg.json          # package descriptor

Code hash is the canonical SHA3-512 over the IR bytes (and/or code bundle) as specified in spec/manifest.schema.json.

2) Deploy the package

export RPC_URL=http://127.0.0.1:8545
export CHAIN_ID=1337
export DEPLOYER_MNEMONIC="abandon abandon ..."

python -m contracts.tools.deploy \
  --package contracts/build/counter/Counter.pkg.json

On success, the tool prints JSON:

{
  "txHash": "0x...",
  "address": "anim1xyz...",
  "receipt": {"status":"SUCCESS","gasUsed":12345}
}

The deploy tool will:
	•	Build sign-bytes from package manifest/IR
	•	Sign using your PQ key (derived from mnemonic)
	•	Submit via RPC (or services relay if configured)
	•	Poll for inclusion and emit the receipt

3) Call methods

Increment:

python -m contracts.tools.call \
  --address anim1xyz... \
  --abi contracts/build/counter/Counter.abi.json \
  --method inc \
  --args '{"n": 1}'

Get value (read-only):

python -m contracts.tools.call \
  --address anim1xyz... \
  --abi contracts/build/counter/Counter.abi.json \
  --method get \
  --read-only

Read-only calls simulate execution without state writes, using the node’s execution/simulation path.

4) Verify source ↔ on-chain code hash

export SERVICES_URL=http://127.0.0.1:8787

python -m contracts.tools.verify \
  --address anim1xyz... \
  --source tests/fixtures/contracts/counter/contract.py \
  --manifest contracts/build/counter/Counter.manifest.json

This will re-build IR (using the same canonical pipeline) and compare the resulting code_hash with the chain’s stored value at that address (or through studio-services’ verify endpoint). It emits a JSON result with match: true/false plus diagnostics.

⸻

ABI generation from source

If you want to derive ABI from your Python source (docstrings or simple decorators), run:

python -m contracts.tools.abi_gen \
  --source path/to/contract.py \
  --out contracts/build/MyContract.abi.json

The generator adheres to contracts/schemas/abi.schema.json (kept in sync with spec/abi.schema.json). See examples in the tests/fixtures/contracts/* tree.

⸻

Determinism & Reproducibility
	•	The builder enforces canonical JSON and stable ordering for IR and manifests.
	•	Hashing uses SHA3-512 per spec; packages embed the computed code_hash.
	•	The linter (lint_contract.py) enforces the deterministic Python subset documented in contracts/CODESTYLE.md (no I/O, no time, banned imports, numeric limits, etc.).
	•	Set ANIMICA_DETERMINISTIC=1 to enable stricter guards and fixed seeds in certain helper paths.
	•	Build outputs are designed to be byte-for-byte reproducible across machines and CI, provided identical inputs and versions.

⸻

Integration with Makefile

Common workflows are wired in contracts/Makefile:

# Lint all contracts
make lint

# Build the sample 'counter' package
make build EX=counter

# Deploy to testnet/devnet (reads RPC_URL/CHAIN_ID/MNEMONIC)
make testnet-deploy EX=counter


⸻

Exit codes & Logging
	•	0 — success; JSON payload printed to stdout
	•	1 — user/input error (bad path, invalid manifest)
	•	2 — network/RPC error
	•	3 — verification mismatch (verify tool)
	•	>=10 — unexpected/internal error

Logs go to stderr. If you’re scripting, consume stdout only.

⸻

Artifacts & Layout

By default, build artifacts land under contracts/build/<name>/:
	•	*.ir — canonical IR bytes for vm_py
	•	*.code.bin — code bundle if present
	•	*.abi.json — ABI schema
	•	*.manifest.json — normalized manifest (with code_hash)
	•	*.pkg.json — package descriptor (paths, code hash, meta)

The tools never write into tests/ or other source directories.

⸻

Troubleshooting
	•	“Could not detect repo root” — set ANIMICA_REPO_ROOT=/absolute/path/to/repo
	•	“Invalid address” — make sure you use animica bech32m addresses (e.g., anim1...). The SDK validates strictly.
	•	Deploy stuck — verify your node is advancing (rpc/chain.getHead), and that the account has funds (use studio-services faucet if provided).
	•	Verify mismatch — ensure you’re compiling the exact source and manifest that were used for deployment; any difference in ABI or IR changes the code_hash.

⸻

References
	•	spec/ — schemas: abi.schema.json, manifest.schema.json, OpenRPC surface, VM opcodes/gas
	•	vm_py/ — validator, compiler/IR, runtime, stdlib
	•	sdk/ — Python/TypeScript/Rust SDKs (deployment/calls/events)
	•	studio-services/ — verification proxy & deployment relay (no server-side signing)
	•	tests/ — fixtures for counter/escrow, end-to-end flows

⸻

License & Security

These tools are for developer workflows and CI. They do not provide a security audit of your contracts. Read contracts/SECURITY.md and the VM determinism rules (contracts/CODESTYLE.md) before deploying to public networks.

