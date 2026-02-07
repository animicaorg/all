# Fix: Mempool Autofetch Infinite Loop

## Problem Statement

When running `animica mempool list`, users experienced an infinite loop where:
1. The CLI shows "Peers know about N transaction(s)"
2. The system automatically calls `p2p.importPeerKnownTxs` to request them
3. The CLI says "✓ Requested N transaction(s) from peers. Run 'animica mempool list' again in a few seconds to see them."
4. But when run again, the mempool is still empty
5. The cycle repeats forever

Example from production:
```
Mempool is empty (no pending transactions)

💡 Tip: Peers know about 2 transaction(s). Fetching them automatically...
✓ Requested 2 transaction(s) from peers. Run 'animica mempool list' again in a few seconds to see them.
```

## Root Cause Analysis

### The Issue

When a peer responds with `TX_NOTFOUND` (indicating they don't have a transaction they previously announced):

**OLD BEHAVIOR** (p2p/txrelay.py line 1224-1226):
```python
# Clear from peer's known_txids since they don't have it
if state and txid in state.known_txids:
    state.known_txids.remove(txid)
```

The txid was only removed from the **responding peer's** `known_txids`. Other peers still had it in their `known_txids`.

### The Loop

1. Node A and Node B both have `txid_X` in their `known_txids`
2. User runs `animica mempool list`
3. CLI calls `p2p.importPeerKnownTxs` with `force=True`
4. System samples Node A's `known_txids`, finds `txid_X`
5. Requests `txid_X` from Node A
6. Node A responds with `TX_NOTFOUND` (transaction was evicted/mined)
7. `txid_X` is removed from Node A's `known_txids` only
8. `txid_X` is added to reject cache (TTL: 5-30 seconds)
9. User runs command again (after a few seconds)
10. Reject cache expires (or is cleared by `force=True`)
11. System samples Node B's `known_txids`, finds `txid_X` again
12. Requests `txid_X` from Node B
13. Node B also responds with `TX_NOTFOUND`
14. Loop continues forever as long as any peer has `txid_X` in `known_txids`

## The Fix

**NEW BEHAVIOR** (p2p/txrelay.py lines 1224-1238):
```python
# Clear from ALL peers' known_txids since the responding peer doesn't have it.
# This prevents infinite loops where multiple peers report knowing about a
# transaction that none of them actually have (e.g., after it was mined or evicted).
removed_from = []
for peer_id, peer_state in self._peer_state.items():
    if txid in peer_state.known_txids:
        peer_state.known_txids.remove(txid)
        removed_from.append(peer_id)
if removed_from:
    log.info(
        "TX_NOTFOUND_CLEARED_FROM_ALL_PEERS",
        extra={
            "hash": txid.hex(),
            "cleared_from_peer_count": len(removed_from),
            "reporting_peer": conn_id,
        },
    )
```

When any peer responds with `TX_NOTFOUND`, the txid is now removed from **ALL peers'** `known_txids`.

### Why This Works

If one peer doesn't have a transaction they previously announced, it means:
- The transaction was mined into a block (all nodes should have removed it)
- The transaction was evicted from mempool (likely the same on other nodes)
- The transaction is invalid and was dropped

In all these cases, other peers are unlikely to have it either. By removing it from all peers' `known_txids`, we prevent the infinite loop.

## Testing

### Test: `test_fix_notfound_clears_all_peers.py`

Two comprehensive test cases:

**Test 1: TX_NOTFOUND clears from ALL peers**
1. Three peers (A, B, C) announce the same txid
2. Verify all three have it in `known_txids`
3. Peer A responds with `TX_NOTFOUND`
4. Verify txid is removed from **all three** peers
5. Verify `request_missing_known` won't retry (no peers have it)

**Test 2: TX_NOTFOUND with force=True**
1. Two peers announce the same txid
2. One peer responds with `TX_NOTFOUND`
3. Verify txid is removed from both peers
4. Verify `force=False` doesn't request (txid was removed)

### Running Tests

```bash
# Run the new test
python3 test_fix_notfound_clears_all_peers.py

# Run related existing tests
python3 -m pytest p2p/tests/test_txrelay_stale_state_fix.py -xvs
python3 -m pytest p2p/tests/test_request_missing_known_eligibility.py -xvs
```

All tests pass ✅

## Impact

### Before Fix
- Infinite loop when peers report `known_txids` for transactions they don't have
- Users must manually intervene or wait for all reject cache TTLs to expire
- Poor user experience with repeated "Fetching automatically..." messages
- Unnecessary network traffic requesting the same non-existent transactions

### After Fix
- Transaction IDs are properly cleaned up when NOTFOUND
- No infinite loops
- `animica mempool list` works as expected
- Better user experience
- Reduced network traffic

## Edge Cases Handled

1. **Single peer has txid**: Works as before, just removes from one peer
2. **Multiple peers have same txid**: All are cleaned up simultaneously
3. **Transaction actually exists elsewhere**: The peer who has it can still announce/send it
4. **Concurrent announcements**: Lock protects peer state during cleanup

## Related Documentation

- `FIX_KNOWN_TXIDS_TO_MEMPOOL.md` - Earlier fix for stale state clearing
- `MEMPOOL_SYNC_MISSING_FETCH_FIX.md` - Original mempool sync implementation
- `TX_PROPAGATION_ARCHITECTURE.md` - Overall architecture

## Monitoring

Look for this log entry to confirm the fix is working:

```json
{
  "event": "TX_NOTFOUND_CLEARED_FROM_ALL_PEERS",
  "hash": "0xe1f3c9ba08ce72c08a4800b2cdb798cd633bea903327497aa5594d1d818bd81c",
  "cleared_from_peer_count": 3,
  "reporting_peer": "0x03d2c4ea4a"
}
```

This indicates that when one peer reported NOTFOUND, the txid was successfully removed from multiple peers' `known_txids`.

## Summary

This fix resolves the infinite loop in `animica mempool list` by ensuring that when a peer reports they don't have a transaction, it's removed from **all** peers' tracking, not just the one that responded. This simple change prevents the system from repeatedly requesting the same non-existent transaction from different peers.
