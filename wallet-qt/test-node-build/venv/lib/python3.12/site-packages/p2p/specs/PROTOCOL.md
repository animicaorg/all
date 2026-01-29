# Animica P2P v1 — Wire Protocol

*Status: draft-v1 (implementation-backed).  See also:*  
- `p2p/specs/HANDSHAKE.md` (Kyber+HKDF handshake, identities)  
- `p2p/specs/GOSSIP.md` (mesh & scoring)  
- `p2p/specs/SYNC.md` (header/block/tx/share sync flows)  
- `p2p/specs/SEEDS.md` (bootstrapping)

This document pins the **message catalog**, **frame layout**, **state machines**, and **DoS limits** for the Animica peer-to-peer protocol. It matches the Python reference located under `p2p/wire/*`, `p2p/protocol/*`, and `p2p/node/*`.

---

## 1) Goals & threat model

- **Interoperable** across transports (TCP, QUIC, WS) with a single canonical frame.
- **PQ-first** confidentiality/integrity: all frames after HELLO are AEAD-encrypted with keys from the Kyber768 + HKDF handshake (see HANDSHAKE).
- **Deterministic** encodings: CBOR (canonical map ordering where applicable) via the `msgspec` codec.
- **DoS awareness**: tight size limits, token-bucket per-peer/topic, bounded fanout; partial pre-validation before decode.
- **Upgradable**: strict versioning & feature bits; unsupported features must degrade gracefully.

---

## 2) Transports

Supported transports (implementation-defined selection):
- TCP (`/ip4/…/tcp/port`) — length-prefixed stream, Nagle off, keepalive on.
- QUIC (`/ip4/…/udp/port/quic-v1`) — ALPN `animica/1`, 0-RTT disabled, 1 stream per direction for control + per-topic unidirectional streams (optional).
- WebSocket (`/ip4/…/tcp/port/ws`) — binary frames only, same framing.

**Transport neutrality:** the **frame** below is identical across transports; the only difference is the outer length-prefix provided by the transport (TCP/WS) or QUIC datagram boundaries.

---

## 3) Frame layout (encrypted)

After the handshake completes, every application message is wrapped in:

+–––––––––––+—————––+––––––––––+–––––––––––+
| u16 msg_id           | u32 seq           | u16 flags          | u64 checksum8        |
+–––––––––––+—————––+––––––––––+–––––––––––+
| u32 payload_len      | payload[bytes] (CBOR, msgspec)                                   |
+–––––––––––+—————————————————————+

- **AEAD**: Entire header+payload is protected under ChaCha20-Poly1305 (default) or AES-GCM (feature). Nonce schedule: `(stream_id || seq)`; see `p2p/crypto/aead.py`.
- **checksum8**: `first_8_bytes(sha3_256(payload))` for fast corruption/DoS drop before decrypt fallback retries.
- **flags**:
  - `0x0001`: COMPRESSED (zstd/snappy; see §8).  
  - `0x0002`: ACK_REQ (peer may emit a lightweight ACK meta).  
  - `0x0004`: RESENT (replay due to perceived loss; idempotent).
- **seq**: Per-connection monotonic counter (sender-side). Used for reordering diagnostics and optional ACKs.

> **Note:** Message body schemas are in §5. The numeric IDs are defined in code (`p2p/wire/message_ids.py`) and summarized here.

---

## 4) Version & feature negotiation

- **Protocol version**: `PROTO_MAJOR=1, PROTO_MINOR=0`. The `HELLO` message carries both.
- **Feature bits** (64-bit). Negotiated by AND across peers. Current assignments:
  - `bit 0`: QUIC transport supported
  - `bit 1`: WS transport supported
  - `bit 8`: zstd compression
  - `bit 9`: snappy compression
  - `bit 16`: DA gossip topics
  - `bit 32`: Stricter CBOR validation (canonical maps only)

If a peer advertises a feature the other lacks, the initiator must not use it on that connection.

---

## 5) Message catalog

The table lists **msg_id**, **name**, **direction**, and **CBOR payload** schema (pseudo-types). IDs match the reference implementation.

### 5.1 Session & liveness

| id | name     | dir        | payload (CBOR)                                                                                           |
|----|----------|------------|-----------------------------------------------------------------------------------------------------------|
| 1  | HELLO    | both→both  | `{ version:{major:u16,minor:u16}, nodeId:bytes32, chainId:u64, algPolicyRoot:bytes32, features:u64 }`    |
| 2  | IDENTIFY | both→both  | `{ agent:string, p2pVersion:string, head:{height:u64, hash:bytes32}, caps:{topics:[u16], rates:{…}} }`   |
| 3  | PING     | both→both  | `{ nonce:u64, ts:u64 }`                                                                                  |
| 4  | PONG     | both→both  | `{ nonce:u64, ts:u64 }`                                                                                  |
| 5  | BYE      | both→both  | `{ code:i32, reason:string }`                                                                            |

**Rules:**  
- `HELLO` is the *only* plaintext message (inside the handshake transcript). All others are AEAD-encrypted.  
- `IDENTIFY` follows successful `HELLO` exchange. Missing `IDENTIFY` within 3s → disconnect.

### 5.2 Inventory / request / transfer (generic)

| id | name     | dir        | payload                                                                                                          |
|----|----------|------------|------------------------------------------------------------------------------------------------------------------|
| 10 | INV      | both→both  | `{ kind:u16, hashes:[bytes32] }`  (*kind: 1=tx, 2=block, 3=header, 4=share, 5=blob*)                            |
| 11 | GETDATA  | both→both  | `{ kind:u16, hashes:[bytes32], want:{"body":bool,"meta":bool} }`                                                 |
| 12 | DATA     | both→both  | `{ kind:u16, items:[ any ] }`  (*item schema depends on kind; see below*)                                       |
| 13 | NOTFOUND | both→both  | `{ kind:u16, hashes:[bytes32] }`                                                                                 |

**Item schemas** (CBOR):
- **tx**: `TxView` (see `rpc/models.py`) — minimal: `{hash, from, to?, nonce, gas, size, fee, payload}`.  
- **header**: `HeaderView` — `{hash, parent, height, daRoot, proofsRoot, txRoot, thetaMicro:u64, mixSeed:bytes32}`.  
- **block**: `BlockCompact` — `header + [tx_hashes] + proof_refs` (no receipts).  
- **share**: `ProofEnvelope` (see proofs/schemas/proof_envelope.cddl).  
- **blob**: `BlobMeta + first_k_shares?` (DA module controls exposure; see DA protocol docs).

### 5.3 Sync accelerators

| id | name        | dir        | payload                                                                                       |
|----|-------------|------------|-----------------------------------------------------------------------------------------------|
| 20 | GETHEADERS  | both→both  | `{ locators:[bytes32], stop?:bytes32, max:i32 }`                                              |
| 21 | HEADERS     | both→both  | `{ headers:[HeaderView] }`                                                                    |
| 22 | GETBLOCKS   | both→both  | `{ hashes:[bytes32], max:i32 }`                                                               |
| 23 | BLOCKS      | both→both  | `{ blocks:[BlockCompact] }`                                                                   |
| 24 | NEWBLOCK    | both→both  | `{ header:HeaderView, txids:[bytes32], proofids:[bytes32] }`                                  |
| 25 | NEWHEAD     | both→both  | `{ header:HeaderView }`                                                                       |

### 5.4 Gossip topics (pubsub-ish)

| id | name          | dir        | payload                                                                                           |
|----|---------------|------------|---------------------------------------------------------------------------------------------------|
| 30 | SUBSCRIBE     | both→both  | `{ topics:[u16] }`  (*topics see §6*)                                                             |
| 31 | UNSUBSCRIBE   | both→both  | `{ topics:[u16] }`                                                                                |
| 32 | GOSSIP        | both→both  | `{ topic:u16, msg: any }`  (*msg schema by topic; e.g. tx body, share envelope, commitment*)     |

**Validation**: see `p2p/gossip/validator.py`. Peers must fast-reject oversize / malformed gossip without allocating large buffers.

### 5.5 Flow control / meta

| id | name       | dir        | payload                                                                                       |
|----|------------|------------|-----------------------------------------------------------------------------------------------|
| 40 | CREDITS    | both→both  | `{ topic:u16, credits:u32 }`  (grant receive window for topic; see §7)                        |
| 41 | PAUSE      | both→both  | `{ topics:[u16], reason:u8 }`                                                                 |
| 42 | RESUME     | both→both  | `{ topics:[u16] }`                                                                            |
| 43 | ACK        | both→both  | `{ ack_seq:u32 }`                                                                             |

---

## 6) Topics (gossip)

Topic IDs (match `p2p/gossip/topics.py`):

| topic id | name       | message schema (CBOR)                                              | size limit |
|----------|------------|---------------------------------------------------------------------|-----------:|
| 1        | txs        | `TxView`                                                            | 256 KiB    |
| 2        | shares     | `ProofEnvelope` (Hash/AI/Quantum/Storage/VDF)                       | 256 KiB    |
| 3        | blocks     | `NewBlock` (header + ids)                                          | 192 KiB    |
| 4        | headers    | `HeaderView`                                                       | 64 KiB     |
| 5        | da-blobs   | `BlobAnnouncement` (commitment + ns + sizes)                       | 64 KiB     |

Fanout, graft/prune, and scoring are specified in **GOSSIP.md**.

---

## 7) Flow control

Each direction keeps a **credit counter per topic**. Sender must not exceed granted credits; receiver periodically emits `CREDITS{topic, credits}` based on local pressure (queue depth, CPU).

- Initial credits: `txs=64`, `shares=64`, `headers=128`, `blocks=8`, `da-blobs=16`.
- Each message consumes **1 credit** (block payload counts as 4).
- `PAUSE/RESUME` are advisory (temporary backpressure during reorg/IO spikes).

Additionally, a **token bucket** per peer enforces global ingress (tx/s and bytes/s). Values are configurable; see `p2p/peer/ratelimit.py`.

---

## 8) Compression

If `flags & COMPRESSED`:
- `payload` is compressed with **zstd** (preferred) or **snappy** if negotiated.
- Compression MUST NOT expand the payload beyond its original length + 1KiB; otherwise drop with `BYE{code=431, "expansion"}`.

---

## 9) State machines

### 9.1 Connection lifecycle

CLOSED
└─ dial/accept
OPENING
└─→ (handshake: Kyber768+HKDF) ─success→ AUTHENTICATED
AUTHENTICATED
├─ send HELLO  ─┐
└─ recv HELLO  ─┘ (version/features/chainId match?)
└─ ok → IDENTIFYING
IDENTIFYING
├─ send IDENTIFY
└─ recv IDENTIFY (within 3s)
└─ ok → READY
READY
├─ periodic PING/PONG
├─ gossip & sync per subscriptions
└─ graceful BYE or transport close → CLOSED

**Hard failures** (disconnect with BYE):
- Version mismatch (major), chainId mismatch, algPolicyRoot mismatch.
- HELLO/IDENTIFY timeouts.
- AEAD failure / checksum mismatch bursts.
- Repeated frame oversize or parse errors.

### 9.2 Header sync (overview)

See **SYNC.md** for diagrams. In brief:
1. At READY, the lagging peer sends `GETHEADERS{locators, max}`.  
2. The leading peer responds `HEADERS{list}` (≤ `max`).  
3. If needed, `GETBLOCKS{hashes}` for missing bodies.  
4. `NEWHEAD` broadcasts tip changes; small lag is closed with incremental `GETHEADERS`.

### 9.3 Gossip mesh (overview)

- On READY, peers `SUBSCRIBE` to desired topics.
- Mesh engine selects **fanout** peers per topic; relays `GOSSIP`.
- Validators (`p2p/gossip/validator.py`) perform O(1) sanity (size, CBOR shape, policy prechecks for shares).
- Duplicate suppression via nullifier/tx-hash rolling filters.
- Scoring (per-topic + global); bad peers are pruned.

---

## 10) Sizes & limits (normative)

- **Frame**: `payload_len ≤ 256 KiB` (topic-specific limits may be lower).
- **HELLO** within 2s of AUTHENTICATED; **IDENTIFY** within 3s after HELLO.
- **PING** every 15s (± jitter); disconnect if no `PONG` for 45s.
- **INV** lists: ≤ 2048 ids; **GETDATA**: ≤ 512 ids.
- **Headers per HEADERS**: ≤ 1024; **Blocks per BLOCKS**: ≤ 16.
- **Share envelope**: ≤ 256 KiB, must contain a non-empty `nullifier` (bytes).

Exceeding normative limits → drop message and apply score penalty; repeated violations → BYE.

---

## 11) Error handling

- Protocol errors produce `BYE{code, reason}` and immediate disconnect:
  - `400` generic, `401` version, `402` chainId, `403` algPolicyRoot, `420` AEAD, `431` compression expansion, `440` rate limit.
- Non-fatal issues (e.g., `NOTFOUND`) are informational.
- Peers SHOULD backoff re-dials with exponential jitter.

---

## 12) Security notes

- All app frames are AEAD-protected with keys from **PQ** handshake (Kyber768 KEM + HKDF-SHA3-256).  
- Node identity signatures (Dilithium3/SPHINCS+) sign the handshake transcript; `peer_id = sha3_256(pubkey || alg_id)`.  
- Replay resistance: nonce schedule binds to `(conn_id, seq)`.  
- DoS: Fast checksum8, CBOR shape checks, topic validators, and token buckets precede any heavy work.

---

## 13) Compatibility & evolution

- **Minor** version increments may add message fields (with default semantics) but never change types. Unknown fields MUST be ignored.
- **New messages** get new IDs. Unknown messages MUST be ignored (with score-neutral outcome).
- **Major** version increments can break wire semantics; nodes MUST refuse mismatched major.

---

## 14) Reference types (CBOR hints)

- `bytes32` = CBOR byte string of length 32.  
- Integers are unsigned unless noted; maps use **canonical** key ordering for structures persisted on disk (headers, blocks). Network payloads tolerate non-canonical ordering but SHOULD be canonical.

---

## 15) Test matrix

The following tests exercise protocol rules end-to-end:

- `p2p/tests/test_handshake.py` — transcript, AEAD round-trip.  
- `p2p/tests/test_header_sync.py` — locators, pagination.  
- `p2p/tests/test_block_sync.py` — parallel bodies.  
- `p2p/tests/test_tx_relay.py` — dedupe, admission, rate control.  
- `p2p/tests/test_share_relay.py` — share envelope fast-path & caps precheck.  
- `p2p/tests/test_end_to_end_two_nodes.py` — full bring-up & sync.

Conformance runners SHOULD verify limits (oversize, invalid shapes) trigger correct disconnects or soft rejections.

---

## 16) Appendix A — Example flows

### A.1 New peer join (TCP)

1. TCP connect → handshake (Kyber+HKDF) → AEAD keys established.  
2. Exchange `HELLO` (verify version/chain/policy/features).  
3. Exchange `IDENTIFY` (agent/version/head).  
4. SUBSCRIBE topics `{txs, shares, headers}`.  
5. Start PING/PONG timer, announce `NEWHEAD` as chain advances.

### A.2 Inventory round

- A sends `INV{kind=tx, hashes=[h1,h2]}`  
- B replies `GETDATA{kind=tx, hashes=[h2]}` (already has `h1`)  
- A sends `DATA{kind=tx, items=[Tx(h2)]}` or `NOTFOUND{kind=tx, hashes=[h2]}`

### A.3 Share relay

- Miner M finds a HashShare → wraps proof as `ProofEnvelope` → publishes `GOSSIP{topic=shares, msg=envelope}`  
- Peers run `preparse_proof` (nullifier, type) and sparse validation → admit or drop → forward to fanout.

---

*End of document.*
