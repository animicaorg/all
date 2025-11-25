# ADDRESSES — bech32m Encoding, HRPs, and PQ Flavors

Animica uses human-friendly **bech32m** addresses with a single, chain-agnostic HRP and a payload that binds the **post-quantum (PQ)** algorithm to the **public key hash**.

This doc defines the byte layout, encoding/decoding rules, validation, and algorithm IDs used across the node, wallet, RPC, and SDKs.

> TL;DR: `anim1…` addresses encode `alg_id (u16, big-endian) || sha3_256(pubkey)` and are always **bech32m** (not legacy bech32).

---

## 1) Human-Readable Part (HRP)

- **HRP:** `anim`
- **Case:** lower-case only in production UIs. Mixed case is invalid per bech32 rules.
- **Network selection:** **not** baked into the address. Network is carried by `chainId` inside transactions/headers and by RPC URLs. This keeps addresses portable across dev/test/main.

> If a future network requires a distinct HRP (e.g., for UI segregation), it will be negotiated via chain params and wallet UX, not by changing the canonical encoding here.

---

## 2) Payload Format (binary)

payload = alg_id_be16 || pk_hash
where:
alg_id_be16 : 2 bytes (unsigned big-endian)
pk_hash     : 32 bytes = sha3_256(pubkey_bytes)
payload_len   : 34 bytes (exact)

- **Algorithm binding:** The `alg_id` ensures verifiers know how to check signatures and addresses.
- **Hash:** `sha3_256(pubkey_bytes)`; keys are the raw public key encodings for the given PQ scheme.

**Bech32m** encodes this 34-byte payload using the standard 5-bit regrouping with the **bech32m** constant.

---

## 3) PQ Algorithm IDs

Authoritative registry: `pq/alg_ids.yaml`.

Current reserved IDs (example snapshot):

| `alg_id` | Scheme                              | Notes                            |
|---------:|-------------------------------------|----------------------------------|
| **1**    | Dilithium3                          | liboqs preferred; browser WASM   |
| **2**    | SPHINCS+ (SHAKE-128s)               | deterministic, larger sigs       |
| **3**    | Kyber-768 (KEM)**                   | *KEM; not used for addresses*    |

> Addresses only use **signature** algorithms. KEMs (e.g., Kyber) do not derive account addresses.

---

## 4) Encoding (wallet/node)

**Inputs:** `(alg_id: u16, pubkey: bytes)`

**Steps:**
1. `pk_hash = sha3_256(pubkey)`
2. `alg = u16_big_endian(alg_id)` → 2 bytes
3. `payload = alg || pk_hash` (34 bytes)
4. `addr = bech32m_encode(hrp="anim", data=payload)`

**Output:** ASCII string like `anim1qq…`

---

## 5) Decoding & Validation (verifiers/RPC)

Given a string `s`:

1. `hrp, data, constant = bech32_decode(s)`
2. **Require:** `constant == BECH32M` (reject legacy bech32)
3. **Require:** `hrp == "anim"`
4. `payload = convert_bits(data, 5→8, strict=true)`
5. **Require:** `len(payload) == 34`
6. Parse `alg_id = u16_big_endian(payload[0:2])`
7. Parse `pk_hash = payload[2:34]`
8. When verifying a Tx signature:
   - Recover/check the **public key** for the declared PQ scheme
   - **Require:** `sha3_256(pubkey) == pk_hash`
   - **Require:** Tx `signature.alg_id == alg_id`

If any step fails → **InvalidAddress**.

---

## 6) JSON / RPC Representation

- Addresses appear as plain **strings** (bech32m).
- No embedded hex for the payload in RPC; debugging tools may expose payload bytes as `0x…`.

**Regex (informal check, not normative):**

^anim1[ac-hj-np-z02-9]{20,90}$

> Real bech32m validation must be done with checksum + regrouping, not only regex.

---

## 7) Error Cases & Rejections

Reject on:
- Wrong checksum constant (**bech32 vs bech32m**)
- HRP mismatch (not `anim`)
- Mixed case or uppercase characters
- Non-canonical regrouping or leftover padding in 5→8 bit conversion
- Payload length ≠ 34
- **Unknown `alg_id`** (unless chain policy allows “future/experimental” IDs; default is reject)
- Public key hash mismatch during signature verification
- Attempt to use **KEM** `alg_id` for addresses

---

## 8) Examples (illustrative)

> These are **illustrative** only; do not hard-code them as vectors.

- Dilithium3 (`alg_id=1 → 0x00 0x01`)

pk_hash = 0x9d3c…(32 bytes)
payload = 0x0001 || pk_hash
addr    = bech32m(“anim”, payload) → anim1qxy…y0

- SPHINCS+ SHAKE-128s (`alg_id=2 → 0x00 0x02`)

pk_hash = 0xb8a1…(32 bytes)
payload = 0x0002 || pk_hash
addr    = anim1…  (bech32m)

---

## 9) Interop & Implementations

- **Python (node/SDK):**
- `pq/py/address.py` — encode/decode helpers
- `core/utils/bytes.py` — hex/bech32 helpers
- **Wallet Extension (MV3):**
- `wallet-extension/src/background/keyring/addresses.ts`
- `wallet-extension/src/utils/bech32.ts`
- **TypeScript SDK:** `sdk/typescript/src/address.ts`
- **Rust SDK:** `sdk/rust/src/address.rs`

---

## 10) Security Notes

- **Domain separation** for signatures is handled at the **Tx SignBytes** level (`animica/tx-v1`), *not* at the address layer.
- Address malleability is prevented by:
- Fixed HRP and **bech32m** checksum
- Fixed payload length (34 bytes)
- Fixed hash function (sha3-256) and embedded `alg_id`
- Avoid leaking raw public keys where not necessary; the address commits to `sha3_256(pubkey)`.

---

## 11) Versioning & Upgrades

- Changes to payload structure or HRP are **consensus-critical** and must be coordinated via:
- `spec/ENCODING.md` updates
- `pq/alg_ids.yaml` updates (for new/retired `alg_id`s)
- Chain parameter gating (activation height/epoch)
- Adding a new PQ scheme:
1. Reserve `alg_id` in `pq/alg_ids.yaml`
2. Implement sign/verify (wallet + node)
3. Add tests & vectors
4. Roll via chain policy and release notes

---

## 12) Test Guidance

When writing vectors:
- Use fixed test keys and publish `pubkey`, `pk_hash`, `alg_id`, `payload`, and resulting `address`.
- Cross-check encode/decode round-trips across **Python**, **TS**, and **Rust** SDKs.
- Include negative tests: wrong checksum, wrong HRP, wrong length, unknown `alg_id`, and `pk_hash` mismatch.

---

### References

- `spec/ENCODING.md` — canonical encoding & domain tags
- `pq/alg_ids.yaml` — algorithm IDs registry
- Wallet extension & SDK address modules listed above
