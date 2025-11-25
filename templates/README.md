# Templates

This directory is a **map** to the ready-to-use scaffolds that live across the repo. It explains what’s available, when to use each template, and the quickest way to copy one into a new project and ship something that runs end-to-end on the Animica devnet.

> TL;DR — If you want a new contract fast:
> - Start from a template in `contracts/templates/`
> - Build it with the local toolchain
> - Deploy with the SDK or studio-services
> - Verify the code hash and iterate

---

## What lives where

We purposely colocate templates with the systems they target (so examples/tests always compile and stay green). Here’s the index:

### Contracts (Python-VM)
Location: `contracts/templates/`

- **counter/** — Minimal stateful example (increment/get), ideal for “hello world” and ABI/event wiring.
- **escrow/** — Deterministic escrow with deposits, dispute window, and deterministic “clock” usage.
- **ai_agent/** — Contract that enqueues an AI job via capabilities and consumes the result on the next block.
- **quantum_rng/** — Demonstrates mixing the beacon with quantum bytes receipts for randomness-heavy apps.

Each subfolder contains:
- `contract.py` — Deterministic Python subset (enforced by `contracts/CODESTYLE.md`)
- `manifest.json` — ABI + metadata (validated by `contracts/schemas/manifest.schema.json`)

### Studio WASM (browser simulator)
Location: `studio-wasm/examples/`

- **counter/**, **escrow/** — Mirror of the above for the in-browser simulator; great for tutorials and docs.

### Services (deploy/verify/faucet)
Location: `studio-services/fixtures/`

- **counter/** — Reference artifacts (contract + manifest) used by automated verification tests.

---

## Quickstart: scaffold a new contract from a template

> Requires Python 3.10+ and the repo’s contract tooling (see `contracts/requirements.txt`).

1) **Pick a template and copy it**

```bash
# Example: start a fresh "mycounter" contract from the counter template
mkdir -p my_dapps/mycounter
rsync -av --exclude '__pycache__' --exclude '.DS_Store' \
  contracts/templates/counter/ my_dapps/mycounter/

	2.	Rename symbols (optional)
The templates use human-readable names like Counter. You can search & replace:

# Replace class and event names in your copy, if you want different identifiers
grep -RIn 'Counter' my_dapps/mycounter
# Example replacement:
# sed -i 's/Counter/MyCounter/g' my_dapps/mycounter/contract.py

	3.	Build a deployable package
From repo root:

python -m venv .venv && . .venv/bin/activate
pip install -r contracts/requirements.txt

python -m contracts.tools.build_package \
  --source my_dapps/mycounter/contract.py \
  --manifest my_dapps/mycounter/manifest.json \
  --out contracts/build/mycounter.pkg.json

This produces a single JSON package with code hash, ABI, and metadata at contracts/build/….
	4.	Deploy to devnet and call
Using the Python SDK helper:

python -m contracts.tools.deploy \
  --package contracts/build/mycounter.pkg.json \
  --rpc $RPC_URL --chain-id $CHAIN_ID \
  --mnemonic "$DEPLOYER_MNEMONIC"

Call methods (read & write):

python -m contracts.tools.call \
  --address <deployed_address> \
  --abi my_dapps/mycounter/manifest.json \
  --func inc --args '{}'

python -m contracts.tools.call \
  --address <deployed_address> \
  --abi my_dapps/mycounter/manifest.json \
  --func get --args '{}'

	5.	Verify the source (optional but recommended)

python -m contracts.tools.verify \
  --package contracts/build/mycounter.pkg.json \
  --source my_dapps/mycounter/contract.py \
  --manifest my_dapps/mycounter/manifest.json \
  --services-url $SERVICES_URL


⸻

When to choose each template
	•	counter — Smallest working example with events and state. Start here if you’re learning the toolchain or testing CI wiring.
	•	escrow — If your dapp moves funds between parties and needs explicit life-cycle transitions plus pausability hooks.
	•	ai_agent — For apps that offload AI tasks (summaries, embeddings) and need deterministic next-block result consumption.
	•	quantum_rng — For randomness-sensitive protocols (lotteries, games) that want to mix the beacon with quantum entropy receipts.

⸻

Determinism & contract rules

All templates follow the deterministic subset described in:
	•	contracts/CODESTYLE.md — allowed Python features & standard patterns
	•	contracts/SECURITY.md — audit checklist and invariants
	•	vm_py/specs/DETERMINISM.md — VM surface area and banned operations

Use the linter before committing:

make -C contracts lint


⸻

Testing locally

The templates are compatible with the local VM harness:

pytest -q contracts/examples/*/tests_local.py

For your own copy:

pytest -q my_dapps/mycounter/tests_local.py

(If you derive from a template, copy one of the tests_local.py from contracts/examples/… and adjust imports/ABI.)

⸻

Adding a new template
	1.	Create a new folder under contracts/templates/<name>/.
	2.	Include at minimum:
	•	contract.py (deterministic, with docstring comments that describe ABI functions)
	•	manifest.json (validated against contracts/schemas/manifest.schema.json)
	3.	Provide a focused README in the corresponding example (under contracts/examples/<name>/) and a tests_local.py with basic positive/negative cases.
	4.	Run:
	•	make -C contracts lint
	•	pytest -q contracts/examples/<name>/tests_local.py
	5.	(Optional) Wire an end-to-end deploy script mirroring deploy_and_test.py from other examples.

⸻

CI expectations
	•	Templates must compile and run under the property/integration suites.
	•	Changing a template should not break tests/examples or devnet e2e gates.
	•	If a template introduces new syscalls/capabilities, update the docs in contracts/docs/CAPABILITIES.md and add a minimal integration test.

⸻

FAQ

Q: What’s the difference between contracts/templates and contracts/examples?
Templates are opinionated starting points. Examples are fully-wired demos used by tutorials and CI; they may include extra tests and deploy scripts.

Q: Can I use studio-web instead of the CLI to try a template?
Yes. Open studio-web, paste the template contract.py, import the manifest.json, simulate calls, then deploy via wallet.

Q: How do I keep my template in sync with gas table or ABI schema changes?
Watch commits to spec/opcodes_vm_py.yaml and spec/abi.schema.json. Rebuild and run the contracts test suite.

⸻

License

Templates are provided under the repository’s main license. See LICENSE at the repo root. Third-party notices (if any) are recorded alongside example assets.

