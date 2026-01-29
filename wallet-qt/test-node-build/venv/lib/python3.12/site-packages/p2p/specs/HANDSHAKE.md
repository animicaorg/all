# Animica P2P Handshake (v1)

**Suite:** `animica-v1`
- **KEM:** Kyber768 (CPA-secure KEM, IND-CCA2 via FO)
- **KDF:** HKDF-SHA3-256
- **AEAD:** ChaCha20-Poly1305 (default) or AES-256-GCM (negotiated)
- **SIG (identity):** Dilithium3 *or* SPHINCS+ SHAKE-128s
- **Encoding (handshake msgs):** CBOR, deterministic map ordering

This document pinches the exact transcript, key schedule, identity binding, and AEAD nonce/keys used by the Animica v1 peer handshake. It matches `p2p/crypto/handshake.py` and `p2p/crypto/keys.py`.

> Relation to wire protocol: the handshake runs **before** regular P2P frames. The **only** protocol message sent in cleartext is `HELLO` (defined in *PROTOCOL.md*), and it is considered part of the **handshake transcript**. After the handshake completes, **all frames** are AEAD-protected.

---

## 0) Roles & notation

Two endpoints:
- **I** = *Initiator* (dials)
- **R** = *Responder* (accepts)

Notation:
- `EK_I, ekI` = Initiator’s **ephemeral Kyber** keypair (pub, sec)
- `EK_R, ekR` = Responder’s **ephemeral Kyber** keypair
- `ct1, ss1` = KEM encaps/ciphertext & shared secret in I→R direction
- `ct2, ss2` = KEM encaps/ciphertext & shared secret in R→I direction
- `||` = byte concatenation
- `H(x)` = `sha3-256(x)`
- `HKDF-Extract/Expand` = HKDF with SHA3-256
- `LE64(x)` = x encoded as little-endian u64
- `U16/32/64` = unsigned integers of that size
- `cb(…)` = canonical CBOR encoding of the structure

---

## 1) Capability advert & KEM key exchange (plaintext)

Each side generates an **ephemeral** Kyber768 keypair.

### 1.1 ClientHello (I→R)
CBOR map (canonically ordered keys):

ClientHello = {
“suite”:      “animica-v1”,
“ekemPub”:    bytes(1184),     # Kyber768 public key
“features”:   U64,             # feature bits (see PROTOCOL.md §4)
“aead”:       [“chacha20-poly1305”,“aes-256-gcm”], # preference order
“rand”:       bytes(32)         # fresh nonce
}

### 1.2 ServerHello (R→I)

ServerHello = {
“suite”:      “animica-v1”,
“ekemPub”:    bytes(1184),
“features”:   U64,             # server-supported bits
“aead”:       “chacha20-poly1305” | “aes-256-gcm”, # server choice
“rand”:       bytes(32)
}

**Selection rule:** `features_eff = ClientHello.features & ServerHello.features`. AEAD picks the first client-supported algorithm that the server also supports (server echoes its choice).

---

## 2) Two-way KEM (hybrid shared secret)

1) **Initiator encapsulates** to `EK_R`:

ct1, ss1 = Kyber768.encaps(EK_R)
(I → R) send ct1   # ciphertext ~1088 bytes

2) **Responder decapsulates**:

ss1 = Kyber768.decaps(ct1, ekR.sec)

3) **Responder encapsulates** to `EK_I`:

ct2, ss2 = Kyber768.encaps(EK_I)
(R → I) send ct2

4) **Initiator decapsulates**:

ss2 = Kyber768.decaps(ct2, ekI.sec)

Both sides now possess `(ss1, ss2)`.

**IKM (Input Keying Material):**

IKM = cb({
“suite”:“animica-v1”,
“dir”: {“roleI”:“initiator”,“roleR”:“responder”},
“ct1”: ct1,
“ct2”: ct2,
“ss”:  ss1 || ss2           # order: I→R then R→I
})

**Salt:** `salt = "animica/handshake v1"` (ASCII bytes)

**Chaining key:**  
`ck  = HKDF-Extract(salt, IKM)`

We DO NOT start encrypting yet. We first bind identities (next section).

---

## 3) Plaintext `HELLO` + Identity binding (sign-the-transcript)

Each node has a *static* **identity signing key** (Dilithium3 or SPHINCS+). The corresponding **peer-id** is described in §6.

### 3.1 HELLO (plaintext; same schema as PROTOCOL §5.1)

Hello = {
“version”: { “major”:U16, “minor”:U16 },
“nodeId”:  bytes(32),       # sha3_256(pubkey || alg_id), see §6
“chainId”: U64,
“algPolicyRoot”: bytes(32),
“features”: U64
}

Order:
- I → R : `Hello_I` (plaintext)
- R → I : `Hello_R` (plaintext)

### 3.2 Transcript hash

We bind all plaintext handshake inputs:

T0 = cb({
“suite”: “animica-v1”,
“clientHello”: ClientHello,
“serverHello”: ServerHello,
“ct1”: ct1,
“ct2”: ct2,
“helloI”: Hello_I,
“helloR”: Hello_R
})
TH = H(T0)    # sha3-256 transcript hash

### 3.3 Identity signatures (both ways)

Each side sends its static identity **public key**, **algorithm ID**, and **signature over TH**:

IdentityProof = {
“sigAlg”:   U16,         # from pq/alg_ids.yaml (e.g., 0x0103=Dilithium3, 0x0201=SPHINCS+ SHAKE-128s)
“pubkey”:   bytes(…),  # signature public key
“signature”: bytes(…)  # sign(TH)
}

Exchange (plaintext, still part of handshake):
- I → R : `IdentityProof_I`
- R → I : `IdentityProof_R`

**Verification rules:**
- Check the `sigAlg` is permitted by local PQ policy (see `spec/pq_policy.yaml` / `pq/POLICY.md`).
- Verify signature against `TH`.
- Recompute `peer_id` locally (see §6) and must match the `Hello.nodeId` announced.

If any check fails → abort with transport close.

---

## 4) Final key schedule & traffic keys

After identities verify, derive final keys from `ck` and the transcript:

infoK = cb({
“label”:“animica/keys v1”,
“aead”:  ServerHello.aead,
“th”:    TH
})

Expand traffic secrets

kI = HKDF-Expand(ck, cb({“dir”:“I→R”}) || infoK, 32)   # Initiator sending key
kR = HKDF-Expand(ck, cb({“dir”:“R→I”}) || infoK, 32)   # Responder sending key

Per-direction base nonces (96-bit)

nI = HKDF-Expand(ck, cb({“nonce”:“I→R”}) || infoK, 12)
nR = HKDF-Expand(ck, cb({“nonce”:“R→I”}) || infoK, 12)

**AEAD choice:**  
- If `ServerHello.aead == "chacha20-poly1305"` → use ChaCha20-Poly1305 with 256-bit key.  
- Else `"aes-256-gcm"`.

**Associated Data (AAD):** the outer **frame header** without payload (see *PROTOCOL.md §3*), encoded as raw bytes before encryption.

---

## 5) Nonce schedule (per connection)

For each encrypted application frame (see *PROTOCOL.md §3*), construct the **nonce** as:

96-bit nonce for AEAD

stream_id : U32 ; seq : U64 (monotonic per-connection)

nonce_dir = nI or nR  # base nonce by direction
ctr      = U32(stream_id) || U64(seq)          # 12 bytes
nonce    = XOR_96(nonce_dir, ctr)              # fixed-base xor counter

- **stream_id** is chosen by the sender (control=0, per-topic streams as needed).
- **seq** increments for each frame sent by that endpoint, starting at 1.
- The `(stream_id, seq)` pair MUST NOT repeat for the lifetime of the connection.

> Rationale: XORing a secret base with a visible counter prevents nonce reuse across different connections and directions while keeping nonces unpredictable.

---

## 6) Peer identity & peer-id

- **Identity key:** static *signature* key (Dilithium3 or SPHINCS+). Nodes SHOULD support at least Dilithium3.
- **peer-id:**  

peer_id = sha3_256( pubkey || U16(sigAlg) )

This 32-byte value is carried as `Hello.nodeId`.

Peers MUST verify:
1. `peer_id` computed from `IdentityProof` equals `Hello.nodeId`.
2. `sigAlg` is enabled by local PQ policy (and not deprecated).
3. `peer_id` used across connections remains stable for the same node.

---

## 7) Rekeying & key updates

Long-lived connections MAY refresh keys:

- Trigger: every **2^20** frames in a direction, or every **30 minutes**, whichever is sooner.
- Mechanism: derive a new `ck' = H( ck || cb({"update":LE64(counter)}) )` and re-run §4 with the same `TH` and `infoK` but incrementing a `generation` field inside `infoK`.
- Rekey point is announced implicitly: the first frame after rekey uses `seq` reset to 1 and fresh `nonce_dir` values.

Both sides MUST synchronize rekey generations; if mismatch is observed (AEAD failures on a burst), try the previous and next generation once, else disconnect.

---

## 8) Errors & aborts

Abort conditions (MUST close transport immediately):
- Suite mismatch (`suite != "animica-v1"`).
- KEM decapsulation failure.
- Identity verification failure (bad signature, unknown/disabled `sigAlg`, `peer_id` mismatch).
- AEAD choice negotiation failure.
- Protocol version or chainId mismatch (from `HELLO`).

---

## 9) Minimal interop test (reference)

1. Generate identity keys (Dilithium3) for I and R.  
2. Run handshake with AEAD=`chacha20-poly1305`.  
3. Validate both sides compute identical `(kI, kR, nI, nR)`.  
4. Encrypt a dummy frame from I→R with `(stream_id=0, seq=1)` and verify decrypt at R; repeat R→I.  
5. Verify `peer_id` computed equals `Hello.nodeId` and signatures over `TH` pass.

See `p2p/tests/test_handshake.py`.

---

## 10) Security notes

- **PQ confidentiality:** Kyber768 two-way KEM provides PQ security of the traffic keys.  
- **Identity binding:** Dilithium3/SPHINCS+ signatures on `TH` prevent MITM across the plaintext portion (incl. `HELLO`).  
- **DoS hardening:** all handshake messages have strict size limits; KEM ciphertexts are length-checked prior to decapsulation.  
- **Forward secrecy (KEM-style):** ephemeral Kyber keys are destroyed after handshake; compromise of identity keys does not reveal traffic keys.

---

## 11) Constants (normative)

- `salt = "animica/handshake v1"`  
- `infoK.label = "animica/keys v1"`  
- AEAD nonce size = 12 bytes  
- Key lengths = 32 bytes (both AEADs)

---

## 12) Wire limits (handshake)

- `ClientHello` ≤ 2 KiB  
- `ServerHello` ≤ 2 KiB  
- `ct1`, `ct2` length MUST match Kyber768 ciphertext length (approx. 1088 bytes).  
- Identity public keys/sigs MUST match their algorithm encodings & sizes.

*End of doc.*
