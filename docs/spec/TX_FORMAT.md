# TX_FORMAT — Envelope, Signatures, Access Lists, Fee Fields

This document specifies the **transaction (Tx) wire format** used by Animica full nodes, wallets, RPC, and SDKs. It covers the canonical fields, the signing domain, the supported **kinds** (`transfer`, `deploy`, `call`), **access lists**, and **fee accounting**.

Authoritative machine-readable schemas live in:
- `spec/tx_format.cddl` (CBOR)
- `spec/abi.schema.json` (ABI payloads referenced by `data`)
- Code references: `core/types/tx.py`, `core/encoding/*`, `execution/*`, `mempool/*`, `rpc/methods/tx.py`

See also: `spec/ENCODING.md` (deterministic CBOR & domain tags), `spec/ADDRESSES.md` (bech32m), and `execution/specs/*`.

---

## 1) Overview

A transaction is a **CBOR map** with lexicographically sorted ASCII keys (deterministic), containing:
- **Unsigned envelope** fields (chain, nonce, fees, kind-specific data)
- A **signature** object (PQ scheme) over canonical **SignBytes** (the unsigned envelope)
- Optional **access list** aiding deterministic gas and future parallel execution

### 1.1 Object identity
- **TxID** (transaction hash) = `sha3_256( CBOR(signed_tx) )`
- **SignBytes hash** = `sha3_256( ASCII("animica/tx-v1") || 0x00 || CBOR(unsigned_tx) )` (see `spec/ENCODING.md`)

---

## 2) Top-level fields (v1)

| Key          | Type                 | Required | Description |
|--------------|----------------------|---------:|-------------|
| `chainId`    | `u32`                | ✅       | Network/chain id (see `spec/chains.json`) |
| `from`       | `address (bech32m)`  | ✅       | Sender account address (`anim1…`) derived from PQ public key (see `spec/ADDRESSES.md`) |
| `nonce`      | `u64`                | ✅       | Per-sender monotonic counter |
| `gasLimit`   | `u64`                | ✅       | Max gas the sender authorizes |
| `gasPrice`   | `u64` (attoANM)      | ✅       | Price per gas unit; v1 uses a simple **base/tip** split at execution time |
| `kind`       | `tstr`               | ✅       | One of: `"transfer"`, `"deploy"`, `"call"` |
| `to`         | `address or null`    | ◻️       | Target address; **`null` for `deploy`** |
| `value`      | `u256`               | ◻️       | Amount to transfer (attoANM). For `deploy`/`call` optional and passed to callee balance |
| `data`       | `bstr`               | ◻️       | Kind-specific payload: empty for plain transfer; ABI-encoded call for `call`; code+manifest bundle or reference for `deploy` |
| `accessList` | `AccessList`         | ◻️       | Optional access list (see §4) |
| `blobs`      | `array(BlobRef)`     | ◻️       | Optional Data-Availability references (commitments); gas/size limits apply |
| `signature`  | `Signature`          | ✅       | PQ signature over **unsigned** fields |

> **Omit** optional fields when empty; do **not** send `null` unless the schema specifies (e.g., `to=null` for `deploy`). No floats, no indefinite-length items.

### 2.1 Types

**Addresses**  
See `spec/ADDRESSES.md`. Bech32m string with HRP `anim`. Payload = `alg_id(u16_be) || sha3_256(pubkey)`.

**BlobRef (informative, v1)**  
A minimal reference to DA commitments when a tx needs to “pin” blobs in the same block:
```cbor
BlobRef = {
  ns: uint,                 ; namespace id
  commit: bstr .size 32,    ; NMT root (commitment)
  size: uint                ; total bytes of blob
}

Gas/size checks are enforced by execution/adapters/da_caps.py (stub in v1).

⸻

3) Signature (PQ)

Signature = {
  alg_id: uint,         ; PQ signature scheme id (see pq/alg_ids.yaml)
  pubkey: bstr,         ; raw scheme-specific public key bytes
  sig: bstr             ; signature bytes
}

Verification rules
	1.	Recompute SignBytes hash from the unsigned envelope (keys sorted, deterministic CBOR; see §5).
	2.	Verify sig under alg_id and pubkey.
	3.	Check from == bech32m( alg_id || sha3_256(pubkey) ).
	4.	Check chainId matches the local node’s network.
	5.	Enforce signature size limits per pq/ scheme (DoS protection).

If any step fails → InvalidTx(SignatureError).

⸻

4) Access Lists

Access lists help estimate gas and enable deterministic parallel scheduling.

AccessList = [
  { address: tstr, storageKeys: [ bstr, ... ] },
  ...
]

	•	address: bech32m (anim1…).
	•	storageKeys: zero or more 32-byte keys the tx expects to touch.
	•	Execution may expand actual accesses; access lists are hints, not hard caps, in v1.
	•	Gas: a small discount applies for pre-declared keys (see execution/gas/table.py).

⸻

5) SignBytes (domain-separated)

The unsigned transaction used for signing is formed by omitting signature from the map and then CBOR-encoding the result with canonical rules. The preimage is:

preimage = ASCII("animica/tx-v1") || 0x00 || CBOR(unsigned_tx)
sign_hash = sha3_256(preimage)

Do not include signature or any non-canonical fields in unsigned_tx. See spec/ENCODING.md for map ordering and integer minimal encoding rules.

⸻

6) Kinds and intrinsic constraints

6.1 transfer
	•	Required: to, value
	•	data must be empty (zero-length)
	•	Intrinsic gas: G_tx + G_value (see execution/gas/intrinsic.py)

6.2 deploy
	•	to = null
	•	data carries a deploy bundle:
	•	Either CBOR-encoded { code: bstr, manifest: JSON (per spec/manifest.schema.json) }
	•	Or a pre-validated package reference (future)
	•	value optional (initial balance of the created account)
	•	Address creation is deterministic (implementation-defined salt/derivation in execution/runtime/contracts.py)
	•	Intrinsic gas: G_tx + G_deploy_base + len(code) * G_codebyte

6.3 call
	•	Required: to
	•	data is ABI-encoded function selector + args, validated against the target ABI if available
	•	value optional
	•	Intrinsic gas: G_tx + G_call_base + len(data) * G_calldata_byte

Exact constants live in spec/opcodes_vm_py.yaml and resolved in execution/gas/table.py.

⸻

7) Fees and gas (v1)
	•	Sender prepays gasLimit * gasPrice from from balance (checked before execution).
	•	At block execution, the chain computes a baseFee (EMA + clamps; see mempool/fee_market.py and execution/runtime/fees.py).
	•	Effective tip to the block coinbase = gasPrice - baseFee (clamped at ≥ 0).
	•	Final deduction:
	•	Burn or treasury split of gasUsed * baseFee (policy-defined)
	•	Tip credit to coinbase: gasUsed * tip
	•	Refund to sender of unused gas: (gasLimit - gasUsed) * gasPrice
	•	If OOG (out-of-gas), state reverts; gas consumed is charged.

v1 intentionally uses single-field gasPrice. A future v2 may add maxFeePerGas / maxPriorityFeePerGas. Such a change would bump the signing domain (e.g., animica/tx-v2).

⸻

8) Validation checklist (admission)

Nodes MUST reject a tx if any of the following fail:
	1.	Encoding: non-canonical CBOR (wrong map order, indefinite length, floats) — see spec/ENCODING.md.
	2.	Signature: invalid per §3/§5, or from mismatch.
	3.	Chain: chainId != node chain.
	4.	Nonce: nonce < account nonce (old), or exceeds policy window (huge gaps).
	5.	Balance: balance(from) >= gasLimit * gasPrice (+ value for transfer/call/deploy).
	6.	Gas limits: gasLimit < intrinsicGas(tx) OR exceeds network maxima.
	7.	Kind constraints: e.g., transfer must have empty data; deploy must have to=null.
	8.	AccessList: malformed entries (bad address, non-32B keys).
	9.	Blobs: commitment size/namespace invalid (if present) or exceeds block/tx caps.
	10.	Size: CBOR-encoded tx too large (DoS guard; policy-bound).

⸻

9) Canonical JSON projection (informative)

{
  "chainId": 1,
  "from": "anim1qxyz...",
  "nonce": 12,
  "gasLimit": 250000,
  "gasPrice": "2000000000",          // attoANM, decimal string in JSON
  "kind": "call",
  "to": "anim1abc...",
  "value": "0",
  "data": "0x36cbbd9d00000000...",    // ABI-encoded call
  "accessList": [
    { "address": "anim1abc...", "storageKeys": ["0x0000..."] }
  ],
  "blobs": [
    { "ns": 24, "commit": "0x5a..", "size": 262144 }
  ],
  "signature": {
    "alg_id": 1,
    "pubkey": "0xA4…",
    "sig": "0xB7…"
  }
}


⸻

10) CBOR diagnostic example (unsigned envelope)

{
  "chainId": 1,
  "from": "anim1qxyz…",
  "nonce": 12,
  "gasLimit": 250000,
  "gasPrice": 2000000000,
  "kind": "transfer",
  "to": "anim1abc…",
  "value": 750000000000000000,  ; 0.75 ANM
  "data": h'',
  "accessList": []
}

The signed tx is the same map plus "signature": {...} appended, but note that CBOR map key ordering remains lexicographic; implementations must sort keys.

⸻

11) Versioning & forwards-compat
	•	Any field addition/removal that changes SignBytes requires a new domain (e.g., animica/tx-v2) and chain activation.
	•	Wallets/SDKs must refuse to sign unknown versions unless explicitly opted in.
	•	Nodes may accept superset fields only after activation and schema update.

⸻

12) References
	•	spec/ENCODING.md, spec/ADDRESSES.md
	•	spec/opcodes_vm_py.yaml and execution/gas/table.py
	•	execution/runtime/* (fees, dispatcher, receipts)
	•	mempool/* (fee market, admission)
	•	core/types/tx.py, rpc/methods/tx.py
	•	da/* (blob commitments; optional)

