# Determinism Rules (Normative)

This document defines the **allowed syntax**, **banned features**, and **resource limits** that make Animica’s Python VM deterministic across all conforming nodes.

> **Scope:** These rules apply to **contract source**, compiled **IR**, and runtime behavior. They are enforced by the validator (`vm_py/validate.py`), sandbox (`vm_py/runtime/sandbox.py`), interpreter (`vm_py/runtime/engine.py`), and ABI/encoding layers. When rules and implementation disagree, **this spec is the source of truth**.

---

## 1) Execution model

- **Single-threaded, gas-first interpreter.** No concurrency, no timers. Every step consumes gas per the resolved table (`vm_py/gas_table.json`).
- **Purely deterministic state:** Only contract storage and emitted events outlive a call. No other host/global state is observable.
- **No ambient I/O:** Contracts cannot access filesystem, clock, network, randomness, or environment variables. All external capability is via **explicit, metered syscalls** surfaced by the stdlib (see §8).
- **Failure semantics:** Any uncaught error reverts the current call (see §10), consumes gas as specified, and yields a deterministic receipt.

---

## 2) Language subset (source)

Only a restricted Python subset is allowed; the validator rejects programs violating the rules below.

### Allowed
- **Modules / structure:** one module per contract file; `import stdlib.*` only (see §8). No relative imports, no third-party modules.
- **Defs & classes:** `def` functions and simple classes (data containers) permitted only if they do not rely on dynamic attribute creation or metaprogramming. Dunder methods are disallowed except `__init__`.
- **Control flow:** `if/elif/else`, `for`, `while`, `break`, `continue`, `return`, conditional expressions.
- **Expressions:** arithmetic on integers, boolean ops, comparisons, slicing of `bytes`/`bytearray`.
- **Literals:** `int`, `bool`, `bytes`, `bytearray`, `str` (ASCII/UTF-8 only by construction), `None`, tuples and lists of allowed element types.
- **Comprehensions:** list/bytes comprehensions over deterministic iterables.
- **With/try:** `try/except/finally` with allowed exceptions (see §10). `with` is disallowed.

### Disallowed (validator rejects)
- `import` of anything outside `stdlib` (e.g., `os`, `sys`, `time`, `random`, `hashlib`, `typing`, `inspect`, `ctypes`, `numpy`, etc.)
- Dynamic code: `eval`, `exec`, `compile`, `getattr`/`setattr` on user strings, `__import__`.
- Reflection & meta: `globals`, `locals`, `vars`, `object.__dict__` access, decorators that capture function objects dynamically.
- Generators / coroutines: `yield`, `async/await`, context managers.
- Comprehensions that close over non-deterministic iterables.
- **Recursion:** direct or indirect recursion is forbidden (depth analysis is non-deterministic across VMs).
- **Sets/frozensets:** disallowed (iteration order depends on salted hashing).
- **Dicts used for iteration without explicit ordering:** permitted to construct/use, but **iteration requires explicit sorting** (see §5).
- Floating-point, complex numbers, decimal/fractions.
- Bit-length breaking ops via shifting to exceed numeric limits (checked at runtime, see §4).

---

## 3) Builtins & standard library

A **positive allowlist** of Python builtins is enabled; anything not listed is unavailable.

- **Canonically allowed builtins** are defined in `vm_py/compiler/builtins_allowlist.py` and include safe total functions like: `len`, `range`, `min`, `max`, `abs`, `all`, `any`, `sum` (on small lists), `enumerate`, `zip`, `sorted`, `reversed`, `bytes`, `bytearray`, `int`, `bool`, `list`, `tuple`, `ord`, `chr` (ASCII), `hex`, `int.from_bytes`, `int.to_bytes`.
- **Forbidden builtins:** `open`, `print`, `hash`, `id`, `dir`, `super`, `vars`, `eval`, `exec`, `getattr`/`setattr`, `__import__`, etc.

> **Normative:** The allowlist file is the source of truth. The validator cross-checks imports and builtins against it.

---

## 4) Numeric model & limits

- **Integer-only arithmetic** (unbounded in theory) but **capped** by configuration to ensure resource bounds:
  - Max integer bit width and intermediate bit width
  - Max exponent for `pow` (with/without modulus)
  - Max length for `bytes`/`bytearray` values
- Limits are specified in `vm_py/config.py` (feature-flagged by network). Exceeding a limit raises `VmError` → revert.
- **Division semantics:** integer floor division only (`//`). True division (`/`) is disabled.
- **Bitwise ops:** `& | ^ ~ << >>` allowed within bit-width caps.
- **No floats or NaN/Inf** anywhere in ABI or runtime.

---

## 5) Collections & ordering

- **Lists/tuples:** iteration order is insertion order → deterministic.
- **Dicts:** Python preserves insertion order. However, **any iteration over dict keys/items/values must be preceded by an explicit, deterministic ordering** (e.g., `for k in sorted(d.keys())`), or the validator will reject patterns known to rely on unspecified ordering.
- **Sets/frozensets:** **disallowed** (see §2).
- **String/bytes encoding:** `str` is logically UTF-8; ABI and storage use **bytes**. Any `str` crossing the ABI boundary is encoded/decoded as UTF-8 with explicit errors policy (see ABI spec).

---

## 6) Hashing & equality

- Python’s `hash()` is **disabled** (randomized per-process). Use stdlib hashing APIs:
  - `stdlib.hash.keccak256`, `stdlib.hash.sha3_256`, `stdlib.hash.sha3_512`
- Equality and ordering of bytes/ints/bools follow Python semantics; no locale or platform dependence.

---

## 7) Time, randomness, environment

- **Time:** No wall-clock, monotonic clock, or block-time syscalls exist in the VM. Block/Tx context (height, coinbase, chainId) is provided via the runtime context objects; these are deterministic inputs (see `vm_py/runtime/context.py`).
- **Randomness:** Use `stdlib.random` which is **deterministic** and **seeded from the enclosing tx hash** (see `vm_py/runtime/random_api.py`). There is **no** ambient entropy source.
- **Environment:** No access to env vars, process info, platform flags.

---

## 8) Syscalls & capabilities

Contracts may call a small set of **host capabilities** exposed via **`stdlib`**:

- **Storage:** `stdlib.storage.get/set` (deterministic key/value, bytes only)
- **Events:** `stdlib.events.emit(name: bytes, args: dict[bytes, bytes|int])` (ordering rules in §11)
- **Hashing:** `stdlib.hash.{keccak256, sha3_256, sha3_512}`
- **ABI helpers:** `stdlib.abi.{require, revert, encode, decode}` (deterministic)
- **Treasury (local sim):** `stdlib.treasury.{balance, transfer}` (no external I/O)
- **Capabilities stubs:** `stdlib.syscalls.{blob_pin, ai_enqueue, quantum_enqueue, ...}` are **deterministic shims** that record requests and **never** perform non-deterministic actions during the same block. Results are consumed **next block** via proofs (outside VM). See `capabilities/` module.

All syscalls are **metered** by gas and **guarded** with input-size caps and determinism checks.

---

## 9) ABI determinism

- ABI encoding is a canonical, length-prefixed bytes format defined in `vm_py/specs/ABI.md`.
- Function dispatch is by method selector (stable) and validated types; no reflection on names at runtime.
- **No platform-dependent encodings.** Endianness and integer sizes are fixed by the ABI.

---

## 10) Errors & reverts

- **Programmatic revert:** use `stdlib.abi.revert(reason: bytes)`; consumes gas as specified but preserves determinism.
- **Assertions:** `stdlib.abi.require(cond, reason)` is preferred; Python `assert` is disallowed.
- **Exceptions:** Only VM-defined exceptions may be caught (`VmError`, `ValidationError`, `OOG`, `Revert`) and are delivered deterministically. Catch-all `except Exception:` is allowed but discouraged; it cannot mask OOG.
- **Out-of-gas (OOG):** raised by the gas meter; always reverts the current call with deterministic receipt.

---

## 11) Events & side effects

- **Event ordering** is the order of `emit` calls within the transaction execution trace; fully deterministic.
- Event payloads must be ABI-encodable. Event logs contribute to the logs hash/bloom deterministically (see `execution/receipts/logs_hash.py`).

---

## 12) Storage semantics

- Keys and values are **bytes**; helpers exist for int<→bytes conversions with fixed endianness.
- Reads of unwritten keys return empty bytes.
- Storage writes are journaled and committed only on successful completion of the call (see `execution/state/journal.py` semantics mirrored in local VM sim).

---

## 13) Gas & resource bounds

- Every opcode / stdlib call has a fixed or input-dependent cost defined by the resolved gas table (`vm_py/gas_table.json`). The estimator provides conservative static upper bounds for tooling.
- Input-dependent costs (e.g., hashing, encoding) are simple affine functions in input length and are **network-configurable** but deterministic.
- **Looping** is permitted but must terminate under gas; infinite loops deterministically OOG.

---

## 14) Packaging & imports

- A contract package includes source and `manifest.json` (ABI + metadata). The loader (`vm_py/runtime/loader.py`) validates, compiles to IR, and links the **synthetic stdlib**. No other imports are possible.

---

## 15) Versioning & feature flags

- VM version is surfaced via `vm_py/version.py`. Any change to IR encoding, ABI bytes, gas semantics, or validator rules **must bump** the version.
- A **strict mode** flag in `vm_py/config.py` may tighten caps or reject constructs that are borderline deterministic; networks may enable stricter presets.

---

## 16) Conformance checklist

Implementations MUST:

1. Reject forbidden imports, builtins, constructs (validator).
2. Enforce numeric and size caps at parse and runtime.
3. Meter every step according to the gas table; raise OOG deterministically.
4. Provide only the synthetic `stdlib` and its deterministic syscalls.
5. Ensure ABI encode/decode stability and byte-for-byte equality across platforms.
6. Forbid Python `hash()`; route all hashing through `stdlib.hash`.
7. Disallow sets and unordered iteration; require explicit ordering for dict iteration.
8. Disallow recursion.
9. Ensure events, storage writes, and receipts are reproducible from inputs.

---

## 17) Rationale (informative)

- **No floats:** differences in rounding modes and NaN handling break consensus.
- **No sets/unordered dict iteration:** salted hashing perturbs iteration order.
- **No recursion:** Python recursion depth and error paths are interpreter-dependent.
- **Deterministic PRNG:** reproducible simulations and verified transcripts.
- **Synthetic stdlib:** tight surface area for auditing and gas metering.

For examples that comply with these rules, see `vm_py/examples/*` and the unit tests under `vm_py/tests/`.

