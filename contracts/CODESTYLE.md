# Animica Contracts — Deterministic Python Subset & Code Style

This document defines the **deterministic Python subset** and style conventions for contracts executed by the Animica Python VM (`vm_py`). Everything here is **consensus-facing**: violations can cause non-determinism across nodes and must be treated as correctness issues, not mere style nits.

> TL;DR: write small, explicit Python using **ints / bytes / bool / address**, the provided **stdlib** only, and constant-time-ish logic where feasible. Avoid dynamic features, implicit ordering, ambient I/O, and unbounded loops. Always annotate types and consider **gas** first.

---

## 1) Goals

- **Determinism:** Same inputs → same state changes, events, gas usage, and return values across all nodes.
- **Auditability:** Simple patterns, predictable control-flow, narrow data types.
- **Resource Safety:** Gas-first design; no unbounded computation nor storage churn.
- **Version Stability:** Contracts compiled with a pinned toolchain and standard library surface.

---

## 2) Language Subset

### 2.1 Allowed Types
- `int` (arbitrary precision; keep values within chain-defined bounds when relevant—e.g., token amounts).
- `bytes` (primary buffer type for hashing, storage keys/values, ABI).
- `bool`
- `Address` (opaque “address” value exposed via stdlib types; typically a `bytes` under the hood but use helpers)
- `Optional[T]`, `tuple[...]` for fixed, small tuples in ABI-facing returns.

> **Not allowed:** `float`, `decimal`, `complex`, user-defined classes (contracts are single-module scripts), generators, coroutines, `set`, `dict` for on-chain state (see ordering caveats below). Lists/tuples **may** be used for local computation if bounded and safe.

### 2.2 Control Flow
- `if`, `elif`, `else`, `for`, `while` allowed if loop bounds are **explicitly bounded** or trivially implied by data length (e.g., iterate over `bytes` of known bounded length). Prefer counting loops (`for i in range(n)`) with **explicit caps**.
- **No recursion.** The validator enforces a recursion depth limit of zero.
- **No dynamic code execution:** `eval`, `exec`, `compile`, `__import__` are rejected.

### 2.3 Modules & Imports
- **Only** contract-local module and `stdlib` surface:  
  `from stdlib import storage, events, hash, abi, treasury, syscalls`
- **No** imports from Python’s standard library (e.g., `os`, `time`, `random`, `threading`, `json`, `math`, etc.). The VM injects a **synthetic stdlib**; anything else is rejected at validation time.

---

## 3) Determinism Rules

### 3.1 Time, Randomness, External I/O
- **No time** queries. Use the block/tx context provided by the runtime when needed (exposed indirectly via deterministic APIs only).
- **Randomness:** Only `stdlib.random` (deterministic PRNG seeded from tx hash) is permitted for tests/demos. Do **not** use it for security-sensitive flows. When the randomness beacon is wired for contracts, it will be exposed via a dedicated deterministic read API.
- **No external I/O** (files, network, environment). All such APIs are blocked.

### 3.2 Ordering & Hash-Seed Sensitivity
- Do not depend on **hash-randomization** or CPython dict/set iteration order. When you must order values, create a **sorted list** using a stable key (e.g., lexical bytes or integer).
- Prefer canonical orderings: e.g., `sorted(items, key=lambda x: x[0])` where `x[0]` is a `bytes` key.

### 3.3 Numeric Safety
- Avoid implicit overflows by design (Python ints are unbounded). Enforce application bounds (e.g., max supply) with `abi.require(...)`.
- Division: use integer division `//` where applicable. Never rely on float conversion.

### 3.4 Gas Stability
- Avoid algorithms with data-dependent superlinear paths (unless bounded tight).
- Use static bounds for loops; avoid repeated storage writes/reads in tight loops.
- Emit events sparingly and in deterministic order.

---

## 4) Contract Structure

### 4.1 Module Layout
Contracts are **single Python files** that expose callable entrypoints via `stdlib.abi`:

- **Top-level constants** first (e.g., version, storage keys).
- **Pure helpers** next (bounded logic only).
- **Entrypoints** last (`@abi.export` or explicit name registry as defined by the toolchain).

Example skeleton:

```python
from stdlib import storage, events, hash, abi, treasury, syscalls

# ---- constants / keys --------------------------------------------------------
KEY_COUNTER = b"counter"  # small, fixed storage keys; prefer bytes

# ---- helpers -----------------------------------------------------------------
def _load_counter() -> int:
    data = storage.get(KEY_COUNTER)
    return 0 if data is None else abi.decode_int(data)

def _save_counter(value: int) -> None:
    abi.require(value >= 0, b"neg")  # application invariant
    storage.set(KEY_COUNTER, abi.encode_int(value))

# ---- entrypoints --------------------------------------------------------------
def inc(delta: int) -> int:
    abi.require(0 < delta <= 1_000, b"delta")  # bound work/inputs
    v = _load_counter()
    v2 = v + delta
    _save_counter(v2)
    events.emit(b"Inc", {b"delta": abi.encode_int(delta), b"value": abi.encode_int(v2)})
    return v2

def get() -> int:
    return _load_counter()

4.2 Naming & Style (PEP 8-ish with consensus twists)
	•	Lower_snake_case for functions and locals; UPPER_SNAKE_CASE for constants.
	•	Keep functions < 60 lines; split helpers aggressively.
	•	Docstrings for entrypoints: describe inputs/returns and invariants.
	•	Type annotations required for all function params/returns.

⸻

5) Storage API Rules
	•	Keys and values are bytes. Use ABI helpers to encode/decode integers and composite values.
	•	Prefer few, stable keys. Avoid patterns that create unbounded key spaces without strict caps.
	•	Reads before writes (load → check → set) in a deterministic sequence.
	•	Delete by writing empty or using provided delete helper if available (ensure semantics are consistent with VM version).

Key patterns:
	•	Small fixed keys: b"balance:" + address_bytes (but consider a compact map scheme).
	•	Composite keys: prefix || uvarint(id)—deterministic, versioned.

⸻

6) Events & ABI
	•	Events must use bytes names and a dict of bytes→bytes arguments.
	•	Prefer short keys and canonical encoding (e.g., abi.encode_int, abi.encode_bytes).
	•	Return values must be encodable via ABI helpers. Avoid large returns—emit events instead.

Event example:

events.emit(b"Transfer", {
  b"from": abi.encode_address(sender),
  b"to":   abi.encode_address(recipient),
  b"value": abi.encode_int(amount),
})


⸻

7) Hashing
	•	Use hash.keccak256 / hash.sha3_256 / hash.sha3_512 only (from stdlib).
	•	Feed bytes only; assemble payload deterministically (abi helpers recommended).
	•	Do not rely on Python object __repr__ / str for hashes—always canonicalize with bytes.

⸻

8) Deterministic Syscalls & Capabilities
	•	syscalls.blob_pin(ns, data) / syscalls.ai_enqueue(...) / syscalls.quantum_enqueue(...) are deterministic façades that enqueue requests and return receipts. Results are consumed next block via deterministic reads (when bridged). Validate lengths and caps.
	•	Treat syscalls like expensive operations—bound sizes, cap counts per call.

⸻

9) Forbidden / Discouraged
	•	❌ import *, dynamic attribute access (getattr on user input).
	•	❌ Mutation of global data after initialization (except via explicit storage writes).
	•	❌ Catch-all except: that hides errors; use targeted guards with clear messages.
	•	❌ Data-dependent iteration over unbounded user inputs.
	•	❌ Floating-point operations.
	•	⚠️ Large in-memory lists/tuples; keep within tight bounds.

⸻

10) Gas-Aware Design Patterns

Do:
	•	Pre-validate inputs with abi.require to fail-fast.
	•	Cache repeated storage reads into locals.
	•	Bound loops with min(len(data), HARD_CAP).
	•	Use pre-sized buffers and simple concatenation for bytes.
	•	Batch event fields compactly.

Avoid:
	•	N² loops on user-controlled arrays.
	•	Re-encoding or re-hashing the same payload multiple times; reuse local bytes.

⸻

11) Data Structure Patterns

11.1 Ordered Collections

If you must aggregate and then apply deterministic operations:

# Collect small pairs then apply canonical order
pairs: list[tuple[bytes, int]] = []
# ... fill under strict caps ...
pairs_sorted = sorted(pairs, key=lambda p: p[0])  # sort by bytes key
for k, v in pairs_sorted:
    storage.set(k, abi.encode_int(v))

11.2 Maps

Python dict is fine for local, small, bounded maps. Never depend on insertion order—explicitly sort keys before producing externally visible effects (events, storage writes).

⸻

12) Testing Expectations
	•	Unit tests should verify return values, events, and storage state deterministically.
	•	Use fixtures for ABI encoding/decoding round-trips.
	•	Test negative paths (reverts, require failures) and boundary values.

⸻

13) Linting & Type Checking
	•	Ruff: enable E, F, I, UP, B, PL rulesets (or repo defaults).
	•	Mypy/Pyright: strict mode recommended for contracts; no Any except for ABI dicts where unavoidable—prefer bytes keys/values.
	•	No unused imports, no unused variables.
	•	100-column soft wrap preferred for readability.

⸻

14) Versioning & Reproducibility
	•	Pin vm_py and toolchain versions in contracts/requirements.txt.
	•	Check in manifests and ABI used during deploys (if they are canonical sources for the contract).
	•	Rebuild artifacts via Makefile targets to ensure consistent outputs.

⸻

15) Security & Invariants
	•	Validate all externally supplied inputs with abi.require.
	•	Enforce monotonicity/bounds where applicable (e.g., balances not negative).
	•	For token-like contracts: use checked math wrappers or explicit abi.require guards on each arithmetic operation affecting supply/balances.
	•	Ensure eventual consistency if you use syscalls; be clear about when results become readable.

⸻

16) Code Review Checklist (Copy-Paste)
	1.	Types & Annotations present on every function and return.
	2.	No forbidden imports; from stdlib import ... only.
	3.	No floats/dynamic eval/recursion.
	4.	Loop bounds: every loop is statically capped or input-bounded with a hard ceiling.
	5.	Ordering: any map/list used to produce storage/events is deterministically sorted.
	6.	Storage discipline: compact keys, minimal writes, read-before-write, consistent encoding.
	7.	Events: bytes-only names/fields; ABI-encoded values.
	8.	Hashing: canonical bytes only; no object repr/str.
	9.	Gas: obvious hotspots minimized; repeated operations cached.
	10.	Reverts: clear reasons; boundary tests exist.
	11.	Syscalls: size caps enforced; deterministic usage.
	12.	Manifests/ABI: coherent with intended external interface.

⸻

17) Examples: Safe vs Unsafe

Safe bounded iteration:

MAX_ITEMS = 64

def set_many(items: list[tuple[bytes, int]]) -> int:
    n = min(len(items), MAX_ITEMS)
    # Sort by key to enforce order
    for k, v in sorted(items[:n], key=lambda kv: kv[0]):
        abi.require(len(k) <= 32, b"key")
        storage.set(k, abi.encode_int(v))
    return n

Unsafe (do NOT do this):

def set_many_unsafe(items):
    # unbounded, unordered, type-unsafe, may explode gas
    for k, v in items:
        storage.set(k, v)  # not ABI-encoded, no size checks


⸻

18) Event & Return Encoding Cheatsheet
	•	abi.encode_int(x: int) -> bytes
	•	abi.decode_int(b: bytes) -> int
	•	abi.encode_bytes(b: bytes) -> bytes (idempotent helper)
	•	abi.encode_tuple((a: int, b: bytes)) -> bytes
	•	abi.decode_tuple(b: bytes) -> tuple[...]
	•	abi.encode_address(addr) -> bytes

⸻

19) Upgradability & Compatibility
	•	Contracts are immutable once deployed; design for migrations by:
	•	Versioning your storage keys (e.g., b"cfg:v1:...").
	•	Exposing read-only getters that can be copied into new versions.
	•	Keeping event formats stable; changes should use new event names.

⸻

20) Documentation
	•	Document each entrypoint with:
	•	Purpose
	•	Inputs (types, units, bounds)
	•	Effects (storage keys touched, events emitted)
	•	Failure conditions (abi.require reasons)
	•	Gas considerations

Example docstring:

def transfer(to: bytes, amount: int) -> None:
    """
    Move tokens to `to`.
    Inputs:
      - to: Address bytes (length=32)
      - amount: int, 0 < amount <= BAL_MAX
    Effects:
      - Decrements sender balance; increments recipient.
      - Emits b"Transfer" event.
    Fails:
      - b"addr" if to invalid.
      - b"amt" if amount out of bounds.
      - b"bal" if insufficient balance.
    Gas:
      - O(1) storage reads/writes + event emission.
    """


⸻

Final Notes
	•	Prefer simplicity over cleverness. Determinism and clarity trump micro-optimizations.
	•	Keep contracts small; push heavy computation to off-chain capabilities with proofs/receipts when available.
	•	Treat every style rule that intersects with determinism as consensus-critical.

