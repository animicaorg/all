# HANDSHAKE — P2P Kyber Handshake & Session Encryption

**Status:** Stable (v1)  
**Scope:** P2P transports (TCP, QUIC, WS). Complements:
- `p2p/crypto/handshake.py` (reference implementation)
- `p2p/crypto/aead.py` (AEAD wrappers & nonce schedule)
- `p2p/crypto/keys.py` (node identity: Dilithium3/SPHINCS+)
- `p2p/crypto/peer_id.py` (peer-id = sha3-256(alg_id || identity_pk))
- `docs/pq/KEYS.md` (key kinds & derivation)

This document specifies a mutually authenticated, PQ-forward-secret handshake using **Kyber768** KEM for key agreement, **Dilithium3** or **SPHINCS+-SHAKE-128s** for identity signatures, **HKDF-SHA3-256** for key schedule, and **ChaCha20-Poly1305** (default) or **AES-256-GCM** for AEAD.

---

## 1) Identities & Algorithms

- **Node identity (static, signing):** `dilithium3` (preferred) or `sphincs_128s`.  
- **Handshake KEM (ephemeral):** `kyber768`. Each side generates a fresh Kyber keypair per connection.  
- **KDF:** HKDF-SHA3-256.  
- **AEAD ciphers:**
  - `CHACHA20_POLY1305` (default, cipher id = `1`)
  - `AES_256_GCM` (optional, cipher id = `2`)

**Peer ID:** `peer_id = sha3_256( alg_id_byte || identity_pubkey_bytes )`. Displayed/stored as hex.  

---

## 2) Message Framing

Handshake frames are **CBOR** maps with deterministic key ordering (see `core/encoding/canonical.py`). Transport framing (length-prefix, QUIC streams, or WS binary) is handled at the transport layer.

Common fields:

| Field        | Type        | Notes                                                  |
|--------------|-------------|--------------------------------------------------------|
| `ver`        | uint        | Protocol version, must be `1` currently               |
| `alpn`       | tstr        | `"animica/1"` (also used by QUIC ALPN)                 |
| `ciphers`    | [uint]      | Supported AEAD ids                                    |
| `nonce`      | bstr(32)    | Random 32 bytes                                       |
| `kem_pk`     | bstr        | Kyber768 public key (initiator or responder)          |
| `sig_alg`    | uint        | Identity signature alg id (matches address policy)    |
| `sig_pk`     | bstr        | Identity public key                                   |
| `sig`        | bstr        | Signature over transcript (see below)                 |
| `ct`         | bstr        | Kyber ciphertext (encapsulation)                      |
| `opts`       | map         | Optional flags (capabilities, rate limits, etc.)      |

**Transcript hash** `T = sha3_256( concat(cbor_bytes_of_each_handshake_message_in_order) )`.

---

## 3) Flow (bi-KEM; fallback to uni-KEM)

### 3.1 Bi-KEM (preferred; mutual encapsulation)

Client (A)                                         Server (B)

⸻

Generate kyber (pkA, skA)                           Generate kyber (pkB, skB)

CLIENT_HELLO:
{ ver=1, alpn=“animica/1”, ciphers=[1,2], nonce=NA,
kem_pk=pkA, sig_alg=a, sig_pk=IDA }

                                          SERVER_HELLO:
                                          { ver=1, alpn="animica/1", cipher=chosen,
                                            nonce=NB, kem_pk=pkB, sig_alg=b, sig_pk=IDB,
                                            sig = Sig_B( T=H(CH||SH_no_sig) ) }

CLIENT_FINISH:
	•	ctAB = Encaps(pkB)  → ssAB
	•	sig  = Sig_A( T=H(CH||SH||ctAB) )
{ ct=ctAB, sig }

SERVER_FINISH:
	•	ctBA = Encaps(pkA)  → ssBA
{ ct=ctBA }

Key schedule (both sides):
	•	ss = HKDF-Extract( salt=“animica:p2p:hs:v1”, IKM = ssAB || ssBA || NA || NB )
	•	exporter = HKDF-Expand(ss, “exporter”, 32)
	•	k_tx, k_rx, iv_tx, iv_rx = HKDF-Expand-Label(ss, “aead-keys”, 232 + 212)
(Directions are role-bound: client tx = server rx, and vice versa)

Start AEAD-protected transport.

### 3.2 Uni-KEM fallback (one encapsulation)
Used if peer advertises `opts.bi_kem=false`:
- Server sends `SERVER_HELLO` with `kem_pk=pkB` and signature over `T`.
- Client sends `CLIENT_FINISH` with a **single** `ctAB=Encaps(pkB)` and signature over `T || ctAB`.
- `ss = HKDF-Extract( salt, IKM = ssAB || NA || NB )` (no `ssBA`).

**Downgrade protection:** the chosen mode and cipher are committed into `T` before signing.

---

## 4) Authentication & Policy

- Each side **verifies** the peer’s `sig` using the advertised `sig_alg` and `sig_pk`.  
- The `sig_pk` must comply with node policy (`docs/pq/POLICY.md`); deprecated algorithms are rejected.  
- Nodes may enforce an **allowlist** of `peer_id`s (ops policy) or consult reputation.  
- On success, set connection identity to `(peer_id, sig_alg)` and retain `exporter` & `T` for auditing.

---

## 5) AEAD & Nonce Schedule

We use 96-bit nonces. For each direction:

- Initialize a 96-bit `base_iv` from HKDF output.  
- Maintain a 64-bit packet counter `seq` starting at 0.  
- **Nonce** = `base_iv[0..3] || seq (big-endian 64-bit)` (or XOR schedule; implementation must match both ends).  
- **AAD** includes: `ver=1`, `role`, and `seq` (CBOR tuple) to bind context.  
- **Rekey** after:
  - `seq >= 2^32` packets, or  
  - 10 minutes wall-clock, whichever comes first.  
  Rekey via `ss' = HKDF-Extract( salt="animica:p2p:rekey", IKM=exporter || seq_max )`, then re-derive AEAD keys as in §3.

---

## 6) QUIC & TCP/WS Notes

- **QUIC:** Use ALPN `"animica/1"`. The application layer still performs the PQ handshake on the first bidirectional stream to derive **application secrets**; QUIC TLS secrets protect transport headers only.  
- **TCP/WS:** Run the handshake immediately after connect / WS upgrade.  
- **Multiple streams:** All streams on a connection share the same key set; stream IDs are implicitly part of AAD at the framing layer.

---

## 7) Replay & Downgrade Protection

- The transcript includes: `ver`, `alpn`, `ciphers`, `opts`, `kem_pk`s, nonces, and any transport hints.  
- Signatures cover the **exact CBOR bytes** of messages seen so far.  
- Reject if `ver != 1`, unknown `alpn`, or if the selected cipher was not offered.  
- Implement a short **HELLO nonce cache** for initiated half-handshakes to mitigate reflection.

---

## 8) Error Handling

- Invalid CBOR / sizes → abort with `HandshakeError.Malformed`.  
- Bad signature → `HandshakeError.IdentityAuth`.  
- Kyber decapsulation failure → `HandshakeError.KEM`.  
- Cipher mismatch / unsupported → `HandshakeError.CipherMismatch`.  
- Any error: drop connection; optionally backoff peer in `p2p/peerstore`.

---

## 9) Operational Guidance

- **Key hygiene:** Ephemeral Kyber keys are single-use per connection. Do not reuse.  
- **Clock independence:** No timestamps are used; rekey is local-timer based.  
- **Logging:** Log `peer_id`, chosen cipher, and a truncated `T` (first 16 bytes) **only**. Never log keys, nonces, or ciphertext.  
- **Metrics:** Export handshake duration histogram and failure counters.

---

## 10) Interop & Test Vectors

- Fixtures: `p2p/fixtures/handshake_transcript.json` contain CBOR hex for `CLIENT_HELLO`, `SERVER_HELLO`, and derived `T`, plus AEAD key material (redacted in logs).  
- CLI: `p2p/cli/listen.py` and `p2p/cli/peer.py` can be used to establish a loopback session and print the chosen cipher & peer_id.

---

## 11) Security Considerations

- **PQ forward secrecy:** Provided by ephemeral Kyber encapsulations; compromise of identity keys does not reveal session keys.  
- **Identity binding:** Signatures bind identity keys to the KEM material and parameters via `T`.  
- **Cipher agility:** Keep the list short; prefer `CHACHA20_POLY1305` on devices without AES-NI.  
- **Denial of service:** Limit Kyber decapsulation attempts per IP / per time window before authentication; use `p2p/peer/ratelimit.py`.

---

## 12) Wire Examples (abstract CBOR)

**CLIENT_HELLO**
```cbor
{
  0: 1,                                      ; ver
  1: "animica/1",                            ; alpn
  2: [1,2],                                  ; ciphers
  3: h'…32B…',                               ; nonce (NA)
  4: h'…kyber_pkA…',                         ; kem_pk
  5: 1,                                      ; sig_alg (1=dilithium3, 2=sphincs_128s)
  6: h'…sig_pkA…'                            ; identity pubkey
}

SERVER_HELLO

{
  0: 1, 1: "animica/1", 2: 1,                ; cipher = 1 (chacha20poly1305)
  3: h'…32B…', 4: h'…kyber_pkB…',
  5: 1, 6: h'…sig_pkB…',
  7: h'…Sig_B( H(CH||SH_no_sig) )…'
}

CLIENT_FINISH

{
  8: h'…ctAB…',
  7: h'…Sig_A( H(CH||SH||ctAB) )…'
}

SERVER_FINISH (bi-KEM only)

{ 8: h'…ctBA…' }


⸻

13) IANA-ish Registries (local)
	•	Cipher ids: 1=CHACHA20_POLY1305, 2=AES_256_GCM
	•	Sig alg ids: 1=DILITHIUM3, 2=SPHINCS_SHAKE_128S
	•	KEM id: 1=KYBER768 (implicit in this spec)

Additions/changes require a minor protocol version bump and updates in p2p/constants.py.

