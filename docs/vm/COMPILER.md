# VM(Py) Compiler — AST → IR → Bytecode, Static Gas Estimation

**Status:** Stable (v1)  
**Audience:** Contract authors, toolchain maintainers, node implementers  
**Source of truth:** `vm_py/compiler/*` (validator, lowering, IR, encoder, typecheck, gas estimator)

This document explains how a Python contract source becomes deterministic bytecode for the VM, and how **static gas estimation** is produced before deployment.

---

## 0) Pipeline Overview

Input: **`manifest.json` + `contract.py`**  
Output: **bytecode blob** (CBOR/msgspec) + **metadata** (IR stats, gas upper bounds)

Phases:

1. **Parse & Validate** (`vm_py/validate.py`)
   - Build Python `ast.AST`, enforce *allowed subset* and caps.
2. **Type & Symbols** (`vm_py/compiler/typecheck.py`, `symbols.py`)
   - Infer/check types against ABI (from the manifest).
3. **Lower to IR** (`vm_py/compiler/ast_lower.py`, `ir.py`)
   - Produce a small, deterministic **register-based IR** with basic blocks.
4. **Normalize & (Tiny) Optimize**
   - Constant folding & trivial DCE; no reordering across side-effects.
5. **Encode** (`vm_py/compiler/encode.py`)
   - Deterministic CBOR/msgspec encoding, stable sort of maps/lists.
6. **Static Gas Estimate** (`vm_py/compiler/gas_estimator.py`)
   - Compute safe upper bounds per function & module, using `vm_py/gas_table.json`.

> Determinism leans on: a **frozen syntax subset**, a **stable IR**, and a **canonical encoder**.

---

## 1) Inputs & Constraints

- **Contract file:** A single Python module exporting public methods listed in `manifest.json` (ABI).
- **Manifest (`manifest.json`):** ABI (functions/events), storage schema hints (optional), version pins.
- **Language subset:** No `import` beyond `stdlib.*` and injected `abi.*` helpers. No I/O, no network, no filesystem, no reflection, no dynamic `exec`.
- **Numerics:** Unbounded integers with policy caps; conversions validated by the type checker.

---

## 2) Validation (AST Gate)

Implemented in `vm_py/validate.py`. Rejects:

- **Forbidden nodes:** `Import`, `ImportFrom`, `With`, `Async*`, `ClassDef`, `Try/Except`, `Raise`, `Yield`, `Lambda`, `ListComp`/`GenExp`, `Match`, etc.
- **Control flow:** Bounded loops only; for-loops allowed when the **bound is statically deducible** or capped by policy (e.g., `for i in range(N_MAX)`).
- **Functions:** No recursion (checked by call-graph DFS). Maximum depth & params capped.
- **Builtins:** Allowlist only (e.g., `len`, `range`, `int`, `bytes`, `bool`, simple tuple/list construction). No `open`, `print`, `vars`, `dir`, randomness, or time.
- **Attributes & subscripts:** Limited to safe primitives and ABI/stdlib shims.
- **Byte sizes:** Literal and constructed `bytes` guarded by max-size caps.
- **Raise/Revert:** Use `abi.revert("msg")` instead of Python `raise`.

Validation produces **spaned errors** (line/col) with `ValidationError`.

---

## 3) Type & Symbols

- **Types:** `int`, `bool`, `bytes`, `address`, tuples of these, and bounded arrays (when ABI states a fixed/upper length).
- **Symbol table:** Lexical scopes, closure-free (no nested defs). Resolves variables, arguments, and builtins.
- **ABI conformance:** Public function params/returns checked against `manifest.json`.
- **Storage hints (optional):** Permit better estimation for storage keys/values sizes.

Type failures raise `ValidationError` or `CompileError` with source spans.

---

## 4) IR Design (Register-Based, Block Structured)

`vm_py/compiler/ir.py` defines:

```python
@dataclass(frozen=True)
class Reg:    idx: int                        # R0..Rn
@dataclass(frozen=True)
class Instr:  op: str; dst: Reg|None; args: tuple[Any, ...]
@dataclass(frozen=True)
class Block:  label: str; instrs: list[Instr]; terminator: Instr   # JUMP/JUMPI/RET/REVERT
@dataclass(frozen=True)
class Func:   name: str; params: list[Reg]; blocks: list[Block]; attrs: dict
@dataclass(frozen=True)
class Module: funcs: list[Func]; meta: dict

	•	Registers are SSA-like temporaries (no mutation).
	•	Side effects (storage/events/syscalls) are explicit ops.
	•	Control flow uses Block + terminator; fall-through is not implicit.
	•	Selectors: A build-time dispatch table maps ABI selectors → entry Func.

Representative opcodes:
	•	Core: CONST, MOVE, ADD/SUB/MUL/DIV/MOD, LT/GT/EQ, AND/OR/NOT, PHI (minimal), JUMP, JUMPI, RET, REVERT.
	•	Bytes/ABI: BYTES_LEN, BYTES_SLICE, BYTES_CONCAT, ABI_ENC, ABI_DEC.
	•	Hash: HASH_KECCAK, HASH_SHA3_256, HASH_SHA3_512.
	•	Storage: SLOAD, SSTORE, SDEL.
	•	Events: EVENT_EMIT.
	•	Syscalls: SC_BLOB_PIN, SC_AI_ENQ, SC_Q_ENQ, SC_ZK_VERIFY, SC_RANDOM.
	•	Treasury: TREASURY_TRANSFER.

The gas model in docs/vm/GAS_MODEL.md maps directly onto these IR ops.

⸻

5) Lowering (AST → IR)

vm_py/compiler/ast_lower.py traverses the validated AST to emit IR:
	•	Expressions allocate fresh registers.
	•	If/else become JUMPI to labeled blocks.
	•	For-range loops become counter blocks; bounds must be statically bounded or guarded by caps.
	•	Calls to stdlib/abi map to explicit IR ops.

Example — Counter:

Python

from stdlib import storage, events

def inc():
    c = storage.get(b"count")
    c = c + 1
    storage.set(b"count", c)
    events.emit(b"Inc", {b"value": c})

IR (schematic)

block entry:
  r0 = CONST b"count"
  r1 = SLOAD r0
  r2 = CONST 1
  r3 = ADD r1, r2
  _  = SSTORE r0, r3
  r4 = ABI_ENC_TUPLE {b"value": r3}
  _  = EVENT_EMIT b"Inc", r4
  RET


⸻

6) Normalization & Tiny Optimizations
	•	Constant Folding: ADD(CONST a, CONST b) → CONST (a+b), strings/bytes concat of literals, len(const).
	•	DCE (trivial): Remove dead temps with no users (within a block).
	•	No CSE/LICM: Avoids complex alias reasoning; keeps predictability.
	•	Strict mode: Can disable even these tiny passes for debugging parity.

All transforms are structure- and order-preserving for side-effect ops.

⸻

7) Encoding (Bytecode)

vm_py/compiler/encode.py emits CBOR/msgspec with:
	•	Module header: version, abi_hash, gas_table_version, build_meta (tool versions).
	•	Functions: stable order by selector/name; blocks by DFS order; instructions as tuples.
	•	Canonicalization: Sorted map keys; stable list ordering; integers encoded minimally.
	•	Bytecode hash: sha3_256(bytecode) used across the stack (RPC/verify/artifacts).

Encoding is deterministic across platforms and Python versions.

⸻

8) Static Gas Estimation

Implemented in vm_py/compiler/gas_estimator.py.

8.1 Data Sources
	•	Gas table: vm_py/gas_table.json (resolved from spec/opcodes_vm_py.yaml).
	•	ABI: parameter/return types; size bounds for bytes/arrays.
	•	Policy caps: global maxima for loops/events/storage sizes.

8.2 Strategy

Compute a safe upper bound per function:
	•	Fixed-cost ops: Sum directly from the table.
	•	Size-linear ops: Use compile-time sizes if literals; else use:
	•	ABI-declared max (e.g., bytes<=64k), or
	•	Network policy cap (fallback).
	•	Branches: Take the max of branch-path costs + guard costs.
	•	Loops: Require static bound (from range(K) or cap); multiply loop-body cost by bound.
	•	Syscalls: Charge call-surface cost using size caps of payloads.

The estimator returns:

{
  "func": "inc",
  "gas_upper": 360,
  "assumptions": {
    "event_payload_max": 64,
    "storage_key_len": 5,
    "storage_val_max": 32
  }
}

8.3 Pseudocode

def estimate_func(func: Func, gas_table: GasTable, caps: Caps) -> Bound:
    seen = set()
    def block_cost(b: Block) -> int:
        cost = 0
        for ins in b.instrs + [b.terminator]:
            cost += cost_of(ins, gas_table, caps)
        return cost

    # Path-sensitive max over CFG with loop-bounds
    def dfs(label, budget, depth):
        if depth > caps.loop_nest_max:
            raise CompileError("Loop nesting too deep")
        b = func.block_by(label)
        c = block_cost(b)
        if b.terminator.op == "JUMP":
            return c + dfs(b.terminator.args[0], budget, depth)
        if b.terminator.op == "JUMPI":
            t = dfs(b.terminator.args[0], budget, depth)
            f = dfs(b.terminator.args[1], budget, depth)
            return c + max(t, f)
        if b.terminator.op == "LOOP":  # canonicalized from for-range
            k = bound_of_loop(b, caps)  # static bound
            body = cost_of_loop_body(b)
            return c + k * body
        return c  # RET/REVERT
    return dfs(func.entry_label, 0, 0)

8.4 Outputs
	•	Per-function: gas_upper, proof of caps used (for debuggability).
	•	Per-module: sum of public entrypoints (for deploy UI heuristics).

SDKs may show both upper bound and dynamic post-call actual gas (from receipts).

⸻

9) Errors & Diagnostics
	•	ValidationError: illegal AST nodes, forbidden imports, recursion, size caps exceeded.
	•	CompileError: lowering/type mismatches, unsupported patterns, loop with unknown bound.
	•	Both include file/line/column and a hint.

⸻

10) Reproducibility
	•	The compiler embeds:
	•	vm_version and gas_table_version
	•	python_version, msgspec/cbor versions
	•	build_git_describe (if available)
	•	Rebuilding the same source + manifest under the same toolchain yields an identical bytecode hash.

⸻

11) Example: Static Gas for inc()

With defaults (see GAS_MODEL):
	•	SLOAD: 80 + 1*key + 1*value(≈1) ≈ 86
	•	ADD: 5
	•	SSTORE: 160 + 1*key + 2*value(≈1) ≈ 167
	•	EVENT_EMIT: 40 + 2*hash(≈28 each) + payload(≈6+N) ≈ 102
	•	Total upper bound: ~360 gas

The compiled artifact stores this estimate alongside the function metadata.

⸻

12) CLI & Programmatic Use
	•	CLI:
omni vm compile path/to/contract.py --manifest path/to/manifest.json --out out.ir
Flags: --strict, --print-ir, --emit-gas-report.
	•	Python API:
from vm_py.runtime.loader import load → returns (bytecode, meta, gas_report)

⸻

13) Bytecode Layout (Simplified)

Module {
  version: "vm-py/1",
  abi_hash: <32B>,
  gas_table_version: "1.0.0",
  funcs: [
    { name, params, blocks: [{label, instrs:[(op, dst, args)...], terminator}, ...] }
  ],
  dispatch: { selector -> func_index },
  meta: { build: {...}, caps: {...}, gas_bounds: {...} }
}

Encoded via CBOR/msgspec with stable ordering.

⸻

14) Security Notes
	•	No ambient I/O; syscalls are explicitly metered.
	•	Loop bounds must be proven or capped at compile time.
	•	Event/data and storage sizes are limited by policy to prevent gas underestimation or state bloat.
	•	Bytecode hash is used across RPC/verify flows; changing the encoder invalidates hashes and requires a version bump.

⸻

See also:
	•	docs/vm/GAS_MODEL.md for the runtime gas schedule.
	•	docs/spec/ENCODING.md for canonical encoding rules.
	•	vm_py/tests/* for validator, IR, and estimator conformance tests.
