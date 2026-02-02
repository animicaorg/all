# Fix: Add Known TxIDs to Local Mempool

## Problem Statement

Users reported that `animica mempool list` showed peers having known transaction IDs, but the local mempool was empty:

```
Peer-known txids (sample):
  peer=0xecf34d9eab conn_id=0x8da0c3f4-c known_txids=1 sample=[0x6c9fac12d83a...]
  peer=0xecf34d9eab conn_id=0x28bc5782-e known_txids=1 sample=[0x6c9fac12d83a...]
Mempool is empty (no pending transactions)
```

## Root Cause Analysis

The issue was caused by **stale transaction state** in the `TxRequestManager`:

1. When a transaction is received and admitted to the mempool, it's marked with state `"accepted_in_mempool"`
2. If that transaction is later evicted from the mempool (due to capacity limits, replacement, or other reasons), the state persists
3. The `can_request()` method returns `False` for transactions in `"accepted_in_mempool"` state
4. The watchdog loop's `request_missing_known()` skips these transactions because `can_request()` returns `False`
5. Result: Transactions remain in peers' `known_txids` but are never re-requested

### Code Flow

```python
# Old behavior (before fix):
request_missing_known():
    for txid in peer.known_txids:
        if self._request_mgr.can_request(txid):  # Returns False if state="accepted_in_mempool"
            request_tx(txid)                      # Never reached!
```

## Solution

Modified `request_missing_known()` to detect and clear stale states:

```python
# New behavior (after fix):
request_missing_known():
    for txid in peer.known_txids:
        has_tx = await self._has_tx(txid)  # Actually check mempool
        if has_tx:
            continue  # Really in mempool, skip
        
        # Check for stale state
        state = self._request_mgr.get_state(txid)
        if state and state.state == "accepted_in_mempool":
            # Stale! Clear it
            self._request_mgr.clear_state(txid)
            log.info("TX_STATE_CLEARED", ...)
        
        if self._request_mgr.can_request(txid):
            request_tx(txid)  # Now this works!
```

## Changes Made

### 1. Modified `request_missing_known()` (p2p/txrelay.py)

- Added logic to detect stale `"accepted_in_mempool"` states
- Clear stale states before checking `can_request()`
- Log `TX_STATE_CLEARED` events for debugging

### 2. Added `clear_state()` method to `TxRequestManager` (p2p/txrelay.py)

- Public method for clearing transaction states
- Better encapsulation than accessing private `_states` directly
- Returns `True` if state was present and cleared

### 3. Added comprehensive tests (p2p/tests/test_stale_accepted_state_fix.py)

Three new test cases:
- `test_stale_accepted_state_is_cleared` - Verifies stale states are detected and cleared
- `test_accepted_state_preserved_if_tx_in_mempool` - Verifies states aren't cleared when tx is actually in mempool
- `test_watchdog_fetches_after_clearing_stale_state` - Verifies watchdog loop works after clearing stale states

## Testing

### New Tests
```bash
$ python3 -m pytest p2p/tests/test_stale_accepted_state_fix.py -xvs
✅ test_stale_accepted_state_is_cleared - PASSED
✅ test_accepted_state_preserved_if_tx_in_mempool - PASSED
✅ test_watchdog_fetches_after_clearing_stale_state - PASSED
```

### Existing Tests (No Regressions)
```bash
$ python3 -m pytest p2p/tests/test_mempool_sync_missing_fetch.py -xvs
✅ test_mempool_sync_loop_requests_missing_known - PASSED
✅ test_request_missing_known_fetches_peer_txids - PASSED

$ python3 -m pytest p2p/tests/test_txrelay_watchdog.py -xvs
✅ test_watchdog_instantiation - PASSED
✅ test_watchdog_loop_runs - PASSED
✅ test_watchdog_requests_missing_transactions - PASSED
✅ test_watchdog_default_configuration - PASSED
```

## Impact

### Before Fix
- Transactions marked as accepted but later evicted would never be re-requested
- Mempool could appear empty even though peers had transactions available
- Manual intervention required (`p2p.importPeerKnownTxs` RPC call)

### After Fix
- Stale states are automatically detected and cleared by the watchdog loop
- Transactions are automatically re-requested from peers
- No manual intervention needed
- Mempool stays synchronized with peers

## How It Works in Production

1. **Watchdog loop runs every 3 seconds** (configurable via `ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_SEC`)
2. **Samples up to 256 transactions** from peers' `known_txids` (configurable via `ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_LIMIT`)
3. **For each sampled transaction:**
   - Checks if it's in the local mempool
   - If not, checks if state is stale
   - Clears stale state if detected
   - Re-requests transaction from peer

4. **Log monitoring:**
   ```
   TX_WATCHDOG_FETCH: requested=5 trigger=watchdog
   TX_STATE_CLEARED: hash=0x6c9fac... reason=marked_accepted_but_not_in_mempool
   TX_GET_SENT: peer=0xecf34d9eab count=5
   ```

## Edge Cases Handled

1. **Transaction actually in mempool** - State is NOT cleared
2. **Transaction in blockchain** - Marked as dropped, not re-requested
3. **Transaction recently rejected** - Skipped by reject cache
4. **Transaction in flight** - Skipped if deadline hasn't expired
5. **Cooldown period** - Respected after clearing stale state

## Configuration

Default values work well for most scenarios:

```python
# Watchdog interval (seconds)
ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_SEC=3

# Max transactions per watchdog cycle
ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_LIMIT=256
```

To make watchdog more/less aggressive:
- Decrease interval for faster detection
- Increase limit to sample more transactions per cycle

## Related Documentation

- `MEMPOOL_SYNC_MISSING_FETCH_FIX.md` - Original mempool sync fix
- `TX_PROPAGATION_ARCHITECTURE.md` - Overall tx propagation design
- `p2p/txrelay.py` - Transaction relay implementation

## Summary

This fix ensures that the mempool watchdog loop can successfully fetch transactions that peers know about, even if those transactions were previously admitted and later evicted. The solution is minimal, focused, and well-tested with no regressions in existing functionality.

**Result:** When users run `animica mempool list` and see peers with `known_txids`, those transactions will now be automatically fetched and appear in the local mempool.
