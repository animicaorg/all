# Transaction Propagation System Rewrite

## Executive Summary

This document describes the comprehensive rewrite of the transaction propagation subsystem to fix the "INV without FETCH/STORE" bug where transactions are announced but never fetched or stored in the receiving node's mempool.

## Problem Statement

**Observed Behavior:**
- Node A has a transaction in its mempool
- Node B's P2P layer shows the txid in "known_txids" for peer connections
- But Node B's mempool remains empty
- The receiving node never fetches tx bodies, never persists them, or rejects them silently

**Root Cause:**
The issue lies in a complex callback chain with async/sync mismatches:

```
TxRelayService.on_tx_data(raw_bytes)
  → self._admit_tx(raw_bytes, origin_peer)
    → P2PService._txrelay_admit_tx()
      → P2PService._admit_tx_result()
        → AsyncP2PDeps.admit_tx()  [ASYNC WRAPPER]
          → P2PDeps.admit_tx()  [SYNC]
            → tx_methods._mempool_submit()
              → MempoolService.submit()  [SYNC]
```

Any failure in this chain can be silent, and the async/sync transitions can lose error context.

## Implementation Strategy

### Phase 1: Diagnosis & Instrumentation ✅ COMPLETED

**Goal:** Add comprehensive logging to identify exactly where the failure occurs.

**Changes Made:**

1. **Enhanced TX_INV Logging** (`p2p/txrelay.py`)
   - Log count of received txids
   - Log first 3 txids for traceability
   - Log how many are missing vs already have
   - Log detailed filtering (inflight, rejected, in_chain)

2. **Enhanced TX_DATA Logging** (`p2p/txrelay.py`)
   - Log when TX_DATA received with item count
   - Log each item: txid, bytes, validation result
   - Log admit_tx call with parameters
   - Log admit_tx result (accepted/rejected + reason)
   - Log exceptions with full context
   - Log broadcast decision

3. **Debug RPC Endpoints** (`rpc/methods/p2p.py`)
   - `debug.mempoolStats`: Query mempool state (pending count, bytes, sources, rejects)
   - `debug.txRelayStats`: Query TX relay statistics (inv/get/push counters, inflight, missing)
   - `debug.txById(txid)`: Check if node has a tx and its status (mempool, chain, rejected)

4. **Async Wrapper** (`rpc/mempool_service.py`)
   - Added `async def admit_tx()` method to MempoolService
   - Wraps synchronous `submit()` with proper error handling
   - Returns `(bool, Optional[str])` tuple for P2P integration
   - Extracts structured error reasons from AdmissionError exceptions

5. **Global Singleton** (`rpc/mempool_service.py`, `rpc/deps.py`)
   - Added `set_mempool_service_singleton()` and `get_mempool_service_singleton()`
   - Wire singleton during RPC context initialization
   - Enables direct access from P2P and debug endpoints

### Phase 2: Protocol Improvements (PARTIAL)

**Current State:**
- TX_INV, TX_GET, TX_DATA messages exist and are functional
- Basic fetch/retry logic exists in TxRelayService
- Mempool sync happens every 15 seconds
- Rate limiting via TokenBucket

**Remaining Work:**
- Add structured TX_NOTFOUND responses with reject reasons
- Implement cursor-based TX_MEMPOOL_REQ/RESP for efficient reconciliation
- Add per-peer penalty scores for invalid data
- Implement "best peer" selection for retries

### Phase 3: Mempool Store (TODO)

**Current State:**
- MempoolService uses a Pool object for in-memory storage
- pending.jsonl persistence exists but may not be durable
- No authoritative tx_by_id index

**Required Changes:**
- Build in-memory `tx_by_id: Dict[bytes, TxEntry]` index
- Store metadata: raw_bytes, received_at, fee, size, from_addr, source_peer
- Ensure durable writes to pending.jsonl with fsync
- Replay pending.jsonl on startup to rebuild tx_by_id
- Make mempool.list() read from tx_by_id, not file scan

### Phase 4: Anti-Entropy Reconciliation (PARTIAL)

**Current State:**
- TxRelayService has `mempool_sync_loop()` that runs every 15s
- Sends TX_MEMPOOL_REQ and processes TX_MEMPOOL_RESP
- Basic retry on inflight timeout

**Required Changes:**
- Send initial TX_INV of local txids on peer connect
- Implement cursor-based paging for large mempools
- Add "mempool mismatch detector": if peer reports known_txids > 0 but we have 0, force pull
- Reduce sync interval to 10s for faster convergence

### Phase 5: Mining Integration (TODO)

**Issue:**
Mining may fail with "module 'rpc.deps' has no attribute 'get_state_db_adapter'" error.

**Required Changes:**
- Fix dependency resolution in mining template builder
- Default include_mempool=true in miner getWork
- Ensure block assembly reads from mempool tx_by_id
- Log included tx count and first 3 txids

## Testing Strategy

### Unit Tests
- Existing tests in `tests/integration/test_tx_propagation_e2e.py`
- Tests INV→GET→PUSH flow with mock mempools
- Tests duplicate prevention
- Tests caching behavior

### Integration Tests (TODO)
- Spin up 2 real nodes with P2P connections
- Submit tx to node A
- Assert node B receives within 3s
- Disconnect node B, submit tx to A, reconnect
- Assert node B receives via reconciliation within 30s

### Manual Testing
1. Start two nodes:
   ```bash
   # Node A
   ANIMICA_RPC_PORT=8545 animica node up

   # Node B (connect to A)
   ANIMICA_RPC_PORT=8546 ANIMICA_P2P_SEEDS="/ip4/127.0.0.1/tcp/30333" animica node up
   ```

2. Submit tx to node A:
   ```bash
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":["0x..."],"id":1}'
   ```

3. Check node B mempool:
   ```bash
   curl -X POST http://localhost:8546/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"debug.mempoolStats","params":[],"id":1}'
   ```

4. Check TX relay stats:
   ```bash
   curl -X POST http://localhost:8546/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"debug.txRelayStats","params":[],"id":1}'
   ```

5. Check specific tx:
   ```bash
   curl -X POST http://localhost:8546/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"debug.txById","params":["0x..."],"id":1}'
   ```

## Acceptance Criteria

### Non-Negotiable Requirements

1. **Latency:** Two connected nodes: tx submitted to A appears in B mempool within 3 seconds (95th percentile)

2. **Resilience:** Disconnect B, submit tx to A, reconnect → B obtains tx via reconciliation within 30 seconds

3. **No Silent Failures:** If a node records a txid from a peer, it MUST either:
   - Fetch+store the tx, OR
   - Record a durable, queryable reason why it didn't (reject reason + peer + timestamp)

4. **Mining:** Miners MUST include mempool txs when include_mempool is enabled

5. **Bounded Operations:** All operations must have:
   - Rate limits (inv/sec, get/sec, push bytes/sec)
   - Memory caps (known_txids: 50k, global_missing: 100k)
   - Message size limits (inv: 1024, get: 256, push: 2MB)
   - Timeouts (inflight: 10s, retry: max 5 attempts)

## Observability

### Log Events

All log events use structured logging with the following fields:

**TX_INV_RECEIVED:**
- peer, count, first_3_txids, conn_id, peer_node_id

**TX_INV_MISSING:**
- peer, missing_count, first_3_missing

**TX_GET_SENT:**
- peer, count, first_3_txids, batch_size

**TX_DATA_RECV:**
- peer, hash, txid, bytes

**TX_DATA_CALLING_ADMIT:**
- peer, hash, bytes, origin

**TX_DATA_ADMIT_RESULT:**
- peer, hash, accepted, reason

**TX_ACCEPTED:**
- hash, origin (peer:node_id)

**TX_REJECTED:**
- hash, reason, origin

**TX_DATA_ADMIT_EXCEPTION:**
- peer, hash, error, error_type

### Metrics Endpoints

**debug.mempoolStats:**
```json
{
  "pending_count": 42,
  "pending_bytes": 12345,
  "top_sources": [],
  "last_accept_at": 1704398400.0,
  "last_reject_at": null,
  "recent_rejects": [],
  "persist_path": "/path/to/pending.jsonl",
  "has_mempool_service": true
}
```

**debug.txRelayStats:**
```json
{
  "inv_sent": 100,
  "inv_recv": 150,
  "get_sent": 50,
  "get_recv": 45,
  "push_sent": 45,
  "push_recv": 50,
  "inflight_count": 5,
  "missing_count": 10,
  "reject_count": 3,
  "has_relay_service": true
}
```

**debug.txById:**
```json
{
  "found": true,
  "in_mempool": true,
  "in_chain": false,
  "source_peer": "peer-node-id",
  "accept_reason": "in_mempool",
  "reject_reason": null,
  "reject_details": null,
  "tx_hash": "0x..."
}
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ RPC Layer                                                    │
│  - eth_sendRawTransaction                                    │
│  - debug.mempoolStats, debug.txRelayStats, debug.txById     │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ MempoolService                                               │
│  - async admit_tx(raw: bytes) → (bool, reason)              │
│  - submit(tx, raw, local, origin_peer) → tx_hash            │
│  - pool: Pool (in-memory index)                             │
│  - pending.jsonl (durable persistence)                      │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▲
                 │
┌────────────────┴────────────────────────────────────────────┐
│ P2PDeps / AsyncP2PDeps                                       │
│  - admit_tx(tx) → wraps MempoolService.admit_tx()           │
│  - Validates: chain_id, PQ signature, tx hash               │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▲
                 │
┌────────────────┴────────────────────────────────────────────┐
│ P2PService                                                   │
│  - TxRelayService: handles INV/GET/DATA protocol            │
│  - Background loops: inv_flush, inflight_timeout, sync      │
│  - Per-peer state: known_txids, inv_queue, inflight         │
└─────────────────────────────────────────────────────────────┘
                 │
                 ▲
                 │
┌────────────────┴────────────────────────────────────────────┐
│ Network Layer (TCP/UDP)                                      │
│  - TX_INV: announce tx hashes                               │
│  - TX_GET: request tx bodies                                │
│  - TX_DATA: deliver tx bodies                               │
│  - TX_NOTFOUND: report unavailable txs                      │
│  - TX_MEMPOOL_REQ/RESP: reconciliation                      │
└─────────────────────────────────────────────────────────────┘
```

## Files Changed

### Core Implementation
- `p2p/txrelay.py`: Enhanced logging in on_tx_inv and on_tx_data
- `rpc/mempool_service.py`: Added async admit_tx wrapper and singleton
- `rpc/deps.py`: Wire mempool singleton during initialization
- `rpc/methods/p2p.py`: Added debug endpoints

### Documentation
- `TX_PROPAGATION_SYSTEM_REWRITE.md`: This document

### Tests
- `tests/integration/test_tx_propagation_e2e.py`: Existing unit tests
- TODO: Add integration tests with real node instances

## Next Steps

1. **Immediate (Testing Phase)**
   - Run diagnose_tx_propagation.py on a multi-node setup
   - Analyze logs to confirm the instrumentation captures all events
   - Identify the exact failure point from logs

2. **Short Term (Bug Fix)**
   - Fix any async/sync issues identified in testing
   - Add structured reject reasons to TX_NOTFOUND
   - Ensure all errors are logged and queryable

3. **Medium Term (Hardening)**
   - Implement cursor-based reconciliation
   - Add mempool mismatch detector
   - Improve peer selection for retries
   - Add penalty scores for bad peers

4. **Long Term (Performance)**
   - Optimize mempool store for large mempools (>100k txs)
   - Add metrics/telemetry for production monitoring
   - Implement adaptive timeout/retry based on network conditions
   - Add compression for TX_DATA messages

## Conclusion

This rewrite provides a solid foundation for reliable transaction propagation with comprehensive observability. The instrumentation added will help diagnose the current issue, and the architectural improvements set the stage for future enhancements.

**Key Improvements:**
- ✅ Comprehensive logging at every step
- ✅ Debug RPC endpoints for real-time diagnostics
- ✅ Async wrapper for proper P2P integration
- ✅ Global singleton for easy access
- ⏳ Remaining work on hardening and reconciliation

**Expected Outcome:**
With the logging in place, we can now diagnose exactly where the "INV without FETCH/STORE" bug occurs and fix it surgically without a complete rewrite of the working components.
