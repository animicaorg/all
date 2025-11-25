# WALLET_OPS — Seed Phrases, BIP-39+PQ Mapping, Hardware Flows

**Status:** Stable (v1)  
**Audience:** Wallet implementers (browser extension, Flutter), SDK signers, ops/security reviewers  
**Related:** `docs/pq/KEYS.md`, `docs/pq/POLICY.md`, `spec/tx_format.cddl`, `core/encoding/canonical.py`, `pq/py/utils/bech32.py`

This document specifies how Animica wallets create/restore **mnemonics**, derive **post-quantum (PQ)** keys (Dilithium3 / SPHINCS+-SHAKE-128s), form **addresses**, and support **hardware/air-gap** signing. It aligns with the PQ policy and address format used across the node, SDKs, and wallet UIs.

---

## TL;DR (Operator Cheatsheet)

- Mnemonics: **12 or 24 words** from the standard BIP-39 English list. Optional **passphrase** (“25th word”).
- Seed derivation: **PBKDF2(HMAC-SHA3-256, 2048 rounds)** → 32-byte seed → **HKDF-SHA3-256** to master secret.
- Deterministic accounts & subkeys for each PQ algorithm via hardened path:

m / 888’ / coin’ / alg’ / account’ / change / index

- `888'` = **purpose** for Animica PQ v1 (constant)  
- `coin'`: 1 = mainnet, 2 = testnet, 1337 = localnet (reserved)  
- `alg'`: 1 = Dilithium3, 2 = SPHINCS+-SHAKE-128s
- Address: `payload = alg_id (1 byte) || sha3_256(pubkey)` → **bech32m** `anim1…`
- Hardware/air-gap: supported via **QR (UR)** and **WebUSB/WebHID** bridges; no server-side keys.
- Rotation: policy-driven migration (enable SPHINCS+ account, move funds, deprecate old alg).

---

## 1) Mnemonics (BIP-39 Compatible Input)

We use the standard BIP-39 English wordlist and checksum rules to produce a mnemonic of **128 bits (12 words)** or **256 bits (24 words)** entropy.  
Let:
- `mnemonic` = space-joined words (NFKD)  
- `passphrase` = optional user string (NFKD)

**Seed derivation (Animica PQ v1):**

seed_m = PBKDF2( HMAC-SHA3-256,
password = mnemonic,
salt     = “mnemonic” + passphrase,
iterations = 2048,
dkLen    = 32 )          # 32 bytes
msk = HKDF-SHA3-256( ikm=seed_m,
salt=“animica:wallet:seed:v1”,
info=“master-secret”,
L=64 )                # 64 bytes master secret

Rationale: PBKDF2 is BIP-39-like but with SHA3-256. HKDF provides domain-separated expansion and allows future stream derivations without re-PBKDF.

**Recovery:** Any BIP-39-capable phrase **works**, but non-Animica tools won’t reproduce Keys 1:1 because of our SHA3 variant and purpose code (see below).

---

## 2) Hierarchical Deterministic PQ Keys

We adopt a **hardened first-three levels** scheme to isolate coins and algorithms:

Path: m / 888’ / coin’ / alg’ / account’ / change / index
	•	888’     : purpose (Animica PQ v1)
	•	coin’    : 1 (mainnet), 2 (testnet), 1337 (localnet)
	•	alg’     : 1 (Dilithium3), 2 (SPHINCS+-SHAKE-128s)
	•	account’ : 0..n (user accounts)
	•	change   : 0 = external (receive), 1 = internal (change)
	•	index    : 0..n (address index)

**Child key derivation function (KDF):**

node_secret = HKDF-SHA3-256(
ikm  = msk,
salt = “animica:wallet:path:v1”,
info = cbor([888’, coin’, alg’, account’, change, index]),
L    = 64 )

We split `node_secret` deterministically into per-algorithm seeds.

---

## 3) Per-Algorithm Key Material

### 3.1 Dilithium3 (alg_id = 1)

sk_seed, aux = node_secret[0:32], node_secret[32:64]
(pk, sk) = Dilithium3.keygen_from_seed(sk_seed)

- `sk_seed` feeds the reference or accelerated backend for deterministic keygen.
- `aux` is reserved (salt for blinding or domain tags where supported).

### 3.2 SPHINCS+-SHAKE-128s (alg_id = 2)

sk_seed, aux = node_secret[0:32], node_secret[32:64]
(pk, sk) = SPHINCS+-SHAKE-128s.keygen_from_seed(sk_seed)

**Determinism note:** Both constructions must match the library’s **from-seed** APIs to guarantee repeatable keys across implementations and platforms.

---

## 4) Address Format

**Payload:**

payload = alg_id (1 byte) || sha3_256(pubkey)

- `alg_id`: 0x01 (Dilithium3), 0x02 (SPHINCS+-SHAKE-128s)
- Hash: SHA3-256 of the raw public key bytes

**Human-readable address (bech32m):**
- **HRP:** `anim` (mainnet), `animt` (testnet), `animl` (localnet)
- `addr = bech32m_encode(HRP, payload)`, e.g., `anim1qxy…`

See `pq/py/address.py` and `pq/py/utils/bech32.py` for the canonical codec.

---

## 5) Signing Domains & Transaction Encoding

- **SignBytes** come from `core/encoding/canonical.py` and `execution/…` modules.
- The wallet must **domain-separate** signatures for:
  - `tx.sign` (transactions)
  - `personal.sign` (human messages, prefixed)
  - `permit.sign` (off-chain approvals; see `docs/pq/KEYS.md`)
- Always verify **chainId** and **alg-policy** before signing.

---

## 6) Storage & Encryption

- **Browser extension:** encrypted vault (AES-GCM) in extension storage; session PIN derives an ephemeral KEK using PBKDF2(HMAC-SHA3-256, 200k+) with per-install salt. Auto-lock on idle.
- **Flutter app:** platform keystore (Keychain/Keystore) sealed; optional biometric gate.  
- **Backups:** only the **mnemonic + passphrase** are required for full recovery.

---

## 7) Hardware & Air-Gap Flows

### 7.1 QR / UR (Air-Gap)
- **Outbound:** device displays a QR with CBOR-encoded `SignRequest { domain, chainId, hash, meta }`.
- **Inbound:** desktop/mobile scans `SignResponse { alg_id, addr, sig }`.
- The request includes **address previews** and **fee summary**; response includes **alg_id** to prevent cross-alg replay.

### 7.2 WebUSB / WebHID Bridge
- Device exposes a simple APDU-like command set:
  - `GET_ADDR(path)` → returns `(alg_id, addr, pubkey)`
  - `SIGN(path, sign_bytes)` → returns `(sig)`
- Transport is framed with length-prefix; host enforces **origin allowlist** and **user approval**.

### 7.3 HSM / enclave (server-side Custody)
- Not recommended for retail users. If required, implement **per-request policy** and **strong auditing**; never export seeds.

---

## 8) Rotation & Recovery

- **Algorithm rotation (policy):** when deprecating, wallets create a **new account at alg_id=2 (SPHINCS+)**, present a one-tap **migrate funds** flow, then mark alg_id=1 accounts **read-only**.
- **Lost device:** reinstall → restore mnemonic (+ passphrase).  
- **Forgot passphrase:** funds are unrecoverable; communicate clearly to users.  
- **Test restores:** encourage periodic dry-run restores on a second device.

---

## 9) Multisig & Permissions

- **Multisig (M-of-N):** implemented at contract layer; each signer may use a different `alg_id`.  
- **Permit flows:** sign typed permits scoped to contract/address with expiry; never sign blind hex blobs.

---

## 10) Edge Cases & Interop

- **Mnemonic from other chains:** accepted; addresses differ due to SHA3 + purpose `888'`.  
- **Export formats:** support **UR:CRYPTO-SEED** and **SLIP-39** (Shamir) as **optional**; conversion must round-trip to BIP-39 words.  
- **Watch-only:** importing an address requires `alg_id` alongside the bech32m string.

---

## 11) Test Plan

1. **Derivation vectors:** Given (mnemonic, passphrase, path) → (alg_id, addr, pubkey) stable across:
   - Browser (WASM), Mobile (ARM), Desktop (x86), CI (Linux).
2. **Negative tests:** wrong chainId / malformed SignBytes → **reject**.
3. **QR air-gap:** round-trip signature matches online signer.
4. **Rotation:** migration flow preserves exact balances & event logs.
5. **Vault integrity:** tampering with storage fails MAC.

---

## 12) Threat Model (Wallet)

- **Primary risks:** seed exfiltration, phishing consent, malicious extensions, weak passphrase.  
- **Mitigations:** content-script isolation, **explicit origin display**, require human-readable SignBytes summary, configurable allowlist, idle auto-lock, clipboard scrubbing, and FIDO2/WebAuthn gate for high-value ops.

---

## 13) Reference Constants

- Purpose: `888'`
- Coin (default set):
  - `1'` mainnet
  - `2'` testnet
  - `1337'` localnet
- Alg:
  - `1'` Dilithium3
  - `2'` SPHINCS+-SHAKE-128s
- HRP: `anim` / `animt` / `animl`
- Bech32 variant: **bech32m**

---

## 14) Pseudocode (Derive → Address → Sign)

```python
def master_secret_from_mnemonic(mnemonic: str, passphrase: str) -> bytes:
    seed_m = pbkdf2_sha3_256(mnemonic, "mnemonic" + passphrase, 2048, 32)
    return hkdf_sha3_256(seed_m, salt=b"animica:wallet:seed:v1",
                         info=b"master-secret", L=64)

def node_secret(msk: bytes, path: tuple[int,int,int,int,int,int]) -> bytes:
    info = cbor_encode(list(path))  # [888', coin', alg', account', change, index]
    return hkdf_sha3_256(msk, salt=b"animica:wallet:path:v1", info=info, L=64)

def pubkey_to_address(hrp: str, alg_id: int, pubkey: bytes) -> str:
    payload = bytes([alg_id]) + sha3_256(pubkey)
    return bech32m_encode(hrp, payload)

Implementation must match wallet-extension/src/background/keyring/* and SDK signers.

⸻

15) UX Notes
	•	Always show network (chainId) and fee summary before signing.
	•	Prefer SPHINCS+ for new accounts where size/latency is acceptable; keep Dilithium3 available for smaller proofs and faster UX.
	•	Provide clear passphrase guidance: “Write it down; it is required to restore this wallet.”

⸻

Changelog
	•	v1: Initial release with purpose 888', SHA3-based PBKDF, HKDF domains, and bech32m addresses.

