# VM(Py) Overview — Deterministic Python Subset & Runtime Architecture

**Status:** Stable (v1)  
**Scope:** Deterministic smart-contract VM implemented in Python with a _safe Python subset_, compiled to a small IR and executed by a gas-metered interpreter.  
**See also:** `vm_py/specs/*`, `execution/*`, `capabilities/*`, `sdk/*`

---

## 1) Design Goals

- **Determinism first.** Same code + inputs ⇒ same outputs, logs, and gas on every node.
- **Auditability.** Human-readable source is validated → lowered → IR with stable encoding.
- **Tight resource bounds.** Static upper-bound gas estimator + runtime gas metering; numeric caps.
- **Small surface.** Int/bytes/bool/address scalars, structured via ABI only; no general I/O.
- **Composable.** Standard library shims (`storage`, `events`, `hash`, `treasury`, `syscalls`) with identical behavior across nodes.

---

## 2) Deterministic Python Subset

Contracts are authored in a strict Python subset and compiled by `vm_py`:

### Allowed
- **Pure Python constructs:** `def`, `if/elif/else`, `while`, `for over range(...)`, assignment, comparisons.
- **Types:** `int` (bounded; see caps), `bytes`, `bool`, `Address` (ABI wrapper), `Optional[T]` in ABI only.
- **Containers (limited):** `tuple` and `list` literals for small, bounded sequences (size caps enforced at validation & ABI).
- **Operators:** integer arithmetic `+ - * // %`, bit ops `& | ^ << >>`, `len(bytes)`, slicing `b[a:b]` with caps.
- **Errors:** `abi.revert(msg)` and `abi.require(cond, msg)`.
- **Imports:** `from stdlib import storage, events, hash, abi, treasury, syscalls` _only_.

### Forbidden
- Floating point (`float`), `decimal`, arbitrary `open()/os/subprocess/socket/threading`, `time`, `random`.
- Reflection & dynamic code: `eval`, `exec`, `__import__`, metaclasses.
- Unbounded recursion, generators, async/await, context managers with I/O.
- Non-deterministic globals or module-level code with side effects.

### Builtins
Allowlist (exact list in `vm_py/compiler/builtins_allowlist.py`): `len`, `range`, `min`, `max`, `abs`, `enumerate` (bounded), `int`, `bytes`. Everything else is rejected at validation.

---

## 3) Runtime Architecture

source.py
│  (AST Validator: syntax, imports, caps)
▼
Lowering (ast_lower.py)  ──▶  Typecheck (typecheck.py)  ──▶  Gas upper-bound (gas_estimator.py)
│
▼
IR (compiler/ir.py)  ⇄  Encode/Decode (compiler/encode.py, msgspec/CBOR; stable)
│
▼
Interpreter (runtime/engine.py)
├─ GasMeter (runtime/gasmeter.py)
├─ Context (runtime/context.py: BlockEnv/TxEnv)
├─ Stdlib shims:
│    • storage (runtime/storage_api.py)
│    • events  (runtime/events_api.py)
│    • hash    (runtime/hash_api.py)
│    • treasury(runtime/treasury_api.py)
│    • syscalls(runtime/syscalls_api.py)  ← capabilities/*
└─ Deterministic PRNG (runtime/random_api.py)

**IR:** A small instruction set with explicit control flow & stack model; every op maps to an entry in `vm_py/gas_table.json`.  
**Encoding:** Canonical msgspec/CBOR with stable field ordering to ensure identical code hashes.

---

## 4) Gas & Resource Model

- **Static bound:** `compiler/gas_estimator.py` traverses IR to produce a safe upper bound.
- **Runtime metering:** `GasMeter` debits per IR op and stdlib call (costs from `gas_table.json`).
- **Refunds:** Certain operations (e.g., storage delete) may incur bounded refunds; finalized via `execution/gas/refund.py`.
- **Caps:**  
  - Int magnitude ≤ `2^256-1` (configurable in `vm_py/config.py`), arithmetic saturates or reverts per op rules.  
  - Bytes length, list/tuple sizes, loop bounds checked statically where possible; enforced dynamically otherwise.
- **Deterministic OOG:** Out-of-gas aborts at the exact same instruction across nodes.

---

## 5) ABI & Entry Points

Contracts expose callable functions defined in a **manifest** (see `spec/abi.schema.json` and `vm_py/examples/*/manifest.json`).

- **Encoding:** Canonical, length-prefixed scalars & arrays (`vm_py/abi/encoding.py`).
- **Dispatch:** `runtime/abi.py` decodes call data → invokes method → encodes return.
- **Events:** `stdlib.events.emit(name: bytes, args: dict)` produces canonical logs.

**Example manifest snippet**
```json
{
  "name": "Counter",
  "abi": {
    "functions": [
      {"name":"inc","inputs":[],"outputs":[]},
      {"name":"get","inputs":[],"outputs":[{"type":"int"}]}
    ]
  }
}


⸻

6) Stdlib Surfaces (Deterministic)
	•	storage — Key/value bytes store: get(key: bytes) -> bytes, set(key: bytes, value: bytes); helpers for ints.
	•	events — emit(topic: bytes, args: dict); ordering is deterministic (program order).
	•	hash — keccak256(b), sha3_256(b), sha3_512(b); byte-only, no streams.
	•	treasury — balance() -> int, transfer(to: Address, amount: int) (local sim is inert; chain mode bridges execution).
	•	syscalls — capability shims:
	•	blob_pin(ns: int, data: bytes) -> commitment
	•	ai_enqueue(model: bytes, prompt: bytes) -> task_id
	•	quantum_enqueue(circuit: bytes, shots: int) -> task_id
	•	zk_verify(circuit_id: bytes, proof: bytes, public: bytes) -> bool
Determinism is enforced by capabilities/runtime/determinism.py (size caps, transcript hashing).

⸻

7) Determinism Rules (Non-Exhaustive)
	•	No wall-clock or external I/O. State & inputs come only from call data and host-provided context (height, coinbase, etc.).
	•	Stable hashing & encoding. All hashes use explicit domain tags; maps/logs are serialized with stable key order.
	•	Event order == program order. No concurrency; single-threaded interpreter.
	•	Error semantics: abi.revert → REVERT; OOG → OOG; all are canonical and reflected in receipts.

⸻

8) Development Flow

CLI tools:

# Compile to IR
omni vm compile vm_py/examples/counter/contract.py --out out.ir

# Run in local simulator (no chain state writes)
omni vm run --manifest vm_py/examples/counter/manifest.json --call inc
omni vm run --manifest vm_py/examples/counter/manifest.json --call get

# Inspect IR & gas
omni vm inspect_ir out.ir

Programmatic (Python):

from vm_py.runtime.loader import load
ctr = load(manifest_path="examples/counter/manifest.json")
ctr.call("inc")
print(ctr.call("get"))  # -> 1


⸻

9) Integration with Execution/State

The VM plugs into execution/runtime/contracts.py via execution/adapters/vm_entry.py when enabled:
	1.	Tx decode → apply_tx → dispatch transfer/deploy/call.
	2.	Deploy stores code hash; calls load and execute code deterministically.
	3.	Receipts built via execution/state/receipts.py with logs and bloom.
	4.	Optional access-list generation via execution/access_list/build.py.

⸻

10) Upgrades & Feature Flags
	•	vm_py/config.py exposes strict mode and caps.
	•	Gas table lives in vm_py/gas_table.json derived from spec/opcodes_vm_py.yaml.
	•	Protocol upgrades are version-gated and coordinated via docs/spec/UPGRADES.md.

⸻

11) Security Notes
	•	Subset enforcement: The AST validator rejects disallowed nodes and imports. Never bypass the validator.
	•	Numeric hazards: Shifts/mults are capped; exceeding caps reverts (or saturates where specified).
	•	ABI validation: Strict types and lengths; malformed inputs revert before execution.
	•	Syscalls: Inputs sanitized; results are not visible same-block unless explicitly designed (e.g., enqueue now, read next block).

⸻

12) Minimal Example (Counter)

contract.py

from stdlib import storage, events, abi

KEY = b"count"

def inc():
    cur = int.from_bytes(storage.get(KEY) or b"\x00", "big")
    nxt = cur + 1
    storage.set(KEY, nxt.to_bytes(32, "big"))
    events.emit(b"Inc", {"value": nxt})

def get() -> int:
    cur = storage.get(KEY) or b"\x00"
    return int.from_bytes(cur, "big")

manifest.json matches ABI for inc() and get().

⸻

13) Reproducibility
	•	Code hash: CBOR/msgspec of IR → SHA3-256; becomes the canonical code identifier.
	•	Deterministic PRNG: Seeded from tx hash via runtime/random_api.py.
	•	Lockfiles: Toolchain versions (Python, msgspec, CBOR libs) are pinned; see docs/vm/REPRODUCIBILITY.md (and repo lockfiles).

⸻

14) References
	•	vm_py/README.md, vm_py/specs/{DETERMINISM,IR,ABI,GAS}.md
	•	execution/specs/* — gas, state, receipts
	•	capabilities/specs/* — syscalls & determinism
	•	sdk/* — client codegen & examples
	•	website/src/docs/WALLET.mdx — wallet interaction (sign & send)

⸻

