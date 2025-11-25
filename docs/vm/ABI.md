# VM(Py) ABI — Call/Data Encoding & Event Topics

**Status:** Stable (v1)  
**Audience:** Contract authors, SDK implementers, node/runtime integrators  
**Scope:** Canonical, deterministic byte encoding for function calls/returns and event topics used by VM(Py).

> TL;DR: Calls and returns use a small, self-contained codec (no JSON/CBOR). Scalars are length-prefixed where variable, big-endian for integers, and strictly bounded. Events have a stable topic scheme and carry canonical data bytes.

---

## 1) Design Goals

- **Deterministic & canonical.** Identical inputs → identical bytes and gas everywhere.
- **Small surface.** A few scalar types + tuples/arrays, no dynamic reflection or schemas at runtime.
- **Language-agnostic.** Friendly to Python/TS/Rust SDKs; no dependency on host endianness.
- **Stable function selection.** Collision-resistant selector tied to the ABI signature.

---

## 2) Primitives

This ABI defines a compact wire format with the following building blocks:

### 2.1 Unsigned Varint (`uvarint`)
- **Format:** LEB128 (little-endian base-128) for non-negative integers.
- **Range:** 0…2^64-1 for lengths/counts; larger values are invalid.
- **Determinism:** Minimal form only (no leading 0x80 “continuations” beyond necessity).

### 2.2 Integers (`int`)
- **Domain:** Unsigned, 0…2^256-1 (cap enforced by validator and runtime).
- **Encoding:** `uvarint(L)` followed by **big-endian** `L` bytes.  
  - **Zero** is encoded with `L=0` and **no** payload bytes.
  - **Canonical:** No leading zero byte permitted (except zero above).

### 2.3 Boolean (`bool`)
- Single byte: `0x00` (False) or `0x01` (True). Any other value invalid.

### 2.4 Bytes (`bytes`)
- `uvarint(len)` + raw bytes; `len` may be 0. Max length is policy-bound (see caps).

### 2.5 Address (`address`)
- **Logical form:** (alg_id: `u8`, pubkey_hash: `sha3_256` 32-byte).
- **Binary payload:** 33 bytes = `alg_id || pubkey_hash`.
- **Encoding:** As **bytes** with `len=33`.  
  - SDKs map bech32m `anim1…` ⇄ 33-byte payload; on-chain is always the 33-byte binary.

### 2.6 Arrays / Tuples
- **Tuple:** `uvarint(n)` then `encode(v0) … encode(v{n-1})` (positional; heterogeneous allowed).
- **Array[T]:** `uvarint(n)` then `encode_T` repeated (homogeneous).  
  - **Canonical:** `n` must match actual element count; nested caps apply recursively.

> **No strings** at the ABI level; use `bytes` with a known text encoding if needed (e.g., UTF-8).

---

## 3) Function Selectors & Call Data

### 3.1 Function Selector (`selector`)
- **Signature string:**  
  `name "(" comma_join(param_types) ")" "->" comma_join(return_types)`  
  Examples:
  - `inc()->`
  - `get()->int`
  - `transfer(address,int)->bool`
- **Selector:** First **8 bytes** of `sha3_256( b"fn:" || signature_utf8 )`.
  - 64-bit truncation balances space with negligible collision risk in practice.
  - Stable across language/toolchain updates if the ABI signature is constant.

### 3.2 Call Data

call_data := selector(8) || args_tuple
args_tuple := tuple encoded via §2.6 with each argument encoded by its type rules

- **No additional length prefix** around the whole call.  
- **Empty args** → `tuple(n=0)` (encoded as a single `uvarint(0)`).

### 3.3 Return Data

ret_data := results_tuple

- `results_tuple` follows the **same** tuple rules as arguments.

### 3.4 Errors & Reverts
- `abi.require(cond, msg)` and `abi.revert(msg)` cause the VM to signal REVERT.
- **Return bytes on REVERT:** `bytes(msg_utf8)` (ABI-encoded bytes) are available to callers/simulators.
- On-chain, REVERT status is reflected in receipts; payload may appear in debug/SIM RPCs.

---

## 4) Events: Topics & Data

Contracts emit events with:
```python
events.emit(name: bytes, args: dict[str, <ABI value>])

4.1 Topic Scheme

To keep topics compact and filterable while avoiding oversized logs:
	•	topic[0] = sha3_256( b"event:" || name )[:32]
	•	topic[1] = sha3_256( canonical_args_bytes )[:32]

Where:
	•	canonical_args_bytes = ABI encoding of a stable map representation:
	1.	Sort args by key (bytewise ascending of key.encode('utf-8')).
	2.	Encode as a tuple of pairs: uvarint(k) entries of (bytes(key_utf8), encode(value)).

Rationale: topic[0] gives a stable identity per event kind; topic[1] enables quick bloom/filtering for “this exact payload” without exploding topic count. Full details live in log data (below).

4.2 Event Data
	•	data = the same canonical_args_bytes described above.
	•	Determinism: Keys must be valid UTF-8; duplicate keys are invalid.

Bloom and receipts layout are specified in docs/spec/RECEIPTS_EVENTS.md; this doc only fixes event ABI bytes.

⸻

5) Examples & Test Vectors

5.1 Scalars
	•	int = 0
	•	L=0 → 00
	•	int = 0x01
	•	L=1 → 01 | 01
	•	int = 0x0102
	•	L=2 → 02 | 01 02
	•	bool True / False
	•	01 / 00
	•	bytes(b””) → 00
	•	bytes(b”\xDE\xAD”) → 02 | DE AD
	•	address (alg_id=0x01, hash=00…0F 32 bytes)
→ 21 | 01 00 00 … 0F (0x21 = 33)

5.2 inc() (no args, no return)
	•	selector: sha3_256("fn:inc()->")[:8]
	•	Suppose selector = 12 34 56 78 9A BC DE F0
	•	args_tuple: uvarint(0) → 00
	•	call_data hex: 12 34 56 78 9A BC DE F0 00

5.3 get()->int (no args, returns an int)
	•	selector: sha3_256("fn:get()->int")[:8] → e.g., AA…
	•	args_tuple: 00
	•	ret_data (e.g., returns 1): int(1) → 01 | 01

5.4 Event

events.emit(b"Inc", {"value": 1})

	•	topic[0] = sha3_256(b"event:Inc")
	•	Canonical args bytes:
	•	Sorted pairs: only ("value", 1)
	•	Tuple count uvarint(1) = 01
	•	key bytes("value") = 05 | 76 61 6C 75 65
	•	value int(1) = 01 | 01
	•	data hex: 01 05 76 61 6C 75 65 01 01
	•	topic[1] = sha3_256(data)

⸻

6) Caps & Limits (enforced by validator/runtime)
	•	int: ≤ 256 bits.
	•	bytes: max length configurable; defaults recommended ≤ 64 KiB per value.
	•	tuples/arrays: max element count (e.g., 1024) and nesting depth (e.g., 8).
	•	event args total encoded size: bounded by receipt/logs policy.

Exact values are network policy; see execution/specs/GAS.md and docs/spec/CAPABILITIES.md for gas implications.

⸻

7) SDK Mapping (Guidance)
	•	Python:
	•	int ⇄ Python int with bound checks.
	•	bytes ⇄ bytes.
	•	address ⇄ helper struct with alg_id and hash, plus bech32m codec.
	•	TypeScript:
	•	int as bigint; encode to BE bytes without leading zero; zero → empty bytes w/ L=0.
	•	bytes as Uint8Array.
	•	address as { alg: number; hash: Uint8Array(32) } plus bech32m conversion.
	•	Rust:
	•	int as Uint<256> or biguint checked; serialize BE.
	•	address as struct { alg: u8, hash: [u8;32] }.

⸻

8) Compatibility Notes
	•	ABI is not EVM ABI: no 32-byte slot alignment, no function name hashing to 4 bytes, no packed/strict sizing quirks.
	•	Function selector uses 8 bytes from SHA3-256 and includes returns in the signature to minimize accidental reuse.
	•	The ABI is stable at v1; any breaking change will bump the major version and gate via network upgrades.

⸻

9) Reference Implementations
	•	Encoder/decoder utilities in:
	•	vm_py/runtime/abi.py, vm_py/abi/{encoding,decoding}.py
	•	SDKs: sdk/python/omni_sdk/tx/encode.py, sdk/typescript/src/tx/encode.ts, sdk/rust/src/tx/encode.rs
	•	Event helpers: execution/state/events.py, execution/receipts/logs_hash.py

⸻

10) Validation Checklist
	•	Integers minimally encoded (no leading zeros, zero uses L=0).
	•	Arrays/tuples counts correct and within caps.
	•	Address is exactly 33 bytes (alg_id + hash).
	•	Function selector matches the signature including returns.
	•	Event args keys are unique, UTF-8, lexicographically sorted in the canonical payload.

