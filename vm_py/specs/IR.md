# Animica Python-VM IR
_Normative description of the intermediate representation executed by `vm_py/runtime/engine.py`._

This document specifies the **instruction set**, **control flow**, **stack model**, and **encoding rules** for the Animica Python-VM IR. It is the contract between the compiler (`vm_py/compiler/*`) and the interpreter (`vm_py/runtime/*`). Any change to op semantics or encoding **must** bump `vm_py/version.py` and the gas table.

---

## 1) Design goals

- **Deterministic**: entirely integer/bytes/boolean; no floats, clocks, or host I/O.
- **Gas-first**: every step is metered from `vm_py/gas_table.json`.
- **Simple stack machine**: small, auditable instruction set; structured blocks + explicit jumps.
- **Stable bytes**: canonical CBOR/msgspec encoding defined in `vm_py/compiler/encode.py`.

---

## 2) Values & types

IR operates on these value kinds:

- **`int`**: unbounded signed integer with network caps (see `vm_py/config.py`).
- **`bool`**: `False`/`True` (stack as `0/1`).
- **`bytes`**: immutable byte strings (caps on length).
- **`addr`**: address is represented as `bytes` with length checks in stdlib when needed.
- **`none`**: unit sentinel used for control plumbing (rare).

No floats/NaN/None arithmetic. Type errors are deterministic `VmError` → revert.

---

## 3) Stack model

- **Operand stack** only; no general registers. A tiny, implicit **frame** is created for each `CALL` with its own stack depth limit.
- Instructions consume/produce a fixed number of operands. For clarity, stack effects are written as:
  - `OP: (in1, in2, ...) -> (out1, out2, ...)`

Examples:
- `ADD: (a:int, b:int) -> (a+b:int)`
- `BYTES_LEN: (b:bytes) -> (n:int)`

---

## 4) Constants & identifiers

- **Const pool**: module-level list of literals (`int`, `bytes`, small tuples). Loaded via `CONST idx`.
- **Names**: functions and externals (stdlib) are referenced by **stable integer IDs**, resolved during compilation/linking. Human-readable names are kept for tooling but not used in execution.

---

## 5) Instruction set

### 5.1 Stack / data movement
- `NOP: () -> ()`
- `POP: (x) -> ()`
- `DUP n: (..., x_n, ..., x_1) -> (..., x_n, ..., x_1, x_n)`  *(1-based, n≥1)*
- `SWAP n: (..., x_n, x_{n-1}) -> (..., x_{n-1}, x_n)`
- `CONST i: () -> (const[i])`

### 5.2 Integer arithmetic (all exact; caps enforced)
- `ADD, SUB, MUL: (a:int, b:int) -> (int)`
- `DIV: (a:int, b:int) -> (a // b)`  *(floor; b≠0 else VmError)*
- `MOD: (a:int, b:int) -> (a % b)`  *(b≠0)*
- `NEG: (a:int) -> (-a:int)`
- Bitwise: `NOT, AND, OR, XOR: (a:int, b?:int) -> (int)`
- Shifts: `SHL, SHR: (a:int, n:int) -> (int)`  *(caps on bit width & shift)*

### 5.3 Comparisons (push `bool` as 0/1)
- `EQ, NEQ: (a, b) -> (bool)`
- `LT, LE, GT, GE: (a:int, b:int) -> (bool)`

### 5.4 Bytes
- `BYTES_LEN: (b:bytes) -> (n:int)`
- `BYTES_CONCAT: (a:bytes, b:bytes) -> (bytes)`
- `BYTES_SLICE: (b:bytes, start:int, end:int) -> (bytes)`  *(0 ≤ start ≤ end ≤ len)*
- `INT_TO_BYTES: (x:int, n:int) -> (bytes)` *(big-endian, zero-padded/truncated with checks)*
- `BYTES_TO_INT: (b:bytes) -> (x:int)` *(big-endian)*

### 5.5 Cryptographic hashing (deterministic, metered)
- `KECCAK256: (b:bytes) -> (h:bytes32)`
- `SHA3_256:  (b:bytes) -> (h:bytes32)`
- `SHA3_512:  (b:bytes) -> (h:bytes64)`

### 5.6 Control flow
Structured into **blocks** and **labels**:

- `JUMP lbl: () -> ()` *(unconditional)*
- `JUMP_IF lbl: (cond:bool) -> ()` *(jump if cond==1)*
- `RET: (retval?) -> (retval?)` *(return from current function; arity checked by signature)*

> **Well-formedness:** all forward branches must target **label boundaries**. The validator ensures no stack underflows and that stack height at each label is consistent.

### 5.7 Calls
- `CALL fid, argc: (arg_{argc-1},...,arg_0) -> (ret*)`
  - `fid`: local function id. The signature (arg/ret counts) is encoded in the module. Arity checked.
- `CALL_EXTERN eid, argc: (args...) -> (ret...)`
  - Calls **stdlib** functions (storage/events/hash/abi/syscalls). See §8 in Determinism spec for allowed externals.
- `REVERT: (reason:bytes) -> (unreachable)`  *(halts current call path with revert)*

> Storage/events are **not** primitive opcodes; they are externals, e.g., `CALL_EXTERN E_STORAGE_GET, 1`.

---

## 6) Gas semantics

- Each opcode has a **base cost**; some have **linear** cost in input size (e.g., hashing, concat).
- Gas is debited **before** executing the effect. Insufficient gas raises `OOG` (deterministic).
- The authoritative table is `vm_py/gas_table.json`. The IR spec does not embed numeric costs.

---

## 7) Modules, functions & blocks

A **Module** contains:
- `version` (u32)
- `consts: list`
- `funcs: list[Func]`
- `entrypoint` function id (optional for scripts)

A **Func** contains:
- `fid` (u32), `name` (debug only)
- `params` count, `returns` count
- `blocks: list[Block]`

A **Block** contains:
- `label` (u32)
- `code: list[Instr]`

> **Canonical order:** funcs and blocks are serialized in ascending id/label order. This is enforced by the encoder.

---

## 8) Encoding (stable)

- IR bytes are produced/consumed by `vm_py/compiler/encode.py`.
- Format is canonical **msgpack-like via msgspec** or CBOR with:
  - Sorted map keys where maps are used,
  - Compact integers,
  - Byte strings for `bytes`.
- The encoder rejects unknown opcodes/fields and enforces canonical ordering. A round-trip `encode⟲decode` MUST be stable byte-for-byte.

---

## 9) Validation & safety

Before execution, the loader/validator ensures:

1. **Opcode well-formedness**: valid operands, label targets exist.
2. **Stack discipline**: per-label stack height is consistent; no underflow; return arity matches function signature.
3. **Resource caps**: const sizes, function/block sizes, maximum depth.
4. **External ids**: `eid` values are from the **linked** stdlib table (network-configurable).
5. **No dead fallthrough** after `RET`/`REVERT` within a block.

Violations cause `ValidationError` at load time, not at runtime.

---

## 10) Small IR “assembly” reference

For readability, examples use a textual assembly with one instruction per line:

; Stack: top on the right
CONST 0             ; push bytes(“counter”)
CALL_EXTERN E_STORAGE_GET, 1   ; -> (val:bytes)
BYTES_LEN                          ; -> (n:int)
JUMP_IF L_has
; init to zero if missing
CONST 1             ; int 0
JUMP L_got

L_has:
BYTES_TO_INT

L_got:
; increment
CONST 2             ; int 1
ADD
DUP 1
INT_TO_BYTES  ; needs explicit length below, shown expanded instead:
; (use ABI encode for canonical length if needed)
CALL_EXTERN E_ABI_ENC_INT, 1     ; -> (bytes)
CONST 0            ; key
SWAP 1
CALL_EXTERN E_STORAGE_SET, 2
RET

> Exact extern ids and ABI helpers depend on the linked stdlib table. The compiler emits them consistently.

---

## 11) Worked examples

### 11.1 Counter.increment()

**Python (subset):**
```py
from stdlib import storage, abi

def inc():
    key = b"counter"
    b = storage.get(key)
    n = 0 if len(b) == 0 else abi.decode_int(b)
    n = n + 1
    storage.set(key, abi.encode_int(n))
    return n

IR sketch (using ABI helpers for int⇄bytes):

; consts: [b"counter", 0, 1]
; params: 0, returns: 1

L0:
CONST 0
CALL_EXTERN E_STORAGE_GET, 1         ; -> (b)
DUP 1
BYTES_LEN                            ; -> (b, n)
CONST 1                              ; 0
EQ                                   ; (b, n==0)
JUMP_IF L_new

; existing value
CALL_EXTERN E_ABI_DEC_INT, 1         ; (n)
JUMP L_have

L_new:
POP                                   ; drop b
CONST 1                               ; n=0

L_have:
CONST 2                               ; +1
ADD
DUP 1
CALL_EXTERN E_ABI_ENC_INT, 1          ; (n, b_enc)
CONST 0
SWAP 1
CALL_EXTERN E_STORAGE_SET, 2
RET

11.2 Keccak of concatenation

; return keccak256(a||b) where a,b are bytes params
; params: 2 (a, b), returns: 1
BYTES_CONCAT
KECCAK256
RET

11.3 Conditional branch

; if x > 10: return x else revert("small")
; consts: [10, b"small"]
; params: 1 (x), returns:1
CONST 0
GT
JUMP_IF L_ok
CONST 1
REVERT
L_ok:
RET


⸻

12) Errors & halting
	•	REVERT halts the current call path and unwinds to the call boundary, producing a deterministic receipt (reason bytes encoded via ABI).
	•	Any runtime check failure (division by zero, out-of-bounds slice, caps exceeded) raises VmError → revert.
	•	OOG can occur before executing an op (debit-first). It reverts with a standard out-of-gas code.

⸻

13) Versioning
	•	The IR version is recorded in the module header. Changing opcodes, stack effects, or encoding increments the version and requires a coordinated gas-table update.

⸻

14) Mapping to high-level Python

The compiler (ast_lower.py, typecheck.py, symbols.py) lowers Python subset constructs:
	•	Expressions → stack sequences (evaluate left-to-right).
	•	Conditionals/loops → labels + JUMP/JUMP_IF, with loop guards ensuring gas-bounded termination.
	•	Function calls → CALL or CALL_EXTERN.
	•	Attribute access on stdlib facades are resolved to extern ids at compile/link.

⸻

15) Conformance checklist (IR)

Implementations MUST:
	1.	Enforce stack effects and label stack heights.
	2.	Debit gas before effect; raise OOG deterministically.
	3.	Use canonical encoding; fail load on non-canonical bytes.
	4.	Disallow unknown extern ids or opcodes.
	5.	Guarantee REVERT and RET terminate blocks (no fallthrough).

⸻

See also:
	•	vm_py/specs/DETERMINISM.md — language subset & runtime rules
	•	vm_py/compiler/encode.py — canonical encoder/decoder
	•	vm_py/runtime/engine.py — reference interpreter
