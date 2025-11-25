# KEYS — Key Kinds, Address Derivation, Multisig & Permit Domain

**Status:** Stable (v1)  
**Scope:** Wallets, SDKs, RPC admission, contract UIs. Complements:
- `docs/spec/ADDRESSES.md` (canonical address format)
- `docs/pq/POLICY.md` (allowed algorithms, rotations)
- `spec/domains.yaml` (domain-separation tags)
- `pq/py/address.py` (reference codec) and `pq/py/registry.py` (sizes/ids)

---

## 1) Key kinds

We distinguish **signature** keys (accounts) and **KEM** keys (P2P). Do not reuse across contexts.

### 1.1 Signature keys (accounts)
- **Algorithms (per policy):** `dilithium3` (preferred), `sphincs_128s` (backup).  
- **Use:** Sign transactions, permits, off-chain authorizations.  
- **Encoding:** Raw public keys per alg; signatures verified over deterministic **SignBytes** (CBOR; see `core/encoding/canonical.py`).

### 1.2 KEM keys (P2P)
- **Algorithm:** `kyber768`.  
- **Use:** Node-to-node handshake (Kyber768 + HKDF-SHA3-256 → AEAD keys).  
- **Not used** for account addressing or transaction signatures.

> Key/alg ids are canonicalized in `pq/alg_ids.yaml` and surfaced by `pq/py/registry.py`.

---

## 2) Deterministic key derivation (wallets)

Wallets derive per-alg subkeys from a mnemonic without BIP-32 curves:

1. **Mnemonic → seed** using PBKDF2-HMAC-SHA3-256 (2048 rounds) with salt `"animica:mnemonic:v1"`.
2. **HKDF-SHA3-256** to derive per-algorithm subkeys:
   - `ikm = seed`
   - `salt = "animica:hkdf:v1"`
   - `info = "alg:<ALG_ID>|purpose:<SIGN|KEM>|path:<ACCOUNT/INDEX>"`

Default paths:
- Accounts: `path = "m/0/<account_index>"`, `purpose=SIGN`, `alg ∈ policy.sign.allowed`
- P2P identity (optional in node): `path = "p2p/0/0"`, `purpose=KEM`, `alg=kyber768`

> **Never** reuse the same derived material across different algs or purposes.

---

## 3) Address derivation (EOA)

**Human-readable part (HRP)** is network-dependent (examples):  
- Mainnet: `anim` → `anim1…`  
- Testnet: `anit` → `anit1…`  
- Local/dev: `anil` → `anil1…`  
(Exact HRPs come from `spec/domains.yaml` / chain params.)

**Payload (33 bytes):**

payload = alg_id_byte || sha3_256(pubkey_bytes)

- `alg_id_byte` — 1-byte canonical id from registry (`dilithium3=0x01`, `sphincs_128s=0x02`, reserved values are defined in code).
- `sha3_256(pubkey_bytes)` — 32 bytes.

**Encoding:** bech32m(HRP, payload).  
**Checksum & case:** per BIP-350; lowercase recommended.

**Validation checklist:**
1. Decode bech32m; HRP must match chain.  
2. Payload length **exactly 33**.  
3. `alg_id_byte` ∈ current policy `sign.allowed`.  
4. Optional: wallet may re-hash the displayed pubkey and compare to the last 32 bytes.

**Example (pseudo):**

pubkey(dilithium3) = 0x04a1… (1472 bytes)
digest = sha3_256(pubkey) = 0x6c9e…
payload = 0x01 || 0x6c9e…
address = bech32m(“anit”, payload) = “anit1qz4…”

> This format allows supporting multiple signature algs while keeping **address length fixed**.

---

## 4) Multisig

We support two patterns. Prefer **contract multisig** for flexibility; a **keyset-EOA** exists for simple M-of-N without runtime code.

### 4.1 Contract multisig (recommended)
- A contract (Python-VM) enforces threshold/policy and exposes `execute()` methods.
- The **account address** is the contract’s address (see `docs/spec/TX_FORMAT.md` & VM docs).
- Signers use normal EOA addresses. The contract checks signatures against its **keyset state**.

**Pros:** upgrades, time-locks, role-based policies.  
**Cons:** requires deploy & gas.

### 4.2 Keyset-EOA (native hash address)
- A pure data structure (no code) defines a static threshold set. The chain validates the aggregate signatures at admission.
- **Keyset object (canonical CBOR):**
  ```cbor
  {
    t: uint,                     ; threshold
    n: uint,                     ; participants length
    signers: [                   ; lexicographically sorted by (alg_id, pubkey)
      { alg: "dilithium3", pub: bytes },
      …
    ]
  }

	•	Address (bech32m)

payload = 0x80 || sha3_256(canonical_cbor(keyset))
address = bech32m(HRP, payload)

	•	Tag 0x80 distinguishes keyset-EOA from single-key EOA (0x01/0x02 alg tags).

	•	Admission rule: a transaction from a keyset-EOA must provide ≥ t valid signatures over the same SignBytes.
	•	Limitations: static membership; larger tx size (multiple PQ signatures).

Both flavors are compatible with policy rotations; however, sunsetted algs cannot be used for new signatures even if present in an old keyset.

⸻

5) Permit domain (off-chain approvals)

A Permit allows an owner to authorize a spender (or an action) off-chain. The spender submits the signed permit on-chain or via RPC.

5.1 Canonical SignBytes (CBOR)

{
  domain: "ANIMICA/PERMIT/V1",       ; fixed ASCII tag
  chainId: uint,                     ; from chain params
  owner: bytes,                      ; 33B address payload (not bech32 string)
  spender: bytes,                    ; 33B address payload
  token: bytes,                      ; 33B address payload (ERC20-like) or zero for native
  value: uint,                       ; max amount / allowance
  nonce: uint,                       ; per-owner-spender nonce
  deadline: uint,                    ; unix seconds
  context: { … } optional            ; free-form (e.g., memo, orderId)
}

	•	Domain tag also appears in spec/domains.yaml.
	•	Addresses: use raw payload bytes, not strings, to avoid HRP confusion.
	•	Canonicalization: deterministic CBOR map ordering (see core/encoding/canonical.py).

5.2 Verification flow
	1.	Spender constructs the SignBytes per above and asks the owner to sign.
	2.	Node/contract verifies:
	•	deadline ≥ now, nonce == expected(owner, spender), chainId matches.
	•	Signature verifies against owner’s alg (from address payload byte).
	•	Optional policy checks (deprecations, allowed algs).
	3.	On success, the system:
	•	Consumes/increments nonce,
	•	Credits/sets allowance for (owner, spender, token) up to value.

Replay protection: the (owner, spender) nonce is monotonically increasing.
Partial fills: contracts may implement decrease-on-use semantics.
Revocation: owner can submit a on-chain transaction to bump nonce or set allowance to 0.

5.3 Other typed messages

For generic signing (e.g., limit orders), use distinct domains:
	•	"ANIMICA/ORDER/V1"
	•	"ANIMICA/DELEGATE/V1"
…and define minimal fields with chainId, nonces, and expiries. Never reuse the PERMIT domain for unrelated payloads.

⸻

6) Security notes
	•	Algorithm policy: Nodes/wallets MUST enforce docs/pq/POLICY.md. Deprecating → Sunset transitions affect acceptance of signatures.
	•	Key storage: Wallets must encrypt private keys at rest (AES-GCM), unlock per session, and implement anti-exfil controls.
	•	No cross-context reuse: Do not reuse KEM keys for signatures, or vice versa.
	•	Address display safety: prefer showing HRP+short-hash (e.g., anim1…6c9e) but validate against payload bytes internally.
	•	Multisig upgrades: prefer contract multisig if you need rotations; keyset-EOA is immutable by design.

⸻

7) Reference sizes (as implemented)

Alg	Pubkey (bytes)	Sig (bytes)	Address tag
Dilithium3	~1472	~2701	0x01
SPHINCS+ 128s	~32	~7856	0x02
Kyber768 (KEM)	~1184 (pk)	n/a	n/a

(Exact sizes depend on backend; the registry enforces limits and serialization.)

⸻

8) Worked examples

8.1 Verify an address → alg
	1.	Decode bech32m → (hrp, payload)
	2.	If payload[0] & 0x80 == 0x80 → keyset-EOA; else single-key EOA.
	3.	For single-key: alg_id = payload[0], check policy → ok.
	4.	Compare hrp with chain’s configured HRP.

8.2 Build a Permit for native coin

{
  "domain": "ANIMICA/PERMIT/V1",
  "chainId": 1,
  "owner": "<33B payload hex>",
  "spender": "<33B payload hex>",
  "token": "00…00",       // 33 zero bytes for native
  "value": 1000000000,
  "nonce": 5,
  "deadline": 1924992000  // 2031-01-01
}

Owner signs this CBOR; spender submits to a contract or RPC method that verifies & applies allowance.

⸻

9) Test vectors & tooling
	•	pq/test_vectors/addresses.json — pubkey → payload → bech32 round-trips.
	•	pq/cli/pq_keygen.py — omni pq keygen --alg dilithium3
	•	pq/cli/pq_sign.py / pq/cli/pq_verify.py — sign/verify domain-separated messages.

⸻

10) Backwards/forwards compatibility
	•	Future address flavors may use additional payload tags (e.g., 0x81 for script-templates).
	•	Wallets should preserve unknown tags when relaying, but only sign for supported types.

