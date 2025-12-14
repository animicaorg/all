# Transaction Gossip Protocol

## Overview

The transaction gossip protocol enables peer-to-peer propagation of transactions across the Animica network. When a transaction is submitted to any node, it is validated and then gossiped to peers, allowing miners across the network to include it in blocks.

## Architecture

### Components

1. **TxRelayHandler** (`p2p/protocol/tx_relay.py`):
   - Protocol handler that manages transaction gossip
   - Subscribes to the `txs` gossip topic
   - Validates and admits incoming transactions to mempool
   - Publishes locally-submitted transactions to peers
   - Tracks metrics for relay events

2. **TxRelayGate** (`p2p/protocol/tx_relay.py`):
   - Lightweight admission filter
   - Provides fast deduplication via rolling Bloom filter
   - Performs size bounds checks
   - Optional signature pre-verification callback

3. **GossipEngine** (`p2p/gossip/engine.py`):
   - Manages pub/sub for gossip topics
   - Handles mesh formation and peer scoring
   - Provides reliable message delivery
   - Rate-limits message flow

4. **P2PDeps** (`p2p/deps.py`):
   - Bridge to core mempool/consensus
   - Provides `admit_tx()` method for mempool admission
   - Performs policy enforcement (fee floor, nonce, chain_id, etc.)

## Gossip Flow

### Inbound Transaction Flow

```
Remote Peer → GossipEngine → TxRelayHandler → TxRelayGate → Mempool
                                    ↓
                              (validation)
                              - Size check
                              - Deduplication
                              - Optional sig precheck
                                    ↓
                              P2PDeps.admit_tx()
                              - Fee floor check
                              - Nonce validation
                              - Chain ID match
                              - Signature verification
                              - Balance check
                                    ↓
                              Add to mempool
```

**Steps:**

1. Remote peer publishes a transaction to the `txs` gossip topic
2. GossipEngine receives the message and forwards to TxRelayHandler's `_handle_gossip_tx()`
3. TxRelayGate performs fast admission checks:
   - Size bounds (reject if > MAX_TX_BYTES = 512 KiB)
   - Bloom filter deduplication (reject if already seen)
   - Optional fast signature domain precheck
4. If admitted by gate, decode transaction from CBOR
5. Call `P2PDeps.admit_tx(tx)` to validate and add to mempool:
   - Check chain_id matches local network
   - Verify PQ signature (Dilithium3/SPHINCS+)
   - Validate nonce (contiguous for sender)
   - Check fee meets floor/watermark
   - Verify sender has sufficient balance
6. If admitted, transaction is available for miner inclusion
7. Metrics are updated (admitted/rejected/duplicate)

### Outbound Transaction Flow

```
RPC Submission → Pending Pool → TxRelayHandler.publish_local_tx() → GossipEngine → Peers
                     ↓
                 (validation)
                 - CBOR decode
                 - Chain ID check
                 - PQ signature verify
                 - Add to pending pool
                     ↓
                 _gossip_tx_to_peers()
                     ↓
                 TxRelayHandler.publish_local_tx()
                     ↓
                 Check dedupe (skip if already gossiped)
                     ↓
                 GossipEngine.publish(topic, tx_cbor)
                     ↓
                 Fanout to mesh peers
```

**Steps:**

1. Client submits transaction via `tx.sendRawTransaction` RPC
2. RPC layer validates transaction:
   - CBOR decode and structure validation
   - Chain ID matches network
   - PQ signature verification
   - Duplicate check (pending pool + persisted txs)
3. Add to pending pool for local mempool processing
4. Call `_gossip_tx_to_peers(raw_tx)` to broadcast
5. TxRelayHandler marks transaction as seen (dedupe)
6. Build gossip topic path: `animica/gossip/v1/{chain_id}/txs`
7. GossipEngine publishes to all peers in the mesh
8. Peers receive via their TxRelayHandler and process (see Inbound Flow)

## Deduplication Strategy

To prevent transaction re-broadcast loops, multiple layers of deduplication are employed:

1. **TxRelayGate Bloom Filter**:
   - Rolling Bloom filter with 3 generations (default)
   - Each generation is ~128 KiB (1M bits, k=7 hash functions)
   - Entries age out after 3 rotations (~3 minutes at 60s/rotation)
   - Prevents same tx from being admitted multiple times from different peers

2. **Seen-Set Per Publish**:
   - Before publishing locally-submitted tx, check if already in Bloom
   - Prevents re-gossiping a transaction we've already relayed

3. **Pending Pool Deduplication**:
   - RPC layer checks pending pool before admission
   - Idempotent: submitting same tx twice returns same hash

4. **Mempool Deduplication**:
   - Mempool itself maintains tx index by hash
   - Duplicate txs are rejected at admission time

## Policy Enforcement

Transaction relay respects mempool admission policies:

### Fee Floor & Watermark

- Transactions must meet minimum fee threshold
- Dynamic watermark adjusts based on mempool pressure
- Low-fee transactions are rejected during high load

### Nonce Ordering

- Transactions must have contiguous nonces per sender
- Gap nonces are held in "pending" queue until filled
- Only ready (executable) transactions enter priority queue

### Chain ID

- All transactions must match the node's configured chain_id
- Mismatches are rejected at RPC validation (before gossip)
- Prevents cross-chain replay attacks

### Signature Verification

- PQ signatures (Dilithium3 or SPHINCS+) are verified
- Domain-separated signing to prevent message type confusion
- Pubkey must match the `from` address

### Balance & Account State

- Sender must have sufficient balance for `value + gas_limit * gas_price`
- Account nonce must be valid for the transaction
- State is checked at admission time (not pre-gossiped)

## Metrics & Logging

TxRelayHandler tracks the following metrics (accessible via `get_metrics()`):

- `rx_inv`: INV messages received (future)
- `rx_get`: GETDATA messages received (future)
- `rx_bodies`: Transaction bodies received via gossip
- `tx_admitted`: Transactions successfully admitted to mempool
- `tx_duplicate`: Duplicate transactions suppressed
- `tx_rejected_oversize`: Transactions rejected for size > MAX_TX_BYTES
- `tx_rejected_verify_fail`: Transactions rejected by fast verify callback
- `tx_rejected_mempool`: Transactions rejected by mempool admission policy
- `tx_published`: Transactions published to gossip mesh

Logging examples:

```
[INFO] TxRelayHandler: Admitted relayed tx from peer_abc123, tx_hash=0x1234...
[DEBUG] TxRelayHandler: Duplicate tx from peer_def456, hash=0x5678...
[WARNING] TxRelayHandler: Oversized tx from peer_789abc: oversize>1048576
[ERROR] TxRelayHandler: Error admitting tx from peer_xyz: invalid_nonce
```

## Gossip Topics

### Topic Format

Topic paths follow the format:
```
{protocol}/{group}/v{version}/{chain_id}/{leaf}
```

For transactions:
```
animica/gossip/v1/{chain_id}/txs
```

Examples:
- Mainnet (chain_id=1): `animica/gossip/v1/1/txs`
- Testnet (chain_id=1337): `animica/gossip/v1/1337/txs`

## Testing

See `p2p/tests/test_tx_gossip_integration.py` for comprehensive tests covering deduplication, metrics, publishing, and size limits.

Run tests with:
```bash
pytest p2p/tests/test_tx_gossip_integration.py -v
```

## Performance Considerations

### Deduplication Overhead

- Bloom filter lookup: O(k) where k=7 hash functions
- Memory per generation: ~128 KiB (tunable)
- Rotation overhead: O(1) (just pointer increment)

### Gossip Fanout

- Default mesh degree: D=6 peers per topic
- Gossip factor: 25% of non-mesh peers get IHAVE
- Rate limiting: token bucket per (peer, topic)

### Admission Bottleneck

- CBOR decoding: ~10-50 μs per tx
- PQ signature verification: ~500 μs (Dilithium3)
- Mempool admission: ~50-200 μs (nonce check, balance query)
- Total: ~600-750 μs per tx

## Security

### DoS Protection

1. **Rate Limiting**: Per-peer token buckets prevent spam
2. **Size Limits**: MAX_TX_BYTES = 512 KiB hard cap per tx
3. **Bloom Filter**: Dedupe prevents re-processing same tx
4. **Peer Scoring**: Low-quality peers (invalid msgs) get pruned

### Sybil Resistance

- PQ handshake (Kyber768) for peer authentication
- Peer ID derived from long-term PQ signing key
- Mesh formation prefers stable, high-score peers

### Spam Mitigation

- Fee floor: Minimum fee required for admission
- Banlist: Persistent spammers can be banned
- Watermark: Dynamic fee threshold rises under load
