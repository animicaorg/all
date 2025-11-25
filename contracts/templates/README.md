# contracts/templates

Curated, **production-leaning** starter templates for Animica Python smart contracts.
Each template is a tiny, audited seed that compiles with the deterministic VM, ships an
ABI+manifest, and includes local unit tests you can run entirely off-chain with the VM.

This directory is meant to be copied into your own repo (or cherry-picked per template).
It mirrors the examples shipped elsewhere in this monorepo but is structured specifically
for quick scaffolding in contract projects.

---

## What you get

Each template contains:

- `contract.py` — a minimal, deterministic Python contract using `vm_py` stdlib.
- `manifest.json` — ABI + metadata; stable code hash once compiled.
- `README.md` — contract-specific notes and usage.
- `tests_local.py` — local unit tests (run on the VM interpreter, no node needed).

Templates are intentionally small (≈50–150 LOC) and follow the same style/lint rules as
the rest of the repo (see **CODESTYLE.md**).

---

## Folder layout & conventions

contracts/
templates/
token/
contract.py
manifest.json
README.md
tests_local.py
escrow/
contract.py
manifest.json
README.md
tests_local.py
ai_agent/
contract.py
manifest.json
README.md
tests_local.py
quantum_rng/
contract.py
manifest.json
README.md
tests_local.py
registry/
contract.py
manifest.json
README.md
tests_local.py
oracle/
contract.py
manifest.json
README.md
tests_local.py
multisig/
contract.py
manifest.json
README.md
tests_local.py

**Naming:** each template directory name is the default package name and on-chain label
used by provided scripts, unless overridden.

---

## Placeholders & parameterization

Templates are written to work **as-is** but can be parameterized by simple find/replace
if you want your own names/IDs. We deliberately use plain text placeholders so you can
customize with any tool (no scaffolding engine required):

- `{{PROJECT_NAME}}` — human-readable name in `manifest.json`.
- `{{SYMBOL}}` — token symbol or short name (where applicable).
- `{{CONTRACT_NAME}}` — class/name shown in ABI docs.
- `{{OWNER_ADDRESS}}` — optional bootstrap owner for Ownable/Multisig templates.

> Tip: Keep placeholder changes deterministic (no current time, no random salts) to
> preserve stable code hashes across builds.

Example quick replace:

```bash
# Customize placeholders safely (GNU sed shown)
sed -i "s/{{PROJECT_NAME}}/AcmeToken/g" contracts/templates/token/manifest.json
sed -i "s/{{SYMBOL}}/ACME/g"          contracts/templates/token/manifest.json


⸻

Deterministic Python subset (required reading)

Contracts must obey the constrained Python subset enforced by vm_py:
	•	No I/O, networking, wall-clock time, or randomness (use provided stdlib APIs).
	•	Only whitelisted builtins; bounded numeric behaviors; explicit gas usage.
	•	Imports limited to stdlib surface provided by the VM.

See: contracts/CODESTYLE.md and vm_py/specs/DETERMINISM.md.

Run the linter to catch violations early:

# From repo root (or contracts/)
python -m contracts.tools.lint_contract path/to/contract.py


⸻

Build → Test → Deploy → Verify (end-to-end)

All templates support the same pipeline using the included tools.

1) Compile & package

# From repo root (or contracts/)
python -m contracts.tools.build_package \
  --src contracts/templates/token/contract.py \
  --manifest contracts/templates/token/manifest.json \
  --out contracts/build/token.pkg.json

This produces a canonical bundle with fields like:

{
  "manifest": { "...": "..." },
  "code": "0x...",                // hex of compiled IR/bytecode
  "code_hash": "0x..."            // sha3-256(code)
}

2) Local unit tests (VM only)

pytest -q contracts/templates/token/tests_local.py

These tests run in pure Python (no node), executing the interpreter and stdlib.

3) Deploy to a node (devnet/testnet)

Prepare environment (see contracts/.env.example):

export RPC_URL=http://127.0.0.1:8545
export CHAIN_ID=1337
export DEPLOYER_MNEMONIC="test test test ... test"

Deploy:

python -m contracts.tools.deploy \
  --package contracts/build/token.pkg.json \
  --gas 1500000

4) Call / interact

python -m contracts.tools.call \
  --address anim1... \
  --abi contracts/templates/token/manifest.json \
  --method balanceOf \
  --args '{"owner":"anim1..."}'

5) Verify source ↔ on-chain code hash

If you run studio-services, you can register a verification:

python -m contracts.tools.verify \
  --services http://127.0.0.1:8787 \
  --address anim1... \
  --src contracts/templates/token/contract.py \
  --manifest contracts/templates/token/manifest.json


⸻

Using the SDK directly

All templates work with omni_sdk (Python/TS/Rust). Python example:

from omni_sdk.rpc.http import HttpClient
from omni_sdk.contracts.client import ContractClient

rpc = HttpClient("http://127.0.0.1:8545", chain_id=1337)
abi = json.load(open("contracts/templates/token/manifest.json"))["abi"]
c = ContractClient(rpc, abi=abi, address="anim1...")

print(c.call("name", {}))
print(c.call("balanceOf", {"owner": "anim1..."}))


⸻

Template catalogue

token/ — Animica-20 fungible token
	•	Deterministic balance/allowance model, Transfer & Approval events.
	•	Optional mintable or permit patterns available in stdlib; keep supply logic explicit.

escrow/ — basic escrow with disputes
	•	Funds escrowed, beneficiary release, and dispute paths; logs all intents.
	•	Deterministic time model (no wall-clock) — use block height or randomness beacon if needed.

ai_agent/ — AICF integration
	•	Enqueue an AI job, store task_id, consume the result next block via capabilities.
	•	Demonstrates deterministic result consumption window.

quantum_rng/ — quantum bytes mixed with beacon
	•	Mixes quantum-proof receipt with randomness beacon using extract-then-xor.
	•	Clear transcript guarantees for auditability.

registry/ — name ↔ address mapping
	•	Simple registry with events; shows pattern for admin controls (Ownable/roles).

oracle/ — DA-backed value feed
	•	Accepts a DA blob commitment and binds a value to it; consumers can read the latest.
	•	Showcases DA commitment validation and provenance.

multisig/ — N-of-M with PQ-aware permits
	•	Threshold approvals with canonical SignBytes design (domain separation included).
	•	Demonstrates structured approvals and replay protection via nonces/expiry.

⸻

Recommended workflow
	1.	Copy a template into your project repo:

mkdir -p my-dapp/contracts
rsync -a contracts/templates/token/ my-dapp/contracts/token/


	2.	Edit placeholders, then run lint:

sed -i 's/{{PROJECT_NAME}}/AcmeToken/g' my-dapp/contracts/token/manifest.json
python -m contracts.tools.lint_contract my-dapp/contracts/token/contract.py


	3.	Compile & test locally:

python -m contracts.tools.build_package \
  --src my-dapp/contracts/token/contract.py \
  --manifest my-dapp/contracts/token/manifest.json \
  --out my-dapp/contracts/build/token.pkg.json

pytest -q my-dapp/contracts/token/tests_local.py


	4.	Deploy to your devnet/testnet and verify:

python -m contracts.tools.deploy --package my-dapp/contracts/build/token.pkg.json
python -m contracts.tools.verify --address anim1... \
  --src my-dapp/contracts/token/contract.py \
  --manifest my-dapp/contracts/token/manifest.json



⸻

Lint, typecheck, and determinism guards

Use the preconfigured tools:

# Lint & determinism checks
python -m contracts.tools.lint_contract contracts/templates/token/contract.py

# Optional: ruff/mypy if you enabled local tooling (see pyproject.toml)
ruff check contracts/templates/token/contract.py
mypy contracts/templates/token/contract.py


⸻

ABI generation (optional)

If you prefer to derive ABI from docstrings/decorators:

python -m contracts.tools.abi_gen \
  --src contracts/templates/token/contract.py \
  --out contracts/templates/token/manifest.json

Always re-build the package after ABI changes to keep manifest/code hash aligned.

⸻

Security considerations
	•	Keep domain separation in any off-chain signed messages (see multisig template).
	•	Emit events for all state-changing operations; prefer explicit, minimal events.
	•	Avoid unbounded loops over user-provided arrays; keep gas upper bounds predictable.
	•	Use safe_uint helpers for arithmetic you intend to saturate/guard.

⸻

Troubleshooting
	•	“ImportError: vm_py not found” — install the VM package or use repo editable:
pip install -e . at the repo root (ensures contracts.tools is importable).
	•	“ChainId mismatch” — set CHAIN_ID to match the node (chain.getChainId).
	•	“Verify failed” — ensure you compiled with the same toolchain and determinism flags;
re-build package and compare code_hash.

⸻

See also
	•	contracts/README.md — project-level guidance, environment setup.
	•	contracts/tools/*.py — CLI helpers used above (build/deploy/call/verify).
	•	vm_py/specs/* — determinism, IR, ABI, gas model.
	•	studio-web — in-browser compile/simulate; great for quick iteration.

