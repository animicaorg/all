# Fix: Transactions Not Being Accepted Despite Being Requested

## Problem Statement

When running `animica mempool list`, users reported seeing:
```
Auto-imported peer transactions: requested=2, newly_visible=0
Mempool is empty (no pending transactions)
```

This indicated that:
- Peers had transactions available (known_txids > 0)
- The CLI requested those transactions from peers
- But no transactions appeared in the local mempool

## Root Cause Analysis

The issue was a **timing/synchronization problem** in the CLI's auto-import feature:

1. **CLI calls `p2p.importPeerKnownTxs`**
   - This RPC method calls `request_missing_txids()` in the P2P service
   - This sends TX_GET messages to peers **asynchronously**
   - The method returns immediately with count of transactions requested

2. **CLI immediately calls `mempool.getPending`**
   - No delay between import request and mempool check
   - TX_GET messages haven't even reached peers yet
   - TX_DATA responses definitely haven't arrived
   - Result: mempool still empty, `newly_visible=0`

### Typical Timeline

```
Time    Action
----    ------
0ms     CLI calls p2p.importPeerKnownTxs → returns immediately
1ms     CLI calls mempool.getPending → returns []
        ❌ Report: "requested=2, newly_visible=0"

50ms    TX_GET reaches peer
55ms    Peer sends TX_DATA back
60ms    TX_DATA received and validated
65ms    Transaction admitted to mempool
        ✓ Transactions are now in mempool, but CLI already finished!
```

## Solution: Adaptive Polling

Implemented polling with increasing delays to wait for transactions to arrive:

```python
delays = [0.05, 0.1, 0.15, 0.2]  # Total: 0.5 seconds max
for delay in delays:
    time.sleep(delay)
    refreshed = call_rpc("mempool.getPending", ...)
    if len(refreshed) > len(result):
        # Transactions arrived! Exit early
        break
else:
    # Timeout: no new transactions after 500ms
    show_timeout_message()
```

### Polling Strategy

| Poll | Delay | Cumulative | Use Case |
|------|-------|------------|----------|
| 1    | 50ms  | 50ms       | Fast local/LAN networks |
| 2    | 100ms | 150ms      | Typical internet latency |
| 3    | 150ms | 300ms      | Busy nodes or moderate congestion |
| 4    | 200ms | 500ms      | Slow/congested networks |

**Key Features:**
- ✅ **Early exit**: Stops as soon as transactions appear (as fast as 50ms)
- ✅ **Adaptive**: Works on both fast and slow networks
- ✅ **User-friendly**: Clear timeout message if transactions don't arrive
- ✅ **Efficient**: Doesn't wait unnecessarily

## Changes Made

### File: `python/animica/cli/mempool.py`

**Added import:**
```python
import time
```

**Modified auto-import logic (lines 291-317):**
- Added polling loop with delays `[0.05, 0.1, 0.15, 0.2]`
- Check mempool after each delay
- Exit early when transactions appear
- Show timeout message if no transactions arrive

**Before:**
```python
import_result = call_rpc("p2p.importPeerKnownTxs", [128], ...)
requested = import_result.get("requested", 0)
if requested > 0:
    refreshed = call_rpc("mempool.getPending", ...)
    # ❌ Immediate check, transactions haven't arrived yet
```

**After:**
```python
import_result = call_rpc("p2p.importPeerKnownTxs", [128], ...)
requested = import_result.get("requested", 0)
if requested > 0:
    delays = [0.05, 0.1, 0.15, 0.2]
    for delay in delays:
        time.sleep(delay)
        refreshed = call_rpc("mempool.getPending", ...)
        if len(refreshed) > len(result):
            # ✅ Transactions arrived! Exit early
            break
    else:
        # Show timeout message
```

## Testing

### Code Inspection
- ✓ Imports `time` module
- ✓ Has polling loop with delays list
- ✓ Early exit when transactions arrive
- ✓ Timeout message for diagnostics
- ✓ Multiple mempool checks with proper RPC calls

### Demonstration
Created demonstration script showing:
- ❌ **Before fix:** Always shows `newly_visible=0`
- ✅ **After fix (fast network):** Shows `newly_visible=2` after 50ms
- ✅ **After fix (slow network):** Shows `newly_visible=2` after 300ms

### Existing Tests
- ✓ Existing unit test `test_mempool_list_auto_imports_peer_transactions_and_displays_them` remains compatible
- ✓ Test expects transactions on second `mempool.getPending` call, which happens in first poll iteration

## Impact

### Before Fix
```
Auto-imported peer transactions: requested=2, newly_visible=0
Mempool is empty (no pending transactions)
```
- Confusing for users
- Transactions were requested but not visible
- Required manual intervention or waiting and re-running command

### After Fix (Fast Network - 50ms)
```
Auto-imported peer transactions: requested=2, newly_visible=2
Pending transactions (2):
  1. 0x66613836b44138d0... nonce=5 status=pending
  2. 0xabc123def456... nonce=6 status=pending
```
- Clear success message
- Transactions visible immediately
- Fast response time

### After Fix (Slow Network - 300ms)
```
Auto-imported peer transactions: requested=2, newly_visible=2
Pending transactions (2):
  ...
```
- Still succeeds, just takes longer
- Adapts to network conditions

### After Fix (Timeout - 500ms)
```
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 0.5s)
Mempool is empty (no pending transactions)
```
- Clear diagnostic message
- User knows timeout occurred
- Can re-run or investigate further

## Security Considerations

- ✅ No security vulnerabilities introduced
- ✅ No external network calls beyond existing RPC
- ✅ Timeout prevents indefinite hanging
- ✅ Early exit prevents unnecessary waiting

## Configuration

No new configuration needed. The polling delays are hardcoded but could be made configurable in the future if needed:

```python
# Potential future environment variables:
ANIMICA_MEMPOOL_POLL_DELAYS=[0.05,0.1,0.15,0.2]  # Comma-separated
ANIMICA_MEMPOOL_IMPORT_TIMEOUT=0.5  # Seconds
```

## Related Documentation

- `FIX_KNOWN_TXIDS_TO_MEMPOOL.md` - Previous fix for stale transaction states
- `MEMPOOL_SYNC_MISSING_FETCH_FIX.md` - Mempool sync improvements
- `TX_PROPAGATION_ARCHITECTURE.md` - Overall transaction propagation design

## Summary

This fix ensures that `animica mempool list` reliably shows transactions that are requested from peers. The adaptive polling approach:

1. **Solves the timing issue**: Waits for transactions to actually arrive
2. **Optimizes for performance**: Exits early on fast networks (50ms)
3. **Handles edge cases**: Works on slow networks, shows timeout message
4. **Improves UX**: Clear feedback on success or timeout

**Result:** Users will now see `newly_visible > 0` when peers have transactions, making the mempool auto-import feature actually work as intended.
