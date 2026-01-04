# Reliable Mempool TX Propagation Implementation Summary

## Overview

This implementation ensures reliable transaction propagation across P2P nodes using a dual-path approach with anti-entropy mechanisms.

## Architecture

### Components

1. **TxRelayService** (`p2p/txrelay.py`)
   - Per-peer state tracking (known_txids, inv_queue, inflight)
   - Message handlers (TX_INV, TX_GET, TX_DATA, TX_MEMPOOL_REQ/RESP)
   - Background loops:
     - `inv_flush_loop()`: Flushes queued txids to peers every 200-500ms
     - `inflight_timeout_loop()`: Retries timed-out tx requests
     - `mempool_sync_loop()`: Anti-entropy sync every 15s

2. **MempoolService** (`rpc/mempool_service.py`)
   - Manages pending transaction pool
   - New: `_p2p_broadcast_callback` for triggering propagation
   - Persists mempool to `pending.jsonl` for restart recovery

3. **P2PService** (`p2p/node/p2p_service.py`)
   - Coordinates between mempool and tx relay
   - Registers peers with txrelay after handshake
   - Triggers initial mempool sync on peer connect
   - Periodic rebroadcast of pending txs

## Propagation Flow

### Path 1: Direct from Mempool (NEW - Reliable)

```
User submits tx via RPC
    ↓
tx.sendRawTransaction()
    ↓
mempool_service.submit(local=True)
    ↓
pool.add() [validates and adds to mempool]
    ↓
_p2p_broadcast_callback(tx_hash, raw)
    ↓
txrelay.on_mempool_add(txid, raw)
    ↓
Enqueue txid to all peers' inv_queue
    ↓
inv_flush_loop() flushes every 200-500ms
    ↓
Send TX_INV to peers
    ↓
Peers send TX_GET
    ↓
Send TX_DATA (full tx bytes)
    ↓
Peers admit to their mempool
```

### Path 2: Via RPC Layer (EXISTING - Best-effort backup)

```
User submits tx via RPC
    ↓
tx.sendRawTransaction()
    ↓
_mempool_submit() → mempool_service.submit()
    ↓
_gossip_tx_to_peers(raw)
    ↓
p2p_service.relay_tx(raw)
    ↓
_admit_tx_result() [validates and adds]
    ↓
txrelay.on_mempool_add(txid, raw)
    ↓
[same as Path 1 from here]
```

**Both paths converge at `txrelay.on_mempool_add()`, providing defense in depth.**

## Anti-Entropy Mechanisms

### 1. Mempool Sync on Peer Connect

When a peer connects:
```
_handle_hello_ack()
    ↓
txrelay.register_peer(conn_id)
    ↓
txrelay.request_mempool_sync(conn_id)
    ↓
Send TX_MEMPOOL_REQ
    ↓
Receive TX_MEMPOOL_RESP with peer's txids
    ↓
Request missing txs via TX_GET
```

### 2. Periodic Mempool Reconciliation

Every 15 seconds (configurable):
```
mempool_sync_loop()
    ↓
For each peer:
    Send TX_MEMPOOL_REQ
    ↓
    Receive TX_MEMPOOL_RESP
    ↓
    Request any missing txs
```

### 3. Periodic Rebroadcast

If enabled (via `ANIMICA_P2P_TX_INV_REANNOUNCE_INTERVAL_S`):
```
_rebroadcast_pending_txs()
    ↓
Get all pending txids from mempool
    ↓
txrelay.announce_txids(txids)
    ↓
Enqueue to all peers' inv_queue
```

### 4. Persistence and Recovery

On node start:
```
MempoolService.__init__()
    ↓
_load_persisted() reads pending.jsonl
    ↓
submit(tx, local=True) for each persisted tx
    ↓
Triggers _p2p_broadcast_callback (if P2P started)
    OR
    Picked up by periodic rebroadcast loop
```

## DoS Protection

### Rate Limits (TokenBucket)

- **INV rate**: 2000 txids/sec, burst 4000 (configurable)
- **TX_DATA rate**: 5MB/sec, burst 10MB (configurable)

### Size Limits

- **Max INV per message**: 1024 txids
- **Max tx size**: Enforced by `max_tx_bytes` (default 1MB)
- **Max GET batch**: 256 txids per request

### Deduplication

- **known_txids** per peer: LRU cache (50k cap) prevents reprocessing
- **inv_queue** per peer: Only queue if not already known
- **inflight tracker**: Prevents duplicate GET requests
- **reject_cache**: Short-term cache (5-30s) for rejected txs

### Request Tracking

- **inflight timeout**: 10s default, retries up to 2 times
- **Retry logic**: Try different peer if original fails
- **Bounded retries**: Give up after max_retries to prevent infinite loops

## Configuration

Environment variables:

```bash
# Enable/disable tx relay (default: true)
ANIMICA_P2P_TX_RELAY=true

# Mempool sync interval (default: 15 seconds)
ANIMICA_P2P_TX_MEMPOOL_SYNC_SEC=15

# Mempool sync limit (default: 2000 txids)
ANIMICA_P2P_TX_MEMPOOL_SYNC_LIMIT=2000

# INV rate limits
ANIMICA_P2P_TX_INV_RATE_PER_SEC=2000
ANIMICA_P2P_TX_INV_RATE_BURST=4000

# TX_DATA rate limits (bytes/sec)
ANIMICA_P2P_TX_DATA_RATE_BYTES_PER_SEC=5000000
ANIMICA_P2P_TX_DATA_RATE_BURST_BYTES=10000000

# Rebroadcast interval (0 = disabled)
ANIMICA_P2P_TX_INV_REANNOUNCE_INTERVAL_S=0
```

## Logs and Metrics

Key log events:

```
TX_ACCEPT_LOCAL      - Tx accepted locally
TX_INV_SEND          - INV sent to peer
TX_INV_RECV          - INV received from peer
TXIDS_LEARNED        - Learned txids from peer (via INV or sync)
TX_GET_SENT          - GET request sent
TX_GET_RECV          - GET request received
TX_DATA_SEND         - TX_DATA sent to peer
TX_DATA_RECV         - TX_DATA received from peer
TX_ACCEPTED          - Tx admitted to mempool from peer
TX_REJECTED          - Tx rejected (with reason)
TX_SYNC_REQ          - Mempool sync request sent
TX_SYNC_RESP_SEND    - Mempool sync response sent
TX_SYNC_RESP_RECV    - Mempool sync response received
TX_RELAY_HEARTBEAT   - Periodic heartbeat (loop health)
```

Use these logs to diagnose propagation issues:
```bash
# Monitor tx propagation
tail -f logs/p2p.log | grep -E "TX_INV|TX_GET|TX_DATA|TX_SYNC"

# Check relay health
tail -f logs/p2p.log | grep "TX_RELAY_HEARTBEAT"
```

## Testing

### Unit Tests

Run the relay service unit tests:
```bash
python3 test_mempool_p2p_callback_integration.py
python3 test_mempool_tx_propagation_manual.py
```

### Integration Tests

```bash
# Run full integration test suite
python3 tests/integration/test_tx_propagation_e2e.py
```

### Manual Testing

1. Start two nodes with P2P enabled:
   ```bash
   # Node A (port 8545)
   ./setup.sh --chain-id 1337 --port 8545 --p2p-port 30333
   
   # Node B (port 8546, connected to A)
   ./setup.sh --chain-id 1337 --port 8546 --p2p-port 30334 \
     --seeds "/ip4/127.0.0.1/tcp/30333"
   ```

2. Submit a transaction to Node A:
   ```bash
   animica tx send --to 0x... --value 1000000000000000000 --rpc http://localhost:8545/rpc
   ```

3. Verify propagation to Node B:
   ```bash
   animica mempool list --rpc http://localhost:8546/rpc
   ```

Expected: Tx appears in Node B mempool within 2-5 seconds.

## Troubleshooting

### Issue: Tx not propagating

**Symptoms**: Submit tx to node A, doesn't appear in node B mempool

**Diagnosis**:
1. Check if nodes are connected:
   ```bash
   animica p2p peers --rpc http://localhost:8545/rpc
   ```
2. Check tx relay enabled:
   ```bash
   grep "ANIMICA_P2P_TX_RELAY" .env
   ```
3. Check logs for INV/GET/DATA:
   ```bash
   grep -E "TX_INV|TX_GET|TX_DATA" logs/p2p.log | tail -50
   ```

**Solutions**:
- Ensure `ANIMICA_P2P_TX_RELAY=true`
- Verify peers are connected and not banned
- Check firewall rules for P2P ports
- Increase log level: `RUST_LOG=debug` or `ANIMICA_LOG_LEVEL=DEBUG`

### Issue: Empty mempool after restart

**Symptoms**: Restart node, mempool is empty

**Diagnosis**:
1. Check if persistence is enabled:
   ```bash
   ls -la ~/.animica/chain-1337/mempool/pending.jsonl
   ```
2. Check logs for restore:
   ```bash
   grep "Restored mempool entries" logs/rpc.log
   ```

**Solutions**:
- Ensure `ANIMICA_MEMPOOL_PERSIST=true`
- Check file permissions on data directory
- Verify pending.jsonl is valid JSON Lines format

### Issue: Slow propagation (>10s)

**Symptoms**: Tx takes a long time to appear on peers

**Diagnosis**:
1. Check inv flush interval:
   ```bash
   # Default is 200-500ms, should be fast
   ```
2. Check network latency:
   ```bash
   ping <peer_ip>
   ```
3. Check if rate limited:
   ```bash
   grep "rate_limited" logs/p2p.log
   ```

**Solutions**:
- Reduce `ANIMICA_P2P_TX_MEMPOOL_SYNC_SEC` for faster anti-entropy
- Increase rate limits if being throttled
- Check for network congestion

## Future Enhancements

Potential improvements:

1. **Compact Set Reconciliation**: Use IBLT or Golomb-coded sets instead of full txid lists
2. **Priority Propagation**: Propagate high-fee txs faster
3. **Tx Compression**: Compress TX_DATA payloads
4. **Smart Peer Selection**: Route txs through well-connected peers first
5. **Metrics Dashboard**: Real-time view of propagation health
6. **Adaptive Rate Limits**: Adjust based on peer behavior
7. **Mempool Diff**: Only sync delta since last sync instead of full list

## References

- TX relay protocol: `p2p/txrelay.py`
- Message types: `p2p/messages_tx.py`
- P2P service integration: `p2p/node/p2p_service.py`
- Mempool service: `rpc/mempool_service.py`
- Specs: `p2p/specs/tx_relay.md` (if exists)
