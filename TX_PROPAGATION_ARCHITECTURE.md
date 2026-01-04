# Transaction Propagation Architecture

## Overview

The Animica transaction propagation system ensures that transactions submitted to any node in the network are reliably broadcast to all peers and included in mined blocks. The system uses a multi-layered approach with caching, rate limiting, and retry mechanisms.

## Architecture Layers

### 1. Message Protocol (Wire Level)

**Message Types** (defined in `/p2p/wire/message_ids.py`):
- `TX_INV` (0x0403): Announce transaction IDs to peers
- `TX_GET` (0x0404): Request full transaction bodies by ID
- `TX_DATA` (0x0405): Deliver transaction bodies
- `TX_NOTFOUND_V2` (0x0406): Indicate requested transactions are unavailable
- `TX_MEMPOOL_REQ` (0x0407): Request mempool snapshot
- `TX_MEMPOOL_RESP` (0x0408): Respond with mempool transaction IDs

**Message Flow**:
```
Node A                                  Node B
  |                                       |
  |--- TX_INV(txids) ------------------>|  (announce new txs)
  |                                       |
  |<-- TX_GET(txids) --------------------|  (request full bodies)
  |                                       |
  |--- TX_DATA(tx_bytes) --------------->|  (deliver bodies)
  |                                       |
```

### 2. TxRelayService (Core Logic)

Location: `/p2p/txrelay.py`

**Responsibilities**:
- Manage per-peer known transaction caches
- Queue INV announcements for batching
- Track inflight GET requests with timeouts
- Enforce rate limits on messages and bandwidth
- Maintain reject cache for failed transactions
- Coordinate periodic mempool sync between peers

**Key Components**:
```python
class TxRelayService:
    # Peer tracking
    _peer_state: Dict[str, PeerTxState]
    
    # Inflight requests (txid -> peer + deadline)
    _inflight: Dict[bytes, InflightEntry]
    
    # Reject cache (txid -> expire_time)
    _reject_cache: OrderedDict[bytes, float]
    
    # Per-peer known txids (LRU)
    known_txids: TxIdSetLRU  # per PeerTxState
    
    # INV queue for batching
    inv_queue: Deque[bytes]  # per PeerTxState
```

**Background Loops**:
1. `inv_flush_loop()`: Periodically flush queued INV announcements (default 200ms)
2. `inflight_timeout_loop()`: Retry or abandon timed-out GET requests (default 10s timeout)
3. `mempool_sync_loop()`: Request full mempool snapshots from peers (default 15s interval)

### 3. P2P Service Integration

Location: `/p2p/node/p2p_service.py`

**Message Routing** (lines 5104-5157):
```python
async def _handle_tx_inv(peer, payload):
    txids = parse_txids(payload.get("txids"))
    await self._txrelay.on_tx_inv(peer_key, txids)

async def _handle_tx_get(peer, payload):
    txids = parse_txids(payload.get("txids"))
    await self._txrelay.on_tx_get(peer_key, txids)

async def _handle_tx_data(peer, payload):
    items = parse_tx_data_items(payload.get("items"))
    await self._txrelay.on_tx_data(peer_key, items)
```

**Peer Lifecycle**:
- `register_peer()` called on handshake complete (line 4785)
- `unregister_peer()` called on disconnect (line 4070)
- `request_mempool_sync()` called on new peer connect (line 4792)

**Send Callbacks** (lines 9562-9607):
```python
async def _txrelay_send_inv(peer_key, txids):
    peer = self._txrelay_find_peer(peer_key)
    await self._send(peer, MsgID.TX_INV, TxInv(txids=txids).to_payload())
```

### 4. RPC Integration

Location: `/rpc/methods/tx.py`

**Transaction Submission Flow**:
```python
def miner_sendTransaction(params):
    # 1. Validate transaction
    tx_obj = _decode_and_validate(raw_tx)
    
    # 2. Admit to mempool
    svc = _get_mempool_service()
    svc.submit(tx_obj, raw_tx)
    
    # 3. Trigger P2P broadcast
    _gossip_tx_to_peers(raw_tx)
    
    return tx_hash
```

**Gossip Function** (line 1033):
```python
def _gossip_tx_to_peers(raw_tx):
    p2p_service = ctx.p2p_service
    if p2p_service:
        asyncio.create_task(p2p_service.relay_tx(raw_tx))
```

**P2P Relay Method** (line 3654 in p2p_service.py):
```python
async def relay_tx(raw_cbor):
    # 1. Admit locally
    admitted, reason = await self._admit_tx_result(raw_cbor, local=True)
    
    # 2. Announce to peers
    if admitted:
        await self._txrelay.on_mempool_add(tx_hash, raw_cbor)
```

### 5. Mempool Service

The mempool service stores admitted transactions and provides query/selection APIs for mining.

**Admission** (via `deps.admit_tx()` in `/p2p/deps.py`):
```python
def admit_tx(tx, local=False, origin_peer=None):
    # 1. Decode and normalize
    tx_obj, raw_cbor = _decode_tx(tx)
    
    # 2. Validate (chain_id, signature, nonce, balance)
    _validate_tx(tx_obj, chain_id)
    
    # 3. Submit to mempool service
    svc = _get_mempool_service()
    svc.submit(tx_obj, raw_cbor)
    
    # 4. Store in pending pool (fallback/cache)
    _pending_put(tx_hash, raw_cbor)
    
    return (True, None)
```

**Retrieval** (for mining and peer queries):
```python
def get_tx_raw(tx_hash):
    # 1. Try mempool service
    svc = _get_mempool_service()
    if svc:
        return svc.get_raw(tx_hash)
    
    # 2. Fallback to pending pool
    return _pending_get(tx_hash)
```

### 6. Mining Integration

Location: `/mining/templates.py` and `/rpc/methods/miner.py`

**Template Building**:
```python
class TemplateBuilder:
    def __init__(self, ..., txs_root_supplier=None):
        self._txs_root = txs_root_supplier or (lambda: ZERO32)
    
    def current_template(self):
        return HeaderTemplate(
            txs_root=self._txs_root(),
            ...
        )
```

**RPC Mining Methods** (line 2279 in miner.py):
```python
def miner_getWork(params):
    include_mempool = params.get("include_mempool", True)
    
    if include_mempool:
        # 1. Get pending transactions
        pending_txs = svc.snapshot(limit=5000)
        
        # 2. Validate and filter
        valid_txs = [tx for tx in pending_txs if _validate_for_block(tx)]
        
        # 3. Compute txs_root
        txs_root = compute_txs_root_from_txs(valid_txs)
        
        # 4. Build template with txs
        template = HeaderTemplate(..., txsRoot=txs_root)
    
    return template
```

## Configuration

### Environment Variables

All flags default to `true` (enabled):

```bash
# Enable TX relay (INV/GET/DATA messages)
export ANIMICA_P2P_TX_RELAY=true

# Enable TX gossip (broadcast to peers)
export ANIMICA_P2P_TX_GOSSIP=true

# Enable mempool gossip (mempool sync)
export ANIMICA_P2P_MEMPOOL_GOSSIP=true

# Master enable for P2P TX features
export ANIMICA_P2P_TX_ENABLED=true
```

### Rate Limits

```bash
# INV announcement rate (messages/sec per peer)
export ANIMICA_P2P_TX_INV_RATE_PER_SEC=2000
export ANIMICA_P2P_TX_INV_RATE_BURST=4000

# TX_DATA bandwidth (bytes/sec per peer)
export ANIMICA_P2P_TX_DATA_RATE_BYTES_PER_SEC=5000000  # 5 MB/s
export ANIMICA_P2P_TX_DATA_RATE_BURST_BYTES=10000000   # 10 MB burst

# Mempool sync
export ANIMICA_P2P_TX_MEMPOOL_SYNC_SEC=15      # sync interval
export ANIMICA_P2P_TX_MEMPOOL_SYNC_LIMIT=2000  # max txs per sync
```

### Tuning Parameters

```bash
# INV batching
export ANIMICA_P2P_TX_INV_SEED_LIMIT=256   # max txs to sync on connect
export ANIMICA_P2P_TX_INV_SEED_BATCH=128   # batch size for initial sync

# Inflight tracking
export ANIMICA_P2P_TX_INFLIGHT_TIMEOUT=10  # seconds before retry
export ANIMICA_P2P_TX_INFLIGHT_RETRIES=2   # max retry attempts

# Caching
export ANIMICA_P2P_TX_KNOWN_TXIDS_CAP=50000  # per-peer known txids cache
```

## Caching & Deduplication

### Three-Level Cache System

1. **Global Seen Cache** (`_seen_tx` in p2p_service.py)
   - Tracks all transaction hashes seen by this node
   - Prevents reprocessing of already-seen transactions
   - LRU with configurable capacity

2. **Per-Peer Known Cache** (`known_txids` in PeerTxState)
   - Tracks which txids each peer already knows
   - Prevents sending INV for txs peer already has
   - TxIdSetLRU with 50K default capacity

3. **Reject Cache** (`_reject_cache` in TxRelayService)
   - Remembers rejected transactions with TTL
   - Prevents repeated validation of invalid txs
   - OrderedDict with time-based expiration

### Cache Updates

```
Event                           | Global Seen | Peer Known | Reject
-----------------------------------------------------------------
Local tx admitted               |     ✓       |            |
Receive TX_INV from peer       |             |     ✓      |
Send TX_INV to peer            |             |     ✓      |
Receive TX_DATA and admit      |     ✓       |     ✓      |
Receive TX_DATA and reject     |             |     ✓      |   ✓
Receive TX_NOTFOUND            |             |            |   ✓
```

## Troubleshooting

### Transaction not propagating

1. **Check TX relay is enabled**:
   ```bash
   # Get P2P status
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"debug_p2p_status","params":[],"id":1}' \
     | jq '.result.tx_relay'
   ```
   
   Should show:
   ```json
   {
     "enabled": true,
     "relay_flags": {
       "tx_relay": true,
       "tx_gossip": true,
       "mempool_gossip": true,
       "p2p_tx_enabled": true
     }
   }
   ```

2. **Verify peers are connected**:
   ```bash
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"debug_p2p_status","params":[],"id":1}' \
     | jq '.result.peers[] | {remote, handshake_complete, chain_match, relay_caps}'
   ```
   
   Each peer should have:
   - `handshake_complete: true`
   - `chain_match: true`
   - `relay_caps.txs: true`

3. **Check mempool service is running**:
   ```bash
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"debug_mempool_status","params":[],"id":1}'
   ```

4. **Monitor relay activity**:
   ```bash
   # Watch logs for relay messages
   tail -f /path/to/logs/animica.log | grep -E "TX_INV|TX_GET|TX_DATA|TX_ACCEPTED"
   ```

### Transaction not included in blocks

1. **Verify mining includes mempool**:
   ```bash
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc":"2.0",
       "method":"miner_getWork",
       "params":[{"include_mempool":true}],
       "id":1
     }' | jq '.result.mempoolEnabled'
   ```
   
   Should return `true`.

2. **Check transaction is in mempool**:
   ```bash
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc":"2.0",
       "method":"eth_getTransactionByHash",
       "params":["0x<tx_hash>"],
       "id":1
     }'
   ```

3. **Verify transaction meets mining criteria**:
   - Gas price meets current floor
   - Nonce is valid (sequential, no gaps)
   - Balance sufficient for gas + value
   - Not already included in recent blocks

## Performance Considerations

### Bandwidth Usage

With default settings:
- **INV messages**: 2000/sec × 200 txids/msg × 32 bytes/txid = ~12 MB/s theoretical max
- **TX_DATA**: 5 MB/s sustained, 10 MB burst per peer
- **Mempool sync**: 2000 txids × 32 bytes = 64 KB every 15s = ~4 KB/s

### Latency

Typical propagation timeline:
1. Submit tx to node A: 0ms
2. Admit to mempool: 1-5ms (signature verification)
3. Queue INV for peers: <1ms
4. INV flush: up to 200ms (next batch)
5. Peer receives INV: network latency (1-100ms)
6. Peer sends GET: <1ms
7. Node A sends DATA: <1ms
8. Peer receives DATA: network latency
9. Peer admits tx: 1-5ms

**Total: ~10-320ms** depending on network and batch timing

### Scalability

- **50 peers**: 100-250 MB/s peak bandwidth (INV + DATA)
- **10K active txs**: 64 KB × 50 peers = 3.2 MB for mempool sync every 15s
- **Memory per peer**: ~2 MB (50K known txids × 32 bytes + queues)

## Testing

### Unit Tests

Run TxRelayService tests:
```bash
cd /home/runner/work/all/all
python3 tests/integration/test_tx_propagation_e2e.py
```

### Manual Testing

Use the provided test script:
```bash
python3 test_mempool_tx_propagation_manual.py
```

### Multi-Node Testing

1. Start two nodes on different ports:
   ```bash
   # Node A
   export ANIMICA_NETWORK=devnet
   export ANIMICA_RPC_PORT=8545
   animica node up
   
   # Node B
   export ANIMICA_NETWORK=devnet
   export ANIMICA_RPC_PORT=8546
   export ANIMICA_P2P_SEEDS="/ip4/127.0.0.1/tcp/30333"
   animica node up
   ```

2. Submit transaction to Node A:
   ```bash
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc":"2.0",
       "method":"eth_sendRawTransaction",
       "params":["0x<signed_tx_hex>"],
       "id":1
     }'
   ```

3. Verify on Node B:
   ```bash
   curl -X POST http://localhost:8546/rpc \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc":"2.0",
       "method":"eth_getTransactionByHash",
       "params":["0x<tx_hash>"],
       "id":1
     }'
   ```

## Security Considerations

### DoS Protection

1. **Rate Limiting**: Per-peer message and bandwidth limits
2. **Reject Cache**: Prevents repeated validation of malicious txs
3. **Inflight Cap**: Limits concurrent requests per peer
4. **Size Limits**: MAX_TX_BYTES (512 KB) enforced at wire level

### Spam Prevention

1. **Fee Floors**: Transactions below minimum fee are rejected
2. **Nonce Ordering**: Gap detection prevents mempool pollution
3. **Balance Checks**: Insufficient balance txs rejected early
4. **Signature Verification**: PQ signatures validated before admission

### Privacy

Transactions are:
- **Not anonymized**: Transaction graph analysis possible
- **Publicly broadcast**: All peers see all transactions
- **Traceable by IP**: P2P connections leak origin information

Use mixnets or relayers for privacy-sensitive transactions.

## Future Enhancements

Potential improvements:
- [ ] Compact block relay (BIP 152-style)
- [ ] Priority-based mempool sync
- [ ] Bloom filter-based reconciliation
- [ ] Erasure coding for reliability
- [ ] Tor/I2P support for privacy
- [ ] Adaptive rate limiting based on peer behavior
- [ ] Transaction replacement (RBF) propagation
- [ ] Mempool fee histogram synchronization
