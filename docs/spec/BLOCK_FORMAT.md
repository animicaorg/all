# BLOCK_FORMAT — Header Fields, Roots, Θ/Γ, Proof Pack Format

This document specifies the **block/header** format used by Animica nodes. It defines the canonical header fields, all consensus roots, how **Θ** (target threshold) and **Γ** (per-block useful-work capacity cap) are represented and enforced, and the **proof pack** envelope carried alongside transactions.

Authoritative machine-readable schemas live in:
- `spec/header_format.cddl` — CBOR schema for the header
- `spec/blob_format.cddl` — DA blob & NMT rules (for `daRoot`)
- `proofs/schemas/*.cddl` — proof envelopes and receipts
- Code references: `core/types/header.py`, `core/types/block.py`, `consensus/*`, `proofs/*`, `da/*`

See also: `spec/TX_FORMAT.md`, `spec/ENCODING.md`, and `consensus/README.md`.

---

## 1) High-level structure

A **Block** is a tuple:

Block {
header: Header,                   ; canonical fields & roots
txs: [ Tx, … ],                 ; CBOR transactions (signed)
proofs: [ ProofEnvelope, … ],   ; useful-work proofs (hash/AI/quantum/storage/VDF)
receipts?: [ Receipt, … ]       ; optional in-body receipts (may be omitted on wire)
}

**Object identity**
- `blockHash = sha3_256( CBOR(header) )` (the header is the commitment to all other parts via roots)
- Block number/height increases by 1 from parent.

---

## 2) Header fields (v1)

A header is a **CBOR map** with deterministic (lexicographic) key ordering.

| Key           | Type          | Required | Description |
|---------------|---------------|---------:|-------------|
| `parentHash`  | `bstr(32)`    | ✅       | Hash of parent header (`sha3_256(CBOR(parent))`) |
| `number`      | `u64`         | ✅       | Height (genesis = 0) |
| `timestamp`   | `u64` (sec)   | ✅       | Wall-clock seconds since Unix epoch (producer-declared; bounded drift) |
| `chainId`     | `u32`         | ✅       | Network id (must match node) |
| `stateRoot`   | `bstr(32)`    | ✅       | Merkle root of post-state after applying txs (see §3.1) |
| `txRoot`      | `bstr(32)`    | ✅       | Merkle root of transactions (order-preserving canonicalization) |
| `receiptsRoot`| `bstr(32)`    | ✅       | Merkle root of receipts/logs summary (see §3.3) |
| `proofsRoot`  | `bstr(32)`    | ✅       | Merkle root of **proof receipts** (not raw proofs) |
| `daRoot`      | `bstr(32)`    | ✅       | Namespaced Merkle Tree (NMT) root over DA blobs in block |
| `mixSeed`     | `bstr(32)`    | ✅       | Mixing seed (prev beacon ⊕ parent mix ⊕ VDF/QRNG mix if present) |
| `nonce`       | `bstr(8)`     | ✅       | Miner nonce domain (binds header to HashShare `u` draws) |
| `theta`       | `u64`         | ✅       | **Θ** micro-nats threshold (target) used for acceptance in this height |
| `policyRoots` | `map`         | ✅       | Pin policy roots: `{ "poies": bstr(32), "algPolicy": bstr(32) }` |
| `extraData`   | `bstr`        | ◻️       | Opaque payload (size-capped) |
| `version`     | `u16`         | ✅       | Header format version (v1 = 1) |

> **Genesis** MUST set `parentHash = 0x00..00`, `number=0`, and provide canonical initial roots per `core/genesis/loader.py`.

---

## 3) Roots & how they are computed

### 3.1 `stateRoot`
Deterministic Merkle root over the **sorted** key/value state at end of block application. Built by `core/chain/state_root.py`. keys are canonical byte keys; values are canonical CBOR encodings of account/storage records.

### 3.2 `txRoot`
Merkle over the CBOR-encoded **signed txs** in block order. Leaves = `sha3_256(CBOR(tx))`. The tree is canonical and stable (see `core/utils/merkle.py`).

### 3.3 `receiptsRoot`
Merkle over **execution receipts**: each is the canonical CBOR of `execution/types/result.py` → `execution/receipts/encoding.py`. The **order matches tx order** 1:1.

### 3.4 `proofsRoot`
Merkle over **proof receipts** (compact records) rather than the full `ProofEnvelope`s:
- For each accepted proof, `proofs/receipts.py` builds a minimal receipt (type id, nullifier, ψ-inputs, metering).
- Leaves = `sha3_256(CBOR(ProofReceipt))`.
- The `proofs` list in the block body is redundant for consensus; nodes may discard raw proofs after verifying & materializing receipts.

### 3.5 `daRoot`
NMT root over all **blob shares** committed in this block (see `da/nmt/*`, `da/blob/commitment.py`). A block **may** have empty DA and then `daRoot = NMT.emptyRoot()`.

---

## 4) Θ target, Γ caps, and acceptance

### 4.1 Scoring & acceptance
For a candidate header, let:
- `H(u)` be the *u-draw* term from HashShare (see `mining/nonce_domain.py`).
- `Σψ` be the sum of mapped useful-work scores from verified proofs (`consensus/scorer.py`).
- The block acceptance predicate is:

S = H(u) + Σψ
Accept iff S ≥ Θ

where **Θ** is `theta` (micro-nats) recorded in the header.

### 4.2 Retarget schedule (Θ)
- Θ is updated by `consensus/difficulty.py` using an EMA over observed inter-block times, with clamps and stability windows.
- Nodes recompute the next Θ from parent history; on import, they verify `header.theta` matches local expectation within protocol rules.

### 4.3 Γ capacity caps
- Per-type caps, per-proof caps, and **total Γ** cap are enforced by `consensus/caps.py` with inputs from the pinned `policyRoots.poies`.
- During block building, the **proof selector** (`mining/proof_selector.py`) filters/weights proofs so that:  
  `Σ (ψ_i under policy) ≤ Γ_total` and per-type caps/diversity/escort rules are satisfied.
- Verifiers must reproduce the same capping when reconstructing `Σψ` from `proofsRoot` receipts; mismatch → reject.

---

## 5) Policy root pinning

`policyRoots` is a map to anchor consensus-critical configuration:

```cbor
policyRoots = {
  "poies": bstr(32),        ; Merkle root over PoIES policy object (caps, weights, escort rules)
  "algPolicy": bstr(32)     ; Merkle root over PQ algorithm policy (ids, thresholds)
}

	•	Builders must compute these from the exact policy objects and fail block assembly if mismatch with local config.
	•	Importers verify these roots before scoring proofs.

⸻

6) Signing / mining domains

6.1 Header SignBytes (domain-separated)

Before HashShare search, miners bind to a header template (without nonce). The sign/hash domain is:

preimage = ASCII("animica/header-v1") || 0x00 || CBOR(header_without_nonce)
headerHashForMining = sha3_256(preimage)

	•	The nonce domain is distinct and appended only in the u-draw (“mix”) path (mining/nonce_domain.py).
	•	Changing any field bound in the template invalidates the u-draws.

6.2 mixSeed

mixSeed = sha3_256( prev.mixSeed || beacon.prev || vdf.output || qrng.mix || parentHash )
Exact combiner and transcript are defined in randomness/specs/BEACON.md. The seed is used in share targeting and, optionally, policy randomness.

⸻

7) Proof pack (body) format

7.1 ProofEnvelope (wire)

Raw proofs are CBOR per proofs/schemas/proof_envelope.cddl:

ProofEnvelope = {
  type_id: uint,           ; 0=HashShare, 1=AI, 2=Quantum, 3=Storage, 4=VDF
  body: bstr,              ; type-specific CBOR/JSON per schema
  nullifier: bstr(32)      ; domain-separated anti-replay (see `proofs/nullifiers.py`)
}

	•	On import, the node verifies each envelope with the registered verifier (proofs/registry.py), obtains metrics, maps them to ψ-inputs (proofs/policy_adapter.py), and then constructs the compact ProofReceipt included in proofsRoot.

7.2 Proof receipts (root material)

ProofReceipt = {
  type_id: uint,
  nullifier: bstr(32),
  metrics: map,            ; small fixed subset needed for Σψ reproduction
  psi_inputs: map,         ; normalized ψ inputs (pre-cap)
  units: uint              ; optional costed units for economics/treasury
}

Receipts are consensus objects. Full proofs are not; nodes may prune proofs after import.

⸻

8) Size & limits (DoS guards)

Policy-bound maxima (configured in spec/params.yaml and spec/poies_policy.yaml):
	•	Max header extraData length
	•	Max block tx count and total bytes (pre-DA)
	•	Max proofs per block and total proof bytes (pre-receipt)
	•	Max DA blob bytes and namespaces per block
	•	Max gas used per block (execution)
	•	Max wall-clock drift for timestamp

Blocks violating any limit are rejected at import.

⸻

9) Canonical CBOR sketch (diagnostic)

Header = {
  "parentHash": h'…32…',
  "number": 1024,
  "timestamp": 1731001123,
  "chainId": 1,
  "stateRoot": h'…32…',
  "txRoot": h'…32…',
  "receiptsRoot": h'…32…',
  "proofsRoot": h'…32…',
  "daRoot": h'…32…',
  "mixSeed": h'…32…',
  "nonce": h'0011aabbccddeeff',
  "theta": 123456,                   # micro-nats
  "policyRoots": {
    "poies": h'…32…',
    "algPolicy": h'…32…'
  },
  "version": 1
}


⸻

10) Validation checklist (import path)

An importing node MUST:
	1.	CBOR: verify deterministic encoding, key set, and version.
	2.	Parent: parentHash points to a known header; number = parent.number + 1.
	3.	Policy pin: recompute policyRoots and match.
	4.	Tx/Receipts: recompute txRoot and receiptsRoot from body.
	5.	DA: recompute daRoot from posted blobs (or ensure empty).
	6.	Proofs: verify each ProofEnvelope, construct receipts, recompute proofsRoot.
	7.	State: re-execute block (serial executor in v1) to get stateRoot; match.
	8.	Θ/retarget: recompute expected Θ; verify header.theta within protocol rules.
	9.	S predicate: recompute S = H(u) + Σψ under caps; require S ≥ Θ.
	10.	Timestamp & limits: enforce drift and size limits.

Any failure → reject the block.

⸻

11) Versioning
	•	Changes to header fields, roots set, or transcript require a version bump (version and animica/header-vN domain).
	•	Policy evolution happens off-chain but is pinned on-chain via policyRoots to ensure deterministic verification.

⸻

12) References
	•	spec/header_format.cddl, spec/blob_format.cddl
	•	consensus/math.py, consensus/difficulty.py, consensus/scorer.py, consensus/caps.py
	•	proofs/* (verifiers, receipts, nullifiers)
	•	da/* (NMT, erasure, availability)
	•	execution/* (apply, receipts), core/chain/*
	•	mining/* (templates, nonce/mix domain, selector)
	•	randomness/specs/* (beacon & VDF)
