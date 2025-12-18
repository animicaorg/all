# Animica P2P Rewrite Summary

This note summarizes the P2P-first architecture already present in the Animica monorepo and maps it onto the requirements in the rewrite brief.

## Step 0: Where things live today
- **Node entrypoints:** `p2p/cli/listen.py` boots the full P2P service (PQ handshake, transports, gossip, sync) via `p2p.node.service.P2PService`, with a minimal TCP fallback for degenerate environments.【F:p2p/cli/listen.py†L296-L337】
- **Core P2P modules:** the P2P stack is organized under `p2p/` with crypto, transports (TCP/QUIC/WS), wire encoding, peer/session management, discovery, gossip, sync, and node lifecycle layers described in `p2p/README.md`.【F:p2p/README.md†L22-L119】
- **Peer store:** peers and their addresses/metadata are persisted in a SQLite-backed `PeerStore`, which also tracks direction/ban scores and powers discovery redials.【F:p2p/peer/peerstore.py†L30-L205】
- **RPC dependency:** sync is peer-driven. The only RPC touchpoint is the optional checkpoints helper (`p2p/checkpoints`) that can fetch signed checkpoints from a configurable URL; the core sync loop and gossip paths do not require a trusted RPC endpoint.

## Architecture mapping
- **Transport + sessions:** PQ-authenticated Kyber handshakes produce AEAD channels over TCP/QUIC/WS with rate limits and DoS caps defined in the wire/peer layers. Message IDs, topics, and length limits are enumerated for deterministic framing.【F:p2p/README.md†L14-L119】
- **Peer discovery:** nodes dial configured multiaddr seeds, fall back to network-based DNS/HTTP seeds, and keep background discovery loops (Kademlia + mDNS) running while redialing stored peers from the SQLite peerstore.【F:p2p/node/service.py†L379-L487】
- **Gossip / propagation:** blocks, headers, transactions, shares, and blobs are gossiped on separate topics with INV/GETDATA-style fetches and per-topic validation/rate limits. Bloom/LRU-style suppression is embedded in the gossip/mempool sync logic.【F:p2p/README.md†L85-L119】【F:p2p/sync/mempool.py†L168-L200】
- **Sync (headers-first):** header sync builds locators, fetches ranges from peers, checks policy roots/schedules, and advances the canonical tip with bounded reorg depth before requesting bodies. Blocks are then fetched via INV/GETDATA with flow-control credits.【F:p2p/README.md†L103-L119】【F:p2p/sync/headers.py†L97-L200】
- **Mempool integration:** INV(tx) handling, fetch/rebroadcast with per-peer suppression, TTL-based dedupe, and admission hooks into the node’s mempool adapters keep the mempool consistent without RPC reliance.【F:p2p/sync/mempool.py†L168-L200】
- **Observability / CLI:** `p2p/README.md` documents metrics, health, and the CLI surface (`animica peer list/connect`, publish tools) for bringing up multi-node topologies locally with no external RPC.【F:p2p/README.md†L154-L208】

## Running locally (P2P-only)
1. Start a listening node with `python -m p2p.cli.listen --db sqlite:///nodeA.db --listen tcp://127.0.0.1:41000 --chain-id 1`.
2. Start a second node on a different port and `animica peer connect` or `python -m p2p.cli.peer connect --addr tcp://127.0.0.1:41000`.
3. Watch header/block sync complete via gossip; metrics expose peer counts, RTT, and per-topic traffic.【F:p2p/README.md†L154-L208】

## Tests
`pytest -q p2p/tests` exercises handshake, gossip, sync, mempool relay, and a two-node bring-up to keep the P2P-first path covered.【F:p2p/README.md†L202-L208】
