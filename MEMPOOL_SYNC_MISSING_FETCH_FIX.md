# Mempool Sync Missing Transaction Fetch Fix

## Issue Summary

**Problem**: Mempool showing as empty on a node despite peers reporting they know about specific transaction IDs.

**Symptom**: Running `animica mempool list` showed:
```
Peer-known txids (sample):
  peer=0x6c299a12c1 known_txids=1 sample=[0x3c255d68f3942d04c2af34a4a572565fdf61ee9650c21c6aab51350d418add92]
Mempool is empty (no pending transactions)
```

## Root Cause

The `mempool_sync_loop()` in `/p2p/txrelay.py` was only sending mempool sync requests to peers but never calling the existing `request_missing_known()` method to proactively fetch transactions that peers have advertised.

When transactions were announced via INV messages or mempool sync responses:
1. The txids were added to the peer's `known_txids` set
2. TX_GET requests were sent to fetch them
3. **BUT** if the TX_GET request timed out, failed, or the response was lost, the txid would remain in `known_txids` indefinitely without being re-requested

The `request_missing_known()` method existed to handle exactly this scenario, but it was never called automatically - only manually via the RPC method `p2p.importPeerKnownTxs`.

## Solution

### Code Changes

**File**: `/p2p/txrelay.py`

Added periodic call to `request_missing_known()` in the `mempool_sync_loop()`:

```python
async def mempool_sync_loop(self) -> None:
    self._running = True
    last_heartbeat = 0.0
    last_missing_fetch = 0.0  # NEW: Track last time we fetched missing txs
    while self._running:
        try:
            await asyncio.sleep(1.0)
            now = time.time()
            
            # ... existing mempool sync code ...
            
            # NEW: Periodically request missing transactions
            if now - last_missing_fetch >= self.mempool_sync_interval_s:
                last_missing_fetch = now
                requested = await self.request_missing_known(limit=128)
                if requested > 0:
                    log.info(
                        "TX_MISSING_FETCH",
                        extra={
                            "requested": requested,
                            "trigger": "mempool_sync_loop",
                        },
                    )
```

### How It Works

1. **Every `mempool_sync_interval_s` (default: 15 seconds)**:
   - The loop calls `request_missing_known(limit=128, trigger="mempool_sync_loop")`
   - This samples up to 128 transaction IDs from peers' `known_txids` sets

2. **For each sampled txid**, it checks if:
   - Not already in flight (being requested)
   - Not in the local mempool (already have it)
   - Not in the blockchain (already mined)
   - Not recently rejected (failed validation)

3. **If any txids meet these criteria**:
   - They are marked as inflight
   - TX_GET messages are sent to the appropriate peers
   - The `TX_MISSING_FETCH` log event is emitted

This ensures **eventual consistency** - even if initial INV messages or TX_GET responses are lost due to network issues, transactions will be fetched on the next sync cycle (every `mempool_sync_interval_s`, default 15 seconds).

## Testing

### New Tests

**File**: `/p2p/tests/test_mempool_sync_missing_fetch.py`

Two new tests verify the fix:

1. **`test_mempool_sync_loop_requests_missing_known`**:
   - Verifies that `mempool_sync_loop()` periodically calls `request_missing_known()`
   - Confirms transactions are automatically fetched

2. **`test_request_missing_known_fetches_peer_txids`**:
   - Tests that `request_missing_known()` correctly identifies missing transactions
   - Verifies TX_GET messages are sent for those transactions

Both tests pass successfully.

### Verification Script

**File**: `/verify_mempool_sync_fix.py`

A standalone verification script demonstrates the fix:
```bash
python verify_mempool_sync_fix.py
```

Output shows:
- Peer reports having 3 transactions
- Mempool sync loop automatically fetches them
- TX_GET is called with the correct txids

## Impact

### Benefits
- **Automatic recovery** from lost messages
- **No manual intervention** required (no need to call `p2p.importPeerKnownTxs`)
- **Minimal overhead** (only 128 txids sampled every 15 seconds)
- **Backward compatible** (no breaking changes)

### Performance
- Request rate: Up to 128 txids every 15 seconds
- Network impact: Minimal (only requests truly missing transactions)
- CPU impact: Negligible (simple sampling and filtering)

## Configuration

The fetch frequency can be adjusted via the `mempool_sync_interval_s` parameter when creating `TxRelayService`:

```python
service = TxRelayService(
    mempool_sync_interval_s=30.0,  # Fetch missing txs every 30 seconds
    # ... other params
)
```

Default is 15 seconds, which provides good balance between responsiveness and efficiency.

## Related Files

- Core fix: `/p2p/txrelay.py` (mempool_sync_loop method)
- Tests: `/p2p/tests/test_mempool_sync_missing_fetch.py`
- Verification: `/verify_mempool_sync_fix.py`
- Architecture docs: `/TX_PROPAGATION_ARCHITECTURE.md`

## Future Improvements

Possible enhancements:
1. Adaptive fetch frequency based on network conditions
2. Priority-based sampling (fetch higher-fee transactions first)
3. Metrics tracking for missing transaction fetch success rate
4. Exponential backoff for repeatedly failing txids

## Conclusion

This fix ensures that the mempool sync mechanism is resilient to network issues. Transactions that peers know about will eventually be fetched and included in the local mempool, even if initial propagation messages were lost.
