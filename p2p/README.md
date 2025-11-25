# Animica P2P

Peer-to-peer networking for Animica. This module provides discovery, a
post-quantum (PQ) authenticated handshake, encrypted transports (TCP/QUIC/WS),
a gossip layer for blocks/txs/shares/DA, and header/block/mempool/share sync.

Gate for this milestone: **two nodes connect with the PQ handshake and fully
sync headers/blocks**, with rate-limited gossip and basic DoS protections.

---

## Design goals

- **PQ-first security**: Kyber-768 KEM for key agreement + HKDF-SHA3 → AEAD.
  Node identities are **Dilithium3** (default) or **SPHINCS+ SHAKE-128s**.
- **Simple & auditable wire**: msgspec/CBOR frames inside AEAD envelopes.
- **Deterministic behavior**: strict message ids, topic names, and limits.
- **Reasonable defaults**: safe rate limits, back-pressure, and validation.

---

## Components

- `p2p/crypto/handshake.py` – Kyber768 + HKDF transcript → tx/rx AEAD keys  
- `p2p/crypto/keys.py` – long-term node identity (Dilithium3/SPHINCS+)  
- `p2p/transport/{tcp,quic,ws}.py` – encrypted, length-prefixed streams  
- `p2p/wire/{messages,encoding,frames}.py` – frame and CBOR/msgspec codecs  
- `p2p/peer/*` – peer object, store, identify, ping, rate-limit  
- `p2p/discovery/*` – DNS/bootstrap seeds, mDNS, Kademlia (lightweight)  
- `p2p/gossip/*` – topics, mesh, scoring, validators  
- `p2p/sync/*` – headers/blocks/txs/shares synchronization  
- `p2p/protocol/*` – HELLO, INV/GETDATA, announces, flow control  
- `p2p/node/*` – service lifecycle, router, health snapshot

---

## Security model

### Identities & peer-id
- **Identity key**: PQ signature keypair (Dilithium3 default; SPHINCS+ allowed).
- **Peer-ID**: `peer_id = sha3_256(alg_id || identity_pubkey)`.
- **Node certificate** (optional, for QUIC ALPN): self-signed with identity key.

### Handshake (high level)
1. **Prologue**: each side sends a plaintext `HELLO` stub with:
   - protocol version, chainId, supported transports,
   - identity **public key** and **alg_id**,
   - advertised **alg-policy root** (from `pq/alg_policy`).
2. **KEM**: Initiator encapsulates to responder’s **KEM pubkey** (Kyber-768) and
   sends `ct`. Responder decaps to get `ss`. Both derive `ss` if mutual.
3. **Key schedule**: `AEAD_{tx,rx} = HKDF-SHA3-256(ss, transcript)` → two
   independent keys + nonces. AEAD = ChaCha20-Poly1305 (default) or AES-GCM.
4. **Authentication**: Each side signs the **transcript hash** using its
   identity key (Dilithium3/SPHINCS+). The signatures are exchanged **inside
   AEAD** and verified before the session becomes active.
5. **Binding**: The transcript includes chainId and alg-policy root to prevent
   cross-network reuse.

**Properties**: PQ confidentiality (Kyber), PQ authentication (Dilithium3/
SPHINCS+), replay protection (nonces + transcript), downgrade resistance
(version & alg-policy bound into transcript).

### DoS/abuse controls
- Token-bucket per connection and per topic.
- Early validators before full decode (length caps, topic id, minimal sanity).
- Bans and cool-downs recorded in `peerstore`.

---

## Wire format

**Frame**: `len || aead( msg_id | seq | flags | cbor(payload) )`

- `len`: 4-byte big-endian length.
- `msg_id`: `u16` (see `p2p/wire/message_ids.py`).
- `seq`: `u32` per stream; used for reordering stats.
- `flags`: bitfield (ack, priority, etc).
- `payload`: msgspec/CBOR, canonical field order.

**AEAD**: per-direction nonces = 64-bit counters; keys derived via HKDF over the
handshake transcript and Kyber shared secret.

---

## Topics & messages

### Gossip topics
- `blocks` – compact block announce + fetch on demand
- `headers` – header announcements (for light sync)
- `txs` – transaction inv/relay (with pre-admission checks)
- `shares` – HashShare / AI / Quantum shares (useful-work)
- `blobs` – DA commitments and sample responses

See `p2p/gossip/topics.py` and validators in `p2p/gossip/validator.py`.

### Request/response
- `INV` / `GETDATA` (blocks, txs, shares, blobs)
- `GETHEADERS` / `HEADERS`
- Ping/Pong and Identify

---

## Sync flows

### Headers-first
1. Build a **locator** from local chain (tips + exponential back-off).
2. Send `GETHEADERS(locator)`, receive `HEADERS`.
3. Validate headers quickly: chainId, Θ schedule, policy roots, PoIES envelope
   minimal checks (full verification deferred to import).
4. Repeat until caught up.

### Blocks
- For announced headers, request blocks via `INV/GETDATA`.
- Validate body (Txs/Proofs), import via `core/chain/block_import.py`.
- Flow control: **credits** per peer (window size), back-pressure signals.

### Mempool & shares
- Transactions relayed after stateless pre-admission; dedupe via bloom.
- Shares (HashShare/AI/Quantum) relayed on dedicated topic with stricter caps.

---

## Discovery

- **Bootstrap seeds**: DNS JSON/TXT endpoints (`p2p/discovery/seeds.py`).
- **mDNS** (optional): LAN discovery for dev networks.
- **Kademlia**: small DHT over `peer_id`; used for peer routing at scale.

---

## Configuration

See `p2p/config.py`. Example:

```toml
# ~/.animica/p2p.toml
listen = ["tcp://0.0.0.0:41000", "ws://0.0.0.0:41001"]
seeds  = ["dns://seeds.animica.org"]
max_peers = 50
gossip:
  [gossip.limits]
  tx_per_sec = 200
  block_per_min = 60
aead = "chacha20poly1305"
kem  = "kyber768"
sign_alg = "dilithium3"

Environment overrides (examples):

export ANIMICA_P2P_LISTEN="tcp://0.0.0.0:41000"
export ANIMICA_P2P_SEEDS="dns://seeds.animica.org"


⸻

Running two nodes locally
	1.	Start node A (listening)

python -m p2p.cli.listen \
  --db sqlite:///nodeA.db \
  --listen tcp://127.0.0.1:41000 \
  --chain-id 1

	2.	Start node B (dial A)

python -m p2p.cli.listen \
  --db sqlite:///nodeB.db \
  --listen tcp://127.0.0.1:41010 \
  --chain-id 1

	3.	Connect B → A

python -m p2p.cli.peer connect --addr tcp://127.0.0.1:41000

	4.	Watch sync

python -m p2p.cli.peer list
python -m p2p.cli.publish headers --count 5   # dev tool

You should see: successful Kyber handshake, identify exchange, and header
sync catching B up to A. Metrics will reflect RTT, bytes, gossip fanout.

⸻

Observability
	•	Metrics: /metrics (Prometheus) via p2p/metrics.py:
	•	p2p_peers{state}, p2p_bytes_total{dir}, p2p_msgs_total{topic},
p2p_gossip_fanout, p2p_rtt_seconds, p2p_rejects_total{reason}.
	•	Logs: structured (json/text) with peer-ids, msg ids, durations.
	•	Health: node/health.py snapshot for connected peers and queues.

⸻

Compatibility & versioning
	•	Protocol version: 1
	•	QUIC ALPN: animica/1
	•	Message ids and schemas are versioned; breaking changes bump the protocol and
are negotiated during HELLO. The alg-policy root is part of identify.

⸻

Testing

pytest -q p2p/tests

Covers: Kyber handshake & AEAD round-trip, peer-id/store, rate limits, gossip
mesh behavior, header/block sync, tx relay, share relay, and a full two-node
bring-up (test_end_to_end_two_nodes.py).

⸻

Threats & mitigations (brief)
	•	Handshake abuse → stateless caps, KEM-cost caps, and early drop.
	•	Gossip floods → per-topic token buckets + mesh scoring/banlist.
	•	Header forging → quick policy & Θ checks before acceptance.
	•	Resource exhaustion → strict frame sizes, message caps, back-pressure.

See p2p/specs/* for detailed protocol, handshake, gossip, and sync docs.

