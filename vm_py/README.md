# Animica Python VM (vm_py)

Deterministic, resource-bounded Python-derived virtual machine for Animica smart contracts.  
Contracts are authored in a strict subset of Python, compiled to a small IR, and executed by a gas-metered interpreter. The VM is designed to be **portable, reproducible, and auditable**—no ambient I/O, no wall clock, no randomness unless deterministically seeded.

---

## Why a Python VM?

- **Approachable**: familiar syntax; great tooling.
- **Deterministic**: strict subset + sandboxed runtime.
- **Spec-first**: opcodes, ABI, and gas are specified (see `spec/`), with golden vectors and tests.
- **Post-quantum ready**: the rest of the stack (addresses, signatures, handshake) is PQ by default; the VM provides domain-separated hashing and deterministic bytes I/O.

---

## Determinism & Safety Guarantees

The VM and validator enforce the following:

- **No ambient I/O**: no filesystem, network, env, time, threads, FFI.
- **Pure compute + host shims** only via a narrow **stdlib**: `storage`, `events`, `hash`, `abi`, `treasury`, `syscalls` (capability stubs). These are deterministic and gas-metered.
- **Type discipline**: integers (unbounded, but capped by gas), `bytes`, `bool`. No floats. Bounded containers in the IR.
- **Opcode allowlist**: specified in `spec/opcodes_vm_py.yaml`, resolved into `vm_py/gas_table.json`.
- **Gas metering**: every instruction and stdlib call charges gas; refunds exist only where explicitly specified.
- **Resource limits**: maximum code size, call depth, loop/IR instruction counts, topic/data sizes for events.
- **Deterministic PRNG**: available through stdlib `random` shim, seeded from tx hash + call index (for tests).
- **Domain separation**: hashing uses explicit domains/personalization strings throughout.
- **Stable encoding**: IR and ABI use deterministic (length-prefixed) encodings; CBOR rules match `spec/tx_format.cddl`.

If any rule would be violated at runtime, execution halts with a deterministic error (`OOG`, `Revert`, or `ValidationError`).

---

## On-disk Layout (high level)

- `vm_py/validate.py` — static validator for the Python subset.
- `vm_py/compiler/*` — AST → IR lowering, encoding/decoding, gas estimator.
- `vm_py/runtime/*` — interpreter, gas meter, host stdlib shims, ABI dispatcher.
- `vm_py/stdlib/*` — the import surface available to contracts.
- `vm_py/cli/*` — command-line tools (`compile`, `run`, `inspect_ir`).
- `vm_py/examples/*` — sample contracts (`counter`, `escrow`) + manifests.
- `vm_py/tests/*` — unit tests and golden vectors.

---

## Quickstart: run the Counter example

> Prereqs: Python 3.11+, a virtualenv, and repo root on your machine.

```bash
# 1) Activate your venv and install editable deps (msgspec/cbor2 used by encoder)
pip install -e .

# 2) Inspect the example manifest & contract
ls vm_py/examples/counter/
# contract.py, manifest.json

# 3) (Optional) Compile to IR and inspect it
python -m vm_py.cli.compile vm_py/examples/counter/contract.py --out /tmp/counter.ir
python -m vm_py.cli.inspect_ir /tmp/counter.ir

# 4) Run a read-only call (no state write)
python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call get
# → prints JSON result: {"ok": true, "return": 0, "gasUsed": ...}

# 5) Run a state-changing call (increment), then read again
python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call inc \
  --args '{}'

python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call get
# → returns 1 (and increases deterministically if run repeatedly in same ephemeral state)

Notes
	•	The CLI maintains an in-process ephemeral state for convenience in quickstarts; node integration uses the execution adapter and persisted state DB.
	•	--args expects JSON that matches the contract ABI schema in the manifest. For byte strings, pass hex like "0xdeadbeef".

⸻

Authoring Contracts
	•	Import only from stdlib:

from stdlib import storage, events, abi, hash, treasury, syscalls

def inc():
    # load, update, store
    v = storage.get_int(b"counter", default=0)
    v = v + 1
    storage.set_int(b"counter", v)
    events.emit(b"Inc", {"value": v})
    return v

	•	No global mutable state outside of storage APIs.
	•	No recursion without an explicit depth cap (enforced at validate time).
	•	Inputs/outputs are ABI-encoded scalars/bytes/address types (see vm_py/specs/ABI.md).

Run python -m vm_py.cli.compile your_contract.py --out your_contract.ir to validate+compile.

⸻

Gas Model
	•	The gas table is resolved from spec/opcodes_vm_py.yaml at build time into vm_py/gas_table.json.
	•	Static upper-bound estimates are available via compiler/gas_estimator.py and surfaced in the CLI and IDE (studio-wasm).
	•	Runtime metering uses runtime/gasmeter.py with saturating add/sub; OOG halts with a deterministic receipt.

⸻

Integration Points
	•	Execution layer: execution/runtime/contracts.py and execution/adapters/vm_entry.py bridge tx calls to the VM (deterministic state, receipts, logs).
	•	RPC: contract call simulation and event decoding are provided by SDKs and, optionally, studio-services.
	•	WASM: studio-wasm/ packages a trimmed VM for in-browser compile & simulate experiences.

⸻

Testing

pytest -q vm_py/tests
# Or run the specific counter test:
pytest -q vm_py/tests/test_runtime_counter.py::test_inc_and_get

The suite covers validator bans, IR round-trip stability, gas estimator bounds, runtime determinism, storage/events, ABI encoding, and PRNG determinism.

⸻

Security & Audit Notes
	•	The validator bans imports, reflection, dynamic exec/eval, file/network/time, threads, and non-deterministic operations.
	•	All stdlib methods are capability-scoped and charge gas; side effects are confined to the contract state and event sink.
	•	See vm_py/audit/checklist.md for reviewer guidance.

⸻

Versioning

vm_py/version.py exports a SemVer string and optional git describe suffix. Any change that can alter byte outputs (IR encoding, ABI, gas costs, bloom/log order) is a consensus change and must be coordinated with spec/ and the node.

⸻

References
	•	spec/opcodes_vm_py.yaml — opcode set and costs
	•	spec/abi.schema.json — ABI schema
	•	spec/tx_format.cddl — CBOR wire for tx & receipts
	•	vm_py/specs/* — detailed specs: DETERMINISM, IR, ABI, GAS

Happy building — and enjoy deterministic Python contracts.
