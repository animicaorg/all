# VM Spec Index

This folder collects the normative specifications for the deterministic Python VM used by Animica. If you’re implementing or auditing the VM, start here.

> **Normative vs informative:** The documents listed as *Normative* define conformance requirements for all nodes. *Informative* docs provide rationale and examples.

---

## Contents

### Normative
- **Determinism rules** — [`DETERMINISM.md`](./DETERMINISM.md)  
  What language features are allowed/banned, resource bounds, I/O restrictions, numeric limits, and sandboxing model.  
  *Referenced by:* runtime sandbox and validator.

- **Intermediate Representation (IR)** — [`IR.md`](./IR.md)  
  The instruction set, control-flow model, encoding, and validation rules. Round-trip stable and hashable.

- **ABI** — [`ABI.md`](./ABI.md)  
  Function dispatch, argument/return encoding, error conventions, and event formatting used by contracts, RPC, and SDKs.

- **Gas model** — [`GAS.md`](./GAS.md)  
  Cost units, metering semantics, refunds, and accounting invariants. Gas table is resolved at build time from the opcode catalog.

### Informative
- Rationale & design notes are interspersed in the above docs. See also repository-wide specs under `spec/` for chain/header/tx formats.

---

## Where each spec is enforced in code

- **Validation / determinism**  
  - Validator: `vm_py/validate.py`  
  - Sandbox: `vm_py/runtime/sandbox.py`
- **IR encode/decode**  
  - Types: `vm_py/compiler/ir.py`  
  - Codec: `vm_py/compiler/encode.py`
- **Gas**  
  - Table loader: `vm_py/compiler/gas_estimator.py` and `vm_py/runtime/gasmeter.py`  
  - Resolved costs: `vm_py/gas_table.json` (derived from `spec/opcodes_vm_py.yaml`)
- **ABI**  
  - Types & codecs: `vm_py/abi/{types,encoding,decoding}.py`  
  - Runtime dispatcher: `vm_py/runtime/abi.py`

Integration points:
- Execution adapter hook: `execution/runtime/contracts.py`  
- SDK encoders mirror ABI: `sdk/*/{tx,contracts}/*`  
- OpenRPC surface (node): `spec/openrpc.json` (served by `rpc/openrpc_mount.py`)

---

## Source of truth & versioning

- The **specs in this directory are normative** for VM behavior and wire compatibility.  
- Version string: `vm_py/version.py` (surfaced via CLI and APIs).  
- Any change that alters IR encoding, ABI bytes, or gas semantics **must bump** the VM version and update test vectors.

---

## Canonical references in `spec/`

- Opcode catalog (input to gas table): `spec/opcodes_vm_py.yaml`  
- ABI JSON-Schema (shared by SDKs): `spec/abi.schema.json`  
- Transaction/header CDDL (signing & execution domains): `spec/tx_format.cddl`, `spec/header_format.cddl`

---

## Validation & test vectors

- Unit tests for determinism, encoding, and runtime:
  - `vm_py/tests/test_validator.py`
  - `vm_py/tests/test_compile_roundtrip.py`
  - `vm_py/tests/test_gas_estimator.py`
  - `vm_py/tests/test_runtime_counter.py`
  - `vm_py/tests/test_abi_encoding.py`
- Example contracts and manifests (used across docs and tests):
  - `vm_py/examples/counter/*`
  - `vm_py/examples/escrow/*`
- Cross-module vectors: `spec/test_vectors/*` (txs, headers, programs)

Run the local suite:

```bash
pytest -q vm_py/tests


⸻

CLI quickstart (for spec conformance sanity)
	•	Compile a contract to IR:

python -m vm_py.cli.compile vm_py/examples/counter/contract.py --out /tmp/counter.ir


	•	Inspect IR & static gas:

python -m vm_py.cli.inspect_ir /tmp/counter.ir


	•	Run a manifest method:

python -m vm_py.cli.run --manifest vm_py/examples/counter/manifest.json --call get



The outputs above must be stable across conforming implementations and match the encoding rules in IR.md and ABI.md.

⸻

Change process
	1.	Update the relevant spec (DETERMINISM.md, IR.md, ABI.md, or GAS.md).
	2.	Update implementation + vm_py/gas_table.json if costs changed.
	3.	Regenerate/extend tests and vectors.
	4.	Bump vm_py/version.py.
	5.	Note the change in module CHANGELOG (repo root or module README).

⸻

Contact & scope

These specs cover the VM only (validation, IR, ABI, gas). Chain/consensus, proofs, DA, and RPC are specified elsewhere in the repository and referenced where necessary.

