# Animica ABI (v1)
_Function dispatch, argument/return encoding, and selector rules for the Python-VM._

This ABI is used by:
- the **VM stdlib** (`vm_py/runtime/abi.py`)
- the **compiler/runtime helpers** (`vm_py/abi/encoding.py`, `vm_py/abi/decoding.py`)
- client SDKs (Python/TS/Rust) and RPC payload helpers

It is deliberately **simple, deterministic, self-delimiting**, and easy to implement in any language.

---

## 1) Overview

- A **call payload** = `selector(8 bytes) || args-encoding`.
- A **return payload** = `rets-encoding` (no selector).
- **Events** encode arguments with the same tuple/list rules (see §7).

The schema for functions (names, input/output types) lives in the contract **manifest** and SDK metadata; encoding does **not** carry field names or runtime type tags (types are known from the manifest).

---

## 2) Types

Supported ABI types:

- `int` — unbounded signed integer (network caps apply in the VM; ABI is value-agnostic).
- `bool` — logical `false/true`.
- `bytes` — arbitrary byte string.
- `address` — raw address payload (33 bytes: `alg_id || sha3_256(pubkey)`).
- `list<T>` — homogeneous list of `T`.
- `tuple(T1,...,Tn)` — fixed-arity product type (used for function I/O).

> Strings are represented as `bytes` with UTF-8 content by convention.

---

## 3) Building the selector (8 bytes)

Each callable function has a **selector**:

selector = sha3_256(“animica:abi:v1|” + canonical_signature_bytes)[:8]

- `canonical_signature` text form:

name “(” paramTypeId [ “,” paramTypeId ]* “)”

Examples:
- `inc()` → `sha3_256("animica:abi:v1|inc()")[:8]`
- `set(bytes)` → `sha3_256("animica:abi:v1|set(bytes)")[:8]`
- `transfer(address,int)` → `sha3_256("animica:abi:v1|transfer(address,int)")[:8]`
- Nested types use bracketed forms: `list<int>`, `tuple(int,bytes)`, `list<tuple(address,int)>`

- Type identifiers (no spaces, lowercase):
- `int`, `bool`, `bytes`, `address`, `list<…>`, `tuple(…)`

> Using 8 bytes (64-bit) virtually eliminates collisions while keeping payloads compact. Collisions across functions in one contract MUST be rejected at deploy/compile time.

---

## 4) Primitive encodings

All sequences are **concatenations** of field encodings in order. No extra framing is added to tuples (arity is known from the manifest).
We use two standard building blocks:

- **uvarint** — unsigned LEB128 (base-128 little-endian) variable-length integer.
- **zigzag** — signed ↔ unsigned mapping: `zz(x) = (x << 1) ^ (x >> 63...)`, inverse restores sign.

### 4.1 `bool`

0x00 = false
0x01 = true

Any other byte is invalid.

### 4.2 `int`

encode_int(x) = uvarint( zigzag(x) )
decode_int()  = zigzag^-1( uvarint() )

This is canonical (no leading zeros, shortest LEB128 form).

### 4.3 `bytes`

encode_bytes(b) = uvarint(len(b)) || b
decode_bytes()  = read len via uvarint, then read len bytes

### 4.4 `address`
Encoded as a length-prefixed `bytes` but MUST be exactly 33 bytes:

encode_address(a) = uvarint(33) || a[33]

Decoders MUST reject lengths ≠ 33.

---

## 5) Composite encodings

### 5.1 `tuple(T1,...,Tn)`
Concatenate encodings of each component **without** extra framing:

encode_tuple(x1..xn) = enc(T1,x1) || … || enc(Tn,xn)

Decoding uses the known arity/types from the manifest.

### 5.2 `list<T>`

encode_list([e1..eN]) = uvarint(N) || enc(T,e1) || … || enc(T,eN)

Empty list = single byte `0x00`.

> Nested lists/tuples compose recursively (e.g., `list<tuple(address,int)>`).

---

## 6) Function call & return payloads

### 6.1 Call payload

payload = selector[8] || encode_tuple(args…)

- The VM/SDK locates the function by selector, validates arg count/types, and decodes using the manifest types.
- Unknown selectors MUST raise `VmError` / `AbiError`.

### 6.2 Return payload

payload = encode_tuple(rets…)

- For zero returns, payload is the empty byte string.
- Reverts are not ABI payloads; they surface via receipt status and (optional) revert reason bytes (see §8).

---

## 7) Events

Contracts emit events through stdlib (`stdlib.events.emit(name: bytes, args: dict)` in the VM).

- **Topic**: free-form `bytes` name chosen by the contract (recommend ASCII).
- **Args encoding**: a single `tuple(...)` agreed by the contract’s ABI for that event.
- **Wire**: events are recorded in the execution logs and **encoded** as:

uvarint(len(topic)) || topic || encode_tuple(args…)

Explorer/SDKs use the ABI to decode the argument layout of known events.

> Event arg **names** are metadata (for UIs) and are not encoded on-chain.

---

## 8) Errors & reverts

- VM `REVERT` encodes a **reason** as `bytes` (free-form) stored in the receipt/log domain, **not** as a function return.
- SDKs expose this as an exception with `reason: bytes` if present.
- Reason bytes are encoded as `encode_bytes(reason)` where recorded (e.g., logs/receipts), but **not** part of ABI return payloads.

---

## 9) Examples

### 9.1 `inc() -> int`
- Selector: `sha3_256("animica:abi:v1|inc()")[:8]`
- Args: none → empty tuple
- Returns: one `int`

call:  [8-byte selector]
ret:   encode_int(n)

### 9.2 `set(bytes) -> ()`

call:  selector || encode_bytes(b)
ret:   “”   (empty)

### 9.3 `transfer(address,int) -> bool`

call:
selector
|| uvarint(33) || [33 address bytes]
|| uvarint( zigzag(amount) )
ret:
0x00 or 0x01

### 9.4 `batch_set(list<tuple(bytes,bytes)>) -> int`
Let `pairs = [(k1,v1),(k2,v2)]`:

args encoding =
uvarint(2)                              ; list length
; tuple(bytes,bytes) #1
uvarint(len(k1)) || k1 || uvarint(len(v1)) || v1
; tuple(bytes,bytes) #2
uvarint(len(k2)) || k2 || uvarint(len(v2)) || v2

Return is a single `int` (e.g., number of updated keys).

---

## 10) Determinism & canonicalization rules

Implementations MUST:

1. Use **shortest** LEB128 encodings for `uvarint` (no redundant 0x80 groups).
2. Enforce `address` length = **33**.
3. Reject malformed inputs early (e.g., unterminated LEB128, overlong encodings, length overflows).
4. Decode tuples strictly by manifest types/arity; extra bytes at end are invalid for calls (SDK may allow trailing bytes only if mandated by higher-level envelopes, never inside ABI).
5. Avoid implicit widening/narrowing; `int` is arbitrary precision but subject to VM caps during execution, not during ABI decode.

---

## 11) Manifest linkage (dispatch table)

- At **compile/deploy**, the toolchain computes each function’s selector and builds a **dispatch table**.
- On **call**, the VM/host finds the entry by selector, loads the type vector, and decodes the tuple accordingly.
- If two functions collide on selector (same contract), the compiler MUST fail.

---

## 12) Pseudocode (reference)

### 12.1 uvarint (LEB128)
```python
def uvarint_encode(n: int) -> bytes:
    if n < 0: raise ValueError
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(0x80 | b)
        else:
            out.append(b)
            return bytes(out)

def uvarint_decode(buf, i=0):
    n = 0; shift = 0
    while True:
        if i >= len(buf): raise ValueError("truncated")
        b = buf[i]; i += 1
        n |= ((b & 0x7F) << shift)
        if (b & 0x80) == 0: return n, i
        shift += 7
        if shift > 63*2: raise ValueError("overflow")

12.2 zigzag

def zigzag(n: int) -> int:
    return (n << 1) ^ (n >> (n.bit_length() or 1))

def unzigzag(u: int) -> int:
    return (u >> 1) ^ -(u & 1)


⸻

13) Versioning
	•	This document defines ABI v1. Changes to selector text, primitive encodings, or list/tuple framing would require a new version string (e.g., animica:abi:v2|…) and a minor/major bump in vm_py/version.py.

⸻

14) Test vectors

See vm_py/fixtures/abi_examples.json and SDK test suites for cross-language conformance vectors covering:
	•	scalars (±ints, bools), bytes (0..large), address round-trips,
	•	nested lists/tuples,
	•	selector derivation and collision checks.

