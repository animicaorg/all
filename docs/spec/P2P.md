# P2P — Handshake (Kyber), Gossip Topics, Scoring, DoS Protection

This document specifies the Animica P2P protocol surfaces and invariants implemented in:

- `p2p/crypto/handshake.py` — **Kyber768** KEM + **HKDF-SHA3-256** → AEAD keys; transcript hash  
- `p2p/crypto/aead.py` — **ChaCha20-Poly1305** (default) / **AES-GCM** wrappers  
- `p2p/crypto/keys.py` — node static identities (**Dilithium3** or **SPHINCS+**)  
- `p2p/crypto/peer_id.py` — `peer-id = sha3_256( alg_id || identity_pubkey )`  
- `p2p/transport/{tcp,quic,ws}.py` — transports; ALPN `animica/1` for QUIC  
- `p2p/wire/{encoding,message_ids,messages,frames}.py` — CBOR/msgspec codecs  
- `p2p/gossip/{topics,mesh,validator,engine}.py` — GossipSub-like mesh & scoring  
- `p2p/sync/{headers,blocks,tx,shares}.py` — sync protocols  
- `p2p/protocol/{hello,inventory,block_announce,tx_relay,share_relay,flow_control}.py` — handlers  
- `p2p/peer/{peer,peerstore,identify,ratelimit}.py` — peers, rate-limits, identify exchange  
- `p2p/discovery/{seeds,mdns,kademlia,nat}.py` — discovery and NAT traversal

Tests covering handshake, gossip, sync, and DoS: `p2p/tests/*`.

---

## 1) Transport & framing

**Transports**: TCP (baseline), QUIC (optional), WebSocket (browser/edge). All transports encapsulate **encrypted frames** after handshake.

**Frame envelope** (`p2p/wire/frames.py`):

struct Frame {
uint16  msg_id;      // see message_ids.py
uint64  seq;         // per-connection sequence; anti-replay within session
uint8   flags;       // SYN=1, ACK=2, COMP=4 (reserved)
bytes   payload;     // CBOR-encoded message matching msg_id
bytes   tag;         // AEAD tag (transport adds); not part of CBOR
}

**Encoding**: Deterministic **CBOR** (canonical sorting; see `wire/encoding.py`). A per-frame checksum is implicit via AEAD tag.

**Limits (defaults, policy, not consensus)**:
- Max frame size: **1 MiB** (soft) / **4 MiB** (hard disconnect).
- Idle timeout: **60s** (TCP/WS), **30s** (QUIC).
- Back-pressure via flow control windows (§6.3).

---

## 2) Handshake (Kyber768 → AEAD)

### 2.1 Roles & materials
- Each node has a **static identity key** (`Dilithium3` or `SPHINCS+`), advertised in HELLO.
- Peer-ID: `peer_id = sha3_256(alg_id || identity_pubkey)`.
- Ephemeral handshake uses **Kyber768** (KEM). AEAD keys are derived via **HKDF-SHA3-256**.

### 2.2 Transcript (HELLO)

1. **ClientHello**

{
“v”: 1,                             // protocol version
“node_ver”: “x.y.z+git”,            // string
“kex”: “kyber768”,                  // fixed in v1
“aead”: “chacha20-poly1305”,        // or “aes-gcm”
“alg_id”: “dilithium3|sphincs128s”, // identity algorithm
“id_pub”: bytes,                    // identity public key
“sig”: bytes,                       // signature over transcript-so-far
“features”: {                       // booleans
“quic”: bool, “ws”: bool, “blobs”: bool
},
“chain_id”: uint64,                 // expected chain
“alg_policy_root”: bytes32,         // pin PQ alg-policy root
“nonce”: bytes12                    // client nonce
}

2. **ServerHello**

{
“v”: 1,
“node_ver”: “…”,
“aead”: “chacha20-poly1305|aes-gcm”,
“id_pub”: bytes,
“sig”: bytes,                       // server identity signature
“nonce”: bytes12,                   // server nonce
“caps”: { “gossip”: […], “limits”: {…} }
}

3. **KEM key exchange**
- Client receives server’s Kyber pubkey (bootstrap step via transport preface) or static well-known on QUIC ALPN.
- Client **encapsulates** → sends `ct` (ciphertext).
- Both sides derive:
  ```
  ikm = SHA3-256( ct || client_nonce || server_nonce )
  (k_tx, k_rx) = HKDF-SHA3-256(ikm, "animica/p2p/v1", info=sorted(ClientHello||ServerHello))
  ```
- Switch to encrypted mode; `seq` resets to `0`.

4. **Identity binding**
- Each side signs the **transcript hash**:
  ```
  T = SHA3-512( ClientHello || ServerHello || ct )
  sig = PQ_sign(identity_sk, T)
  ```
- The signature is embedded in the respective *Hello* objects.
- Peers **MUST** verify the signature against `id_pub`.

> Identity binding resists active MITM within the handshake. No PKI is mandated; **peerstore pinning** and **allowlists** are recommended (§7).

### 2.3 Identify exchange
After encryption, peers exchange `IDENTIFY` message containing:
- `chain_id`, `head_height`, supported topics, max message sizes, observed addresses.

---

## 3) Messages & topics

### 3.1 Message IDs (subset)
| ID  | Name              | Payload (CBOR)                           |
|-----|-------------------|------------------------------------------|
| 0x01| HELLO             | ClientHello / ServerHello                |
| 0x02| IDENTIFY          | {chain_id, head_height, caps, addrs}     |
| 0x10| PING              | {nonce}                                  |
| 0x11| PONG              | {nonce}                                  |
| 0x20| INV               | {topic, items:[Hash]}                    |
| 0x21| GETDATA           | {topic, items:[Hash]}                    |
| 0x22| DATA              | {topic, entries:[Any]}                   |
| 0x30| GRAFT             | {topic}                                  |
| 0x31| PRUNE             | {topic, backoff_s}                       |
| 0x40| FLOW_CREDIT       | {topic, credits}                          |

`INV/GETDATA/DATA` implement a thin request/announce layer for sync.

### 3.2 Gossip topics (`p2p/gossip/topics.py`)
| Topic          | Payload (DATA)                        | Validator (fast path)                          |
|----------------|---------------------------------------|------------------------------------------------|
| `blocks`       | Compact block headers + proofs        | header sanity, chainId, size limits            |
| `headers`      | Header batch                          | fields/cbOR order; policy roots                |
| `txs`          | Transactions (CBOR)                   | chainId/gas/size; PQ-sig precheck              |
| `shares`       | Useful-work shares (Hash/AI/Q/VDF)    | policy precheck (caps/Γ), envelope checksum    |
| `blobs`        | DA commitments + samples (optional)   | namespace & commitment domain checks           |

Validators perform **cheap** checks prior to full decoding; invalid messages increment peer penalty.

---

## 4) Gossip mesh & scoring (GossipSub-like)

### 4.1 Mesh
- Each subscribed topic maintains a small **mesh** (`D = 6` target peers).
- On **GRAFT**, add peer to mesh (if under target); on **PRUNE`, remove (send backoff).

### 4.2 Scoring vector
Per-topic peer score `S` is a weighted sum:

S = w_time * delivery_time_score
	•	w_val  * valid_message_ratio

	•	w_inv  * invalid_msg_penalties
	•	w_dup  * duplicate_spam

	•	w_mesh * mesh_stability

	•	w_chrn * churn_rate

**Defaults (policy):**
- `w_time = 1.0`
- `w_val  = 2.0`
- `w_inv  = 5.0`
- `w_dup  = 0.5`
- `w_mesh = 0.2`
- `w_chrn = 0.2`

Peers below **`S_min = -5`** are **throttled**; below **`S_ban = -20`** are **temporarily banned** (cooldown).

### 4.3 IWANT / IHAVE (implicit via INV/GETDATA)
- `INV` advertises hashes; receivers pull via `GETDATA`.
- Bloom-filter dedupe avoids re-advertising.

---

## 5) Sync protocols

### 5.1 Headers sync (`p2p/sync/headers.py`)
- Locators + `getheaders/headers` range responses.
- Validate basic schedule (Θ), policy roots, linkage (`prevHash`).

### 5.2 Blocks sync (`p2p/sync/blocks.py`)
- Parallel fetch; deterministic reassembly; size & gas budget guards.

### 5.3 Tx mempool sync (`p2p/sync/mempool.py`)
- INV/GETDATA/tx relay with **duplicate suppression** and **rate-limits** per peer.

### 5.4 Shares sync (`p2p/sync/shares.py`)
- Hash/AI/Quantum/VDF share relay with **policy precheck** (caps, Γ).

---

## 6) Flow control & DoS limits

### 6.1 Token-bucket rate limits (`p2p/peer/ratelimit.py`)
- **Per-peer**: tx/s, bytes/s; **per-topic** buckets to prevent flooding.
- **Global** buckets as a second line of defense.

**Defaults**:
- Per-peer: `5 msgs/s`, `256 KiB/s`.
- Topic multipliers (policy): `blocks=1.0`, `headers=2.0`, `txs=0.7`, `shares=1.0`, `blobs=0.5`.

### 6.2 Message size & structure guards
- Hard cap per `DATA` entry (e.g., tx ≤ **128 KiB**, header ≤ **64 KiB**).
- Batch caps (e.g., ≤ **1024 txs** per DATA).

### 6.3 Credits-based flow control (`p2p/protocol/flow_control.py`)
- Each topic assigns **credits** per peer; sending consumes credits; `FLOW_CREDIT` refills.
- Back-pressure protects decoders and downstream DBs.

### 6.4 Duplicate suppression
- Rolling bloom filters (per topic) + LRU caches keyed by hashes.

### 6.5 Penalties & bans
- Invalid payload → increment **invalid counter**; on threshold: **PRUNE** peer and reduce credits.
- Repeated malformed frames → **disconnect** with backoff.

---

## 7) Discovery & peerstore

- **Seeds**: DNS / JSON endpoints (`p2p/discovery/seeds.py`).
- **mDNS**: optional LAN discovery.
- **Kademlia**: lightweight DHT keyed by `peer_id`.
- **NAT**: UPnP/NAT-PMP assistance; optional hole punching.
- **Peerstore**: remembers `peer_id`, observed addrs, **identity key** and **score**; supports **allowlist/denylist**.

Operators should pin known-good bootstraps; optionally enforce allowlist for production.

---

## 8) Security considerations

- **Handshake security**: Kyber768 KEM with transcript-binding PQ signatures (Dilithium3/SPHINCS+). AEAD provides integrity & confidentiality.
- **Replay**: `seq` counters per connection + nonces in transcript thwart replay within session; HELLO signatures cover both sides’ nonces.
- **No global PKI**: Trust is local; peerstore pinning or manual allowlists recommended.
- **Resource exhausiton**: Token buckets, credits, size caps, structured decoders.
- **Censorship-resistance**: Multi-seed discovery; peer scoring discourages targeted drop, but no consensus-level guarantees.

---

## 9) Wire schemas (informative)

### 9.1 INV

{
“topic”: “txs|blocks|headers|shares|blobs”,
“items”: [ bytes32, … ],   // content hashes
}

### 9.2 DATA — txs (example)

{
“topic”: “txs”,
“entries”: [ , … ],
}

### 9.3 BLOCK_ANNOUNCE (compact)

{
“height”: uint64,
“hash”: bytes32,
“header_compact”: bytes,   // canonical encoding w/o receipts/txs
}

---

## 10) Timing & parameters (policy defaults)

| Parameter                      | Default        | Notes                                  |
|--------------------------------|----------------|----------------------------------------|
| Mesh degree `D`                | 6              | target peers per topic                 |
| PRUNE backoff                  | 60 s           | do not graft within this window        |
| Ping interval                  | 20 s           | keeps NAT & connections alive          |
| Idle timeout (TCP/WS)          | 60 s           | disconnect after inactivity            |
| Idle timeout (QUIC)            | 30 s           |                                        |
| Max frame                      | 1 MiB (soft)   | 4 MiB hard disconnect                  |
| Per-peer msgs/s                | 5              | token bucket                           |
| Per-peer bytes/s               | 256 KiB        | token bucket                           |
| Flow control credits (start)   | 1 MiB/topic    | replenished via FLOW_CREDIT            |

---

## 11) Pseudocode snippets

### 11.1 Handshake (client)
```text
connect()
send ClientHello
recv ServerHello
verify_sig(ServerHello.id_pub, transcript_hash)
ct = kyber.encaps(server_kem_pub)
derive k_tx,k_rx = hkdf(ct, client_nonce, server_nonce, transcript)
switch_to_aead(k_tx,k_rx)
send IDENTIFY

11.2 Gossip validation (fast path)

on_data(topic, entry):
  if size(entry) > topic.max_size: penalize(peer, "oversize"); drop
  if !cheap_sanity(entry): penalize(peer, "sanity"); drop
  if bloom.seen(hash(entry)): return   # dup
  enqueue_full_decode(entry)


⸻

12) Versioning

This is P2P v1. Changes that remain wire-compatible (e.g., adding optional fields) are minor. Breaking changes (handshake parameters, message ids) bump the major and ALPN.

⸻

13) References (implementation)
	•	p2p/tests/test_handshake.py — Kyber handshake, AEAD round-trip, transcript hash
	•	p2p/tests/test_gossip_mesh.py — mesh fanout, graft/prune, scoring
	•	p2p/tests/test_header_sync.py — locator-based header sync
	•	p2p/tests/test_block_sync.py — parallel block fetch
	•	p2p/tests/test_tx_relay.py — admission/rate control paths
	•	p2p/tests/test_share_relay.py — policy precheck on shares
	•	p2p/tests/test_rate_limit.py — per-peer/topic buckets

