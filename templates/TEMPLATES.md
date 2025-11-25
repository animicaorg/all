# Templates — Catalog & Playbook

A single, authoritative reference for all first-party templates shipped in this repo. If you’re about to start a new contract or example, start here. This doc explains **what each template is for**, **how to scaffold and run it**, and **how to keep it compatible** with the chain spec, VM, and SDKs.

> If you only need a quick start:
>
> 1) Pick a template from **contracts/templates/**  
> 2) Copy it into your app and rename identifiers  
> 3) Build a package → deploy → verify (commands below)

---

## 0) Where templates live (map)

- **Contracts (Python-VM)** → `contracts/templates/*`
  - `counter/` — smallest stateful example (inc/get)
  - `escrow/` — time/phase-driven escrow with events
  - `ai_agent/` — enqueues AI work via capabilities; consumes result next block
  - `quantum_rng/` — mixes randomness beacon with quantum bytes receipts

- **Studio (browser simulator)** → `studio-wasm/examples/*`
  - Mirrors of `counter/` and `escrow/` for in-browser run/inspect

- **Services fixtures** → `studio-services/fixtures/*`
  - Reference artifacts for deploy/verify tests (used by CI)

- **End-to-end examples** → `contracts/examples/*`
  - Full demos (deploy scripts, local tests) that inform how templates should be used

---

## 1) Template matrix (purpose, storage, ABI, events, deps)

> Use this as a “choose the right scaffold” guide.

### A. `counter/`
- **Use when**: you want the smallest stateful demo of the Python-VM (storage + event + ABI).
- **Storage layout**:
  - `b"count" -> int` (monotonic increment)
- **ABI**:
  - `inc() -> None`
  - `get() -> {"value": int}`
- **Events**:
  - `CounterIncremented(value: int)`
- **Determinism notes**: no external syscalls; ideal for gas/receipt regression tests.
- **Typical next steps**: add access control (ownable/roles) from stdlib; wire into a UI.

### B. `escrow/`
- **Use when**: you need funds held under a simple life-cycle with dispute windows.
- **Storage layout** (illustrative keys):
  - `b"payer" -> address`
  - `b"payee" -> address`
  - `b"amount" -> uint`
  - `b"state" -> int` (enum: INIT, FUNDED, RELEASED, REFUNDED, DISPUTED)
  - `b"deadline" -> int` (block height or beacon time proxy)
- **ABI**:
  - `fund(amount: int) -> None`
  - `release() -> None`
  - `refund() -> None`
  - `dispute() -> None`
  - `status() -> {"state": int, "amount": int}`
- **Events**:
  - `EscrowFunded(amount)`
  - `EscrowReleased(amount)`
  - `EscrowRefunded(amount)`
  - `EscrowDisputed()`
- **Determinism notes**: uses deterministic block context; no wall-clock.
- **Extensions**: add pausability/roles; integrate with `treasury` stdlib.

### C. `ai_agent/`
- **Use when**: contracts should **request AI compute now** and **consume the result next block**.
- **Storage layout**:
  - `b"last_task" -> bytes` (task id)
  - `b"last_result" -> bytes` (opaque result slice/summary)
- **ABI**:
  - `enqueue(model: bytes, prompt: bytes) -> {"task": bytes}`
  - `consume(task: bytes) -> {"ok": bool, "result": bytes}`
- **Events**:
  - `AIEnqueued(task, model)`
  - `AIConsumed(task, ok)`
- **Dependencies**: `capabilities` host bindings (AI), enforced size caps/fees.
- **Determinism notes**: the task id is derived from on-chain inputs (height|tx|caller|payload), ensuring replay safety.

### D. `quantum_rng/`
- **Use when**: you want to mix **beacon randomness** with **quantum bytes receipt** for higher entropy.
- **Storage layout**:
  - `b"seed" -> bytes32`
  - `b"last_mix" -> bytes32`
- **ABI**:
  - `mix(commitment: bytes, receipt: bytes) -> {"mix": bytes32}`
  - `get() -> {"mix": bytes32}`
- **Events**:
  - `QuantumMixed(mix)`
- **Dependencies**: capabilities quantum receipt format; randomness/beacon adapter.
- **Determinism notes**: pure extract-then-xor with transcript binding; stable across nodes.

---

## 2) Scaffold → build → deploy → verify (canonical flow)

> Assumes Python 3.10+, `contracts/requirements.txt` installed, and environment variables set:
> `RPC_URL`, `CHAIN_ID`, `DEPLOYER_MNEMONIC`.

### 2.1 Scaffold from a template

```bash
# Example: start a new app from counter
mkdir -p my_dapps/mycounter
rsync -av --exclude '__pycache__' --exclude '.DS_Store' \
  contracts/templates/counter/ my_dapps/mycounter/

# Optionally rename symbols (class names, events)
grep -RIn 'Counter' my_dapps/mycounter
# sed -i 's/Counter/MyCounter/g' my_dapps/mycounter/contract.py

2.2 Build a deployable package (manifest + code hash)

python -m venv .venv && . .venv/bin/activate
pip install -r contracts/requirements.txt

python -m contracts.tools.build_package \
  --source my_dapps/mycounter/contract.py \
  --manifest my_dapps/mycounter/manifest.json \
  --out contracts/build/mycounter.pkg.json

Artifacts:
	•	contracts/build/*.pkg.json — single-file package (code hash, ABI, metadata)
	•	tests/artifacts/ — CI will stash build outputs (via collectors)

2.3 Deploy (SDK or studio-services)

Direct via SDK/CLI:

python -m contracts.tools.deploy \
  --package contracts/build/mycounter.pkg.json \
  --rpc "$RPC_URL" --chain-id "$CHAIN_ID" \
  --mnemonic "$DEPLOYER_MNEMONIC"

Via services (if running studio-services):

curl -sS -X POST "$SERVICES_URL/deploy" \
  -H 'content-type: application/json' \
  --data-binary @contracts/build/mycounter.pkg.json

2.4 Interact (read/write)

# Write (inc)
python -m contracts.tools.call \
  --address <deployed_addr> \
  --abi my_dapps/mycounter/manifest.json \
  --func inc --args '{}'

# Read (get)
python -m contracts.tools.call \
  --address <deployed_addr> \
  --abi my_dapps/mycounter/manifest.json \
  --func get --args '{}'

2.5 Verify (source ↔ on-chain code hash)

python -m contracts.tools.verify \
  --package contracts/build/mycounter.pkg.json \
  --source my_dapps/mycounter/contract.py \
  --manifest my_dapps/mycounter/manifest.json \
  --services-url "$SERVICES_URL"


⸻

3) Compatibility & versioning (keep things in sync)

Templates are coupled to the following moving parts:
	•	ABI Schema → spec/abi.schema.json (mirrored under contracts/schemas/abi.schema.json)
	•	VM Gas Table → spec/opcodes_vm_py.yaml (resolved into vm_py/gas_table.json)
	•	OpenRPC → spec/openrpc.json (SDK codegen & services client)
	•	Chain Params → spec/params.yaml (gas limits, Θ/Γ hooks may affect deploy size/fees)
	•	Randomness/DA policies → if your template consumes these, pin versions in docs

Best practice:
	•	Re-build templates after bumps to ABI or gas tables.
	•	Run make -C contracts lint and all example tests:

pytest -q contracts/examples/*/tests_local.py



⸻

4) Determinism, security, and style
	•	Determinism: follow vm_py/specs/DETERMINISM.md. No IO, no global clock, no nondeterministic libs.
	•	Style & Safety:
	•	contracts/CODESTYLE.md — Python subset, forbidden imports, resource bounds
	•	contracts/SECURITY.md — invariants checklists (reentrancy not applicable, but state ordering & event emission are)
	•	Lint:

make -C contracts lint



⸻

5) Test guidance (local & CI)
	•	Local unit: every template has or can copy a tests_local.py that:
	•	builds a tiny VM state
	•	runs positive/negative calls
	•	asserts logs/events and gas bounds
	•	Property tests (optional): if you generalize a template (e.g., token supply rules), consider adding hypothesis-based props under tests/property/.
	•	Integration: use devnet harness under tests/devnet/ to validate deploy + call flows in a realistic environment.

Example:

pytest -q contracts/examples/counter/tests_local.py


⸻

6) Capability-aware templates (AI / Quantum / DA / Randomness)

If your template needs off-chain capabilities:
	•	AI: use contracts/stdlib/capabilities/ai_compute.py (enqueue → id → next-block consume)
	•	Quantum: contracts/stdlib/capabilities/quantum.py (traps receipts; verify & mix)
	•	DA: contracts/stdlib/capabilities/da_blob.py (pin & store commitment)
	•	Randomness: contracts/stdlib/capabilities/randomness.py (read beacon; commit/reveal helper)
	•	ZK Verify: contracts/stdlib/capabilities/zkverify.py (returns bool + units; emits event)

Keep input sizes small (gas & determinism), and rely on receipts not raw outputs where possible.

⸻

7) Adding a new template (maintainer checklist)
	1.	Create contracts/templates/<name>/contract.py and manifest.json.
	2.	Validate the manifest against schema:

python -m contracts.tools.abi_gen --check manifest mypath/manifest.json


	3.	Add a minimal example under contracts/examples/<name>/ with:
	•	README.md, tests_local.py, deploy_and_test.py
	4.	Ensure determinism & style:

make -C contracts lint


	5.	Run example tests and fast CI:

pytest -q contracts/examples/<name>/tests_local.py
python tests/ci/run_fast_suite.py


	6.	Update this doc’s matrix with purpose, storage, ABI, events, and deps.
	7.	If capabilities are used, extend contracts/docs/CAPABILITIES.md.

⸻

8) Troubleshooting
	•	“Manifest validation failed”
Ensure it matches contracts/schemas/manifest.schema.json. Check function names, arg types, event field names.
	•	“OOG (Out-Of-Gas) during inc()”
Lower gas estimate or update the gas table; inspect per-instruction costs in vm_py/gas_table.json.
	•	“Verify mismatch”
Rebuild the package. Any change in source bytes changes the code hash.
	•	“No result for AI task”
Remember: consumption is next block. Use the consume(task) ABI after the following block is finalized in devnet.
	•	“Non-deterministic behavior”
Check for banned imports (e.g., random, time, os). Run the validator if unsure.

⸻

9) FAQ

Q: Can templates import each other?
A: Prefer copying patterns; contracts are single-file for auditability and to keep code hash derivation trivial.

Q: How do I expose new events?
A: Define them consistently and document in the manifest; use contracts/stdlib/utils/events.py helpers where applicable.

Q: Do templates support upgrades?
A: See contracts/stdlib/upgrade/proxy.py and contracts/docs/PATTERNS.md (pin code hash!).

⸻

10) References
	•	Specs: spec/abi.schema.json, spec/opcodes_vm_py.yaml, spec/openrpc.json
	•	VM: vm_py/specs/*, vm_py/runtime/*
	•	Docs: contracts/docs/*
	•	Tools: contracts/tools/* (build_package.py, deploy.py, call.py, verify.py)

⸻

Happy shipping 👋 — if you build a new template that’s broadly useful, contribute it back with tests and a short README so it stays green in CI.
