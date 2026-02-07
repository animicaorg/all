# PR Summary: Fix Mempool Autofetch Infinite Loop

## Problem
Users reported being stuck in an infinite loop when running `animica mempool list`:
- CLI shows "Peers know about N transaction(s)"
- System automatically requests them
- Says to check again in a few seconds
- But transactions never appear in mempool
- Loop repeats indefinitely

## Root Cause
When a peer responded with `TX_NOTFOUND`, the transaction ID was only removed from that **one peer's** `known_txids` list. Other peers still had it in their lists, so the system would keep requesting the same non-existent transaction from different peers forever.

## Solution
Modified `on_tx_notfound()` in `p2p/txrelay.py` to remove the transaction ID from **ALL peers'** `known_txids` when any peer responds with NOTFOUND.

### Code Change (17 lines)
In `p2p/txrelay.py`, replaced:
```python
# Clear from peer's known_txids since they don't have it
if state and txid in state.known_txids:
    state.known_txids.remove(txid)
```

With:
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

## Testing
Created comprehensive test suite (`test_fix_notfound_clears_all_peers.py`):
- ✅ Verifies txid is cleared from all peers when NOTFOUND received
- ✅ Verifies request_missing_known won't retry after cleanup
- ✅ Tests force=True behavior
- All existing related tests still pass

## Impact
- **Before**: Infinite loop, requires manual intervention, poor UX
- **After**: Proper cleanup, no loops, transactions handled correctly

## Files Changed (449 lines added, 3 deleted)
1. `p2p/txrelay.py` (+20, -3) - The fix
2. `test_fix_notfound_clears_all_peers.py` (+269) - Test suite
3. `FIX_MEMPOOL_AUTOFETCH_INFINITE_LOOP.md` (+163) - Documentation

## Security Considerations
✅ No security implications. This is a bug fix that improves correctness by preventing stale state.

## Monitoring
New log entry `TX_NOTFOUND_CLEARED_FROM_ALL_PEERS` tracks when the fix is triggered, showing:
- Transaction hash
- Number of peers cleared from
- Peer that reported NOTFOUND

## Related Issues
This complements earlier work in:
- `FIX_KNOWN_TXIDS_TO_MEMPOOL.md` - Stale state clearing
- `MEMPOOL_SYNC_MISSING_FETCH_FIX.md` - Original mempool sync

## Ready for Merge
✅ Fix implemented and tested
✅ Documentation complete
✅ No regressions
✅ Minimal, surgical change (17 lines)
