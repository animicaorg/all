# Transaction Propagation Troubleshooting Guide

This guide helps diagnose and fix transaction propagation issues in Animica's P2P network.

## Quick Verification

Use the manual verification script:

```bash
# Install dependencies
pip install aiohttp cbor2

# Run verification against two nodes
python tests/manual/verify_tx_propagation.py \
  --node-a http://localhost:8545 \
  --node-b http://localhost:8546

# Or check an existing transaction
python tests/manual/verify_tx_propagation.py \
  --tx-hash 0x... \
  --node-a http://localhost:8545 \
  --node-b http://localhost:8546
```

## Architecture Overview

Transaction propagation uses a multi-phase protocol:

```
Node A (submits tx)                    Node B (receives tx)
  |                                         |
  | 1. RPC: tx.sendRawTransaction          |
  |    ↓                                    |
  | 2. Mempool admission                   |
  |    ↓                                    |
  | 3. TxRelayService.on_mempool_add()     |
  |    ↓                                    |
  | 4. Queue TX_INV ──────────────────────→| 5. Receive TX_INV
  |                                         |    ↓
  |                                         | 6. Check: has_tx? → No
  |                                         |    ↓
  | 8. Receive TX_GET ←───────────────────-| 7. Send TX_GET
  |    ↓                                    |
  | 9. Send TX_DATA ──────────────────────→| 10. Receive TX_DATA
  |                                         |     ↓
  |                                         | 11. Validate & admit to mempool
```

### Key Components

1. **TxRelayService** (`p2p/txrelay.py`):
   - Manages INV/GET/DATA message flow
   - Maintains caches: `known_txids`, `inflight`, `reject_cache`
   - Runs background loops: `inv_flush_loop()`, `inflight_timeout_loop()`, `mempool_sync_loop()`

2. **P2PService** (`p2p/node/p2p_service.py`):
   - Integrates TxRelayService
   - Routes TX_INV, TX_GET, TX_DATA, TX_NOTFOUND messages
   - Registers peers after HELLO handshake

3. **RPC Layer** (`rpc/methods/tx.py`):
   - `tx.sendRawTransaction` → `_gossip_tx_to_peers()` → `p2p_service.relay_tx()`

## Common Issues

### Issue 1: Transactions Not Leaving Node A

**Symptoms:**
- Tx appears in Node A mempool
- Node A logs show `TX_ACCEPT_LOCAL` but no `TX_INV_SEND`
- Node B never sees the tx

**Diagnosis:**
```bash
# Check if TxRelayService loops are running
docker-compose logs node1 | grep TX_RELAY_HEARTBEAT

# Check if tx relay is enabled
curl http://localhost:8545/rpc -H 'content-type: application/json' -d \
  '{"jsonrpc":"2.0","id":1,"method":"p2p.status","params":[]}' | jq '.result.tx_relay_v2'
```

**Fixes:**
1. Ensure `ANIMICA_P2P_TX_RELAY=1` is set (default: enabled)
2. Verify `inv_flush_loop()` is started in P2PService (should see heartbeats every 10s)
3. Check that `relay_tx()` is called after tx admission

### Issue 2: INV Sent But No GET Received

**Symptoms:**
- Node A logs show `TX_INV_SEND`
- Node B logs show `TXIDS_LEARNED` from `tx_inv`
- But no `TX_GET_SENT` from Node B

**Diagnosis:**
```bash
# Check if Node B already has the tx (either in mempool or chain)
docker-compose logs node2 | grep -A5 "TXIDS_LEARNED"

# Look for has_tx or has_chain_tx checks
docker-compose logs node2 | grep "TX_GET_SENT"
```

**Fixes:**
1. **Already in mempool**: Node B may already have the tx (duplicate)
2. **Already in chain**: Tx was mined before propagation
3. **Reject cache**: Node B rejected this tx recently (TTL: 5-30s)
4. **Inflight**: Request already pending for this txid

### Issue 3: GET Sent But No DATA Received

**Symptoms:**
- Node B logs show `TX_GET_SENT`
- Node A logs show `TX_GET_RECV`
- But no `TX_DATA_SEND` from Node A

**Diagnosis:**
```bash
# Check if Node A still has the tx in mempool
docker-compose logs node1 | grep -A5 "TX_GET_RECV"

# Check for TX_NOTFOUND
docker-compose logs node1 | grep TX_NOTFOUND
```

**Fixes:**
1. **Tx mined**: Between INV and GET, the tx was mined and removed from mempool
2. **Tx evicted**: Mempool eviction policy removed the tx
3. **Rate limited**: TX_DATA send was rate-limited (check for "rate_limited" in logs)

### Issue 4: DATA Received But Not Admitted

**Symptoms:**
- Node B logs show `TX_DATA_RECV`
- But no `TX_ACCEPTED` log
- Instead see `TX_REJECTED`

**Diagnosis:**
```bash
# Check rejection reason
docker-compose logs node2 | grep -B2 -A2 TX_REJECTED

# Common rejection reasons:
# - hash_mismatch: computed hash != announced txid
# - oversize: tx exceeds max_tx_bytes
# - chain_id: wrong network
# - signature: invalid PQ signature
# - nonce: nonce too low or gap
# - balance: insufficient funds
```

**Fixes:**
- **hash_mismatch**: Data corruption in transit (rare, check network)
- **oversize**: Increase `max_tx_bytes` if needed
- **chain_id**: Ensure all nodes use same chain ID
- **signature/nonce/balance**: Valid admission failures (expected)

### Issue 5: No P2P Peers

**Symptoms:**
- `p2p.listPeers` returns empty array
- Nodes can't discover each other

**Diagnosis:**
```bash
# Check peer count
curl http://localhost:8545/rpc -H 'content-type: application/json' -d \
  '{"jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]}' | jq '.result | length'

# Check seeds configuration
docker-compose logs node1 | grep -i seed
```

**Fixes:**
1. Check `docker-compose.multinode.yml` seeds are configured correctly
2. Ensure nodes are on same network (bridge network in Docker)
3. Verify ports are exposed: 30333 (TCP), 443 (UDP/QUIC)
4. Check firewall rules (if running on different hosts)

## Debug Logging

Enable verbose logging to see all relay messages:

```bash
# In docker-compose.multinode.yml or .env
ANIMICA_LOG_LEVEL=DEBUG
ANIMICA_P2P_TX_RELAY=1
```

Key log patterns to grep for:

```bash
# Full propagation flow
docker-compose logs node1 | grep -E "TX_ACCEPT_LOCAL|TX_INV_SEND"
docker-compose logs node2 | grep -E "TXIDS_LEARNED|TX_GET_SENT|TX_DATA_RECV|TX_ACCEPTED"

# Heartbeats (every 10s, confirms loops are running)
docker-compose logs | grep TX_RELAY_HEARTBEAT

# Errors
docker-compose logs | grep -E "ERROR|WARNING" | grep -i tx
```

## Configuration Reference

Environment variables that affect tx propagation:

```bash
# Enable/disable tx relay (default: enabled)
ANIMICA_P2P_TX_RELAY=1

# INV flush interval (default: 0.2s)
# Longer = more coalescing, shorter = faster propagation
ANIMICA_P2P_TX_INV_FLUSH_INTERVAL_S=0.2

# INV batch size (default: 200 txids per message)
ANIMICA_P2P_TX_INV_BATCH=200

# Inflight timeout (default: 10s)
# How long to wait for TX_DATA before retry
ANIMICA_P2P_TX_INFLIGHT_TIMEOUT_S=10

# Mempool sync interval (default: 15s)
# How often to request full mempool snapshot from peers
ANIMICA_P2P_TX_MEMPOOL_SYNC_SEC=15

# Rate limits
ANIMICA_P2P_TX_INV_RATE_PER_SEC=2000
ANIMICA_P2P_TX_INV_RATE_BURST=4000
```

## Testing Changes

### Unit Tests

```bash
# Run txrelay service tests
python3 -m pytest p2p/tests/test_txrelay_service_v2.py -xvs

# Run integration tests
RUN_INTEGRATION_TESTS=1 python3 -m pytest tests/integration/test_tx_propagation_e2e.py -xvs
```

### Multi-Node Docker Test

```bash
# Start nodes
docker-compose -f docker-compose.multinode.yml up -d

# Wait for startup (30s)
sleep 30

# Verify connectivity
curl http://localhost:8545/rpc -H 'content-type: application/json' -d \
  '{"jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]}' | jq

# Run verification script
python tests/manual/verify_tx_propagation.py \
  --node-a http://localhost:8545 \
  --node-b http://localhost:8546

# Clean up
docker-compose -f docker-compose.multinode.yml down
```

## Expected Behavior

**Normal propagation (2 nodes):**
```
t=0.0s:  Node A: RPC accepts tx
t=0.01s: Node A: TX_ACCEPT_LOCAL (mempool admits)
t=0.01s: Node A: on_mempool_add() queues INV
t=0.2s:  Node A: inv_flush_loop() → TX_INV_SEND
t=0.21s: Node B: TXIDS_LEARNED (receives INV)
t=0.21s: Node B: TX_GET_SENT (requests full tx)
t=0.22s: Node A: TX_GET_RECV
t=0.22s: Node A: TX_DATA_SEND
t=0.23s: Node B: TX_DATA_RECV
t=0.23s: Node B: validates hash
t=0.23s: Node B: TX_ACCEPTED (admits to mempool)
```

**Latency breakdown:**
- Local RPC → mempool admission: <10ms
- Mempool admission → INV queued: <1ms
- INV queued → INV sent: ~200ms (inv_flush_interval)
- INV sent → GET received: <50ms (network RTT)
- GET → DATA: <50ms (network RTT)
- DATA → admission: <10ms

**Total: ~320ms typical propagation time**

## FAQ

### Q: Why do transactions sometimes take seconds to propagate?

A: The `inv_flush_interval` (default 200ms) batches announcements. This is intentional to:
- Reduce message overhead (coalesce multiple txs)
- Apply rate limiting (avoid burst flooding)
- Provide back-pressure (prevent DoS)

If you need faster propagation, reduce `ANIMICA_P2P_TX_INV_FLUSH_INTERVAL_S=0.05` (50ms).

### Q: Can transactions be lost during propagation?

A: Transient failures (network errors, rate limits) are handled by:
- Inflight timeout + retry (up to 2 retries by default)
- Periodic mempool sync (every 15s)
- Manual sync via `p2p.syncMempool` RPC call

Permanent failures (invalid tx) are cached in `reject_cache` to avoid repeated attempts.

### Q: How does propagation work with >2 nodes?

The relay service broadcasts to ALL connected peers (fan-out):
```
Node A ──INV──> Node B
        ──INV──> Node C
        ──INV──> Node D
```

Each receiving node independently decides to GET based on whether it already has the tx.

### Q: What happens if a tx is mined while propagating?

Nodes check `has_chain_tx()` before issuing GET. If the tx is already in a block, they skip the GET request. The inflight entry times out naturally.

## Additional Resources

- **Code**: `p2p/txrelay.py` - TxRelayService implementation
- **Tests**: `p2p/tests/test_txrelay_service_v2.py` - Unit tests
- **Integration**: `tests/integration/test_tx_propagation_e2e.py` - End-to-end tests
- **Architecture**: `p2p/README.md` - P2P networking overview
- **Specification**: `p2p/docs/` - Protocol specifications
