# Animica Python-VM Examples

This folder contains small, deterministic sample contracts and manifests used to exercise the Python VM locally—no node or network required.

Contents:
- `counter/contract.py` — a tiny counter contract (increment + get).
- `counter/manifest.json` — ABI + metadata for the counter.
- `escrow/contract.py` — minimal escrow demo using the treasury API.
- `escrow/manifest.json` — ABI + metadata for the escrow demo.
- `blob_pinner/contract.py` — pins bytes via the blob.pin capability and emits the commitment.
- `blob_pinner/manifest.json` — declares blob.pin capability + limits for the demo.
- `useful_work/` — block-scoring demo contract used by `useful_work_demo.py`.

> The VM runs fully offline and deterministically. Each CLI invocation creates a fresh in-memory state unless the tool exposes an explicit state persistence flag (see `-h` on each command).

---

## Prerequisites

- Python **3.11+**
- Run commands from the **repo root** so `vm_py` is importable (or add it to `PYTHONPATH`).

Helpful CLIs shipped in this repo:

- **Compile**: `python -m vm_py.cli.compile`  
- **Run** (simulate a call): `python -m vm_py.cli.run`  
- **Inspect IR**: `python -m vm_py.cli.inspect_ir`

Each command supports `-h/--help` for full options.

---

## Quickstart: Counter

### 1) Compile the contract to IR

You can compile from the manifest (recommended) or directly from the source file.

```bash
# From manifest → IR
python -m vm_py.cli.compile \
  --manifest vm_py/examples/counter/manifest.json \
  --out /tmp/counter.ir

or

# From source → IR
python -m vm_py.cli.compile \
  --source vm_py/examples/counter/contract.py \
  --out /tmp/counter.ir

2) Inspect the IR

python -m vm_py.cli.inspect_ir --ir /tmp/counter.ir
# prints: code hash, size, static gas upper bound, and a pretty IR summary

3) Run methods

Use the manifest so the runner knows the ABI.

# Read current value (expected 0 in a fresh run)
python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call get

# Increment (no args); returns nothing, but emits an event in logs
python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call inc

Note: Each run is a fresh VM instance. If the runner provides a state persistence option, it will be listed in --help.

⸻

Escrow demo

Compile and inspect just like the counter:

python -m vm_py.cli.compile \
  --manifest vm_py/examples/escrow/manifest.json \
  --out /tmp/escrow.ir

python -m vm_py.cli.inspect_ir --ir /tmp/escrow.ir

Then call functions exposed by the escrow ABI (see its manifest.json for names/signatures):

# Example (function names are illustrative; check the manifest):
python -m vm_py.cli.run \
  --manifest vm_py/examples/escrow/manifest.json \
  --call get_state

python -m vm_py.cli.run \
  --manifest vm_py/examples/escrow/manifest.json \
  --call deposit --args '[{"to":"anim1...", "amount":1000}]'


⸻

Tips & Troubleshooting
	•	Argument encoding: pass --args with a JSON array matching the function’s ABI parameter list.
Example: --args '["hello", 42, "0xdeadbeef"]'
	•	Gas estimates: inspect-ir reports a static upper bound derived from the compiled IR.
	•	Pretty IR: inspect-ir --format json prints a JSON structure useful for tooling.
	•	Reproducibility: The IR bytes and code hash are stable across machines given identical inputs.

⸻

What’s next?
	•	Use the IR with the higher-level execution layer or SDK once you’re ready to integrate on a node.
	•	For browser-side simulation, see the studio-wasm/ package which bundles a Pyodide build of the VM.

