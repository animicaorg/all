# ENCODING — Canonical CBOR, IDs, and Domain-Separation Tags

This document defines the **wire-level encoding profile** for Animica consensus objects and signatures:
- Canonical **CBOR** rules (deterministic, malleability-resistant)
- Canonical **IDs** used across subsystems (chains, algorithms, proof types, ZK schemes)
- **Domain-separation** tags for hashing and signing

Authoritative shape schemas live in `spec/*.cddl` and JSON-Schema where called out. Implementations: `core/encoding/*`, `core/utils/*`, `proofs/utils/*`, `randomness/utils/*`, `capabilities/jobs/*`.

---

## 1) Canonical CBOR Profile

Animica uses a **strict CBOR subset** for consensus objects (Tx/Header/Block/Receipt/ProofEnvelope). The goals are **bit-identical** encoding and **no malleability**.

### 1.1 Deterministic rules (normative)

- **No indefinite-length** items. All strings, byte strings, and arrays **must** use definite-length encodings.
- **No floating point** values anywhere.
- **No CBOR semantic tags** (e.g., no bignum tags). Integers must fit as **unsigned** where used.
- **Minimal integer encoding**: shortest-length form (e.g., `0` → 0x00; `255` → 0x18 0xFF; never padded).
- **Maps**:
  - Keys are **text strings** (`tstr`) only; **ASCII** subset required.
  - Keys are **unique** and must be sorted in **strict bytewise lexicographic** order of their UTF-8 encoding.
  - **No null-valued keys** to represent absence; omit fields instead unless the schema explicitly permits `null`.
- **Byte strings** (`bstr`): carry raw binary; when exposed in JSON/RPC they appear as **`0x`-prefixed lowercase hex**.
- **Arrays**: stable order, no trailing holes.
- **Booleans**: allowed where defined, never as integers.

### 1.2 Canonical JSON (informative)

For RPC/SDK mirrors, the canonical JSON view follows:
- **Hex** fields: `0x`-prefixed lowercase.
- **Unsigned big ints** (e.g., `u256`): **decimal strings**.
- **Addresses**: **bech32m** HRP `anim` (i.e., `anim1…`).

### 1.3 Optional fields

- Optional fields are **omitted when empty/absent**.
- `null` is allowed **only** where schemas require it (e.g., `Tx.to = null` for `deploy`).

---

## 2) Canonical Object & Merkle Hashes

Unless specified otherwise:

- **Object hash** = `sha3_256( cbor(object) )`
- **Merkle leaves** (for `txsRoot`, `receiptsRoot`, `proofsRoot`) use `sha3_256( cbor(element) )`
- Internal node hash = `sha3_256( left || right )`

> Data Availability uses a **Namespaced Merkle Tree** (NMT) with different leaf/node encodings; see `da/schemas/*.cddl` and `da/nmt/*`. Its root is bound in `Header.daRoot`.

---

## 3) IDs and Registries

These IDs appear inside CBOR objects as small integers or well-scoped strings.

### 3.1 Chain IDs
- `chainId : u32`  
  Registry lives in `spec/chains.json` (CAIP-2 style), e.g.:
  - `animica:1` mainnet
  - `animica:2` testnet
  - `animica:1337` devnet

### 3.2 PQ Algorithm IDs
- `signature.alg_id : u16`  
  Registry: `pq/alg_ids.yaml`. Examples:
  - `1` = Dilithium3
  - `2` = SPHINCS+ (SHAKE-128s)

### 3.3 Proof Type IDs
- `ProofEnvelope.type_id : u16` (see `consensus/types.py`, `proofs/types.py`)
  - `1` HashShare
  - `2` AIProof
  - `3` QuantumProof
  - `4` StorageHeartbeat
  - `5` VDFProof

### 3.4 ZK Registries (informative)
- `zk/registry/registry.yaml` records **scheme/curve/hash/version** per `circuit_id`.
- `zk/registry/vk_cache.json` pins **verifying keys (VK)** by `circuit_id` with content hashes.

---

## 4) Domain-Separation Tags

All **signature** and **binding** preimages use explicit domain tags to prevent cross-context collisions.

### 4.1 Conventions
- Domain tags are **ASCII** strings of the form:  
  `animica/<component>-<purpose>-v<major>`
- The hash/sign preimage format is:  
  `H( ASCII(domain) || 0x00 || payload )`  
  where `payload` is the CBOR encoding (or specified bytes). The `0x00` sentinel cleanly separates domain from payload.
- Default hash **H** is `sha3_256`, unless noted.

### 4.2 Canonical Domain Tags

| Component         | Purpose                          | Tag                                 | Payload                                                | Hash |
|-------------------|----------------------------------|-------------------------------------|--------------------------------------------------------|------|
| **Tx**            | SignBytes                        | `animica/tx-v1`                     | CBOR of **unsigned** Tx (no `signature`)              | sha3_256 |
| **Header**        | Nonce / mix draw                 | `animica/nonce-v1`                  | CBOR(header fields bound for draw)                     | sha3_256 |
| **Proof**         | Nullifier derivation             | `animica/proof-nullifier-v1`        | CBOR(proof body + header binds)                        | sha3_256 |
| **DA**            | Blob commitment                  | `animica/da-commit-v1`              | NMT leaf material (per `da/blob/commitment.py`)        | sha3_256 |
| **P2P**           | Handshake transcript             | `animica/p2p-hs-v1`                 | transcript bytes                                       | sha3_256 |
| **Address**       | bech32m payload binding          | `animica/addr-v1`                    | `alg_id || sha3_256(pubkey)`                           | sha3_256 |
| **Randomness**    | Commit–reveal commitment         | `animica/rand-commit-v1`            | `addr || salt || payload` (see `randomness/commit_*`)  | sha3_256 |
| **Capabilities**  | Deterministic job_id             | `animica/job-id-v1`                 | `chainId || height || txHash || caller || payload`     | sha3_256 |
| **ZK**            | Verify transcript binding        | `animica/zk-verify-v1`              | CBOR(envelope/circuit/vk digest)                        | sha3_256 |
| **Alg-Policy**    | PQ alg-policy Merkle root        | `animica/alg-policy-root-v1`        | Canonical JSON → bytes of tree object                  | **sha3_512** |

> The **Alg-Policy** path uses `sha3_512` for extra headroom on tree hashing and aligns with `pq/alg_policy/build_root.py`.

### 4.3 Notes
- If a component adds a new domain, increment **`-v<major>`** when changing preimage structure.
- Avoid reusing the same payload under different domains; prefer **wrapping** (domain || 0x00 || old_payload).

---

## 5) Address Encoding (bech32m)

- Raw address payload = `alg_id (u16, big-endian) || sha3_256(pubkey)`
- HRP = `anim`
- Final user-facing string is **bech32m** (`anim1…`).
- Validation: decodes to payload with matching `alg_id` and 32-byte hash; matched against Tx `from`.

Implementation references:
- Python: `pq/py/utils/bech32.py`, `core/utils/bytes.py`
- TS/Wallet: `wallet-extension/src/utils/bech32.ts`
- SDK mirrors: `sdk/*/address.*`

---

## 6) Encoding Pitfalls & Rejections

Nodes must reject on:

1. **Map key order** wrong or duplicate keys.
2. **Indefinite-length** encodings.
3. **Floats** anywhere.
4. **Non-ASCII** map keys.
5. **Unexpected `null`** where schema doesn’t permit it.
6. **Overwide integers** (e.g., > u256 where u256 is required).
7. **Extraneous fields** not in schema (strict decoding mode in consensus paths).

---

## 7) Example: Tx SignBytes

Pseudo-code:

```text
domain = "animica/tx-v1"
unsigned_tx = {
  chainId, from, nonce, kind, to?, value, gasLimit, gasPrice, accessList?, data
}
preimage = ASCII(domain) || 0x00 || CBOR(unsigned_tx)
sign_hash = sha3_256(preimage)
signature = PQ.Sign(alg_id, sk, sign_hash)

Verifiers recompute sign_hash from the encoded Tx and check:
	•	signature verifies under pubkey
	•	from == bech32m( alg_id || sha3_256(pubkey) )
	•	chainId matches node network

⸻

8) Versioning & Upgrades
	•	Any change that alters encoded bytes or domain preimages is a consensus change and requires:
	•	New -v<major> domain tag (where relevant)
	•	Bumped CDDL version/comments
	•	Gated activation via chain params (see spec/params.yaml)

⸻

9) References
	•	Schemas: spec/tx_format.cddl, spec/header_format.cddl, spec/blob_format.cddl
	•	Core encoding: core/encoding/{cbor.py,canonical.py}
	•	Hash/Merkle: core/utils/{hash.py,merkle.py}, da/nmt/*
	•	Proofs/Nullifiers: proofs/utils/{hash.py,keccak_stream.py}, consensus/nullifiers.py
	•	Randomness: randomness/utils/hash.py
	•	Capabilities: capabilities/jobs/id.py, capabilities/host/*
	•	ZK: zk/integration/*, zk/registry/*

