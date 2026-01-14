# Blockchain Sync Stuck Near Highest Head - Fix Summary

## Problem Statement

Users reported that blockchain syncing was getting stuck a few blocks away from the highest head. The node would remain in a syncing state even though it was very close to the network tip, causing unnecessary delays in transaction submission and other operations.

## Root Cause Analysis

The issue occurred when:

1. **Local node at height N** (e.g., 6495) with `headers == blocks` (both at same height)
2. **All connected peers report height ≤ N** (their advertised heights haven't updated yet)
3. **Sync logic stops requesting headers** because `remote_height <= local_height` (line 8849)
4. **"at_tip" error state is set**, blocking future header requests
5. **Node waits for full stall timeout** (~30 seconds) before attempting recovery
6. **Meanwhile, network progresses** to N+1, N+2, etc., but node is stuck

### Why This Happens

- **Height propagation lag**: Peers may not immediately update their advertised head heights when new blocks arrive
- **Conservative at-tip logic**: The node assumes it's at the tip if all connected peers are at or below its height
- **Long stall timeout**: The 30-second stall timeout is designed for genuinely stuck states, not for quick re-checks

## Solution

### 1. Proactive Multi-Peer Header Requests

**Location**: `p2p/node/p2p_service.py`, lines 8873-8896

When `headers == blocks` and `remote_height <= local_height`, instead of immediately stopping:
- Try up to 3 different peers before concluding we're at the tip
- This catches cases where some peers have new blocks but haven't updated their advertised heights
- Prevents premature "at_tip" conclusion

```python
# When headers == blocks, try other peers to check for new blocks
# This prevents getting stuck when all connected peers haven't updated their heights yet
if (
    best_header_height == local_height
    and len(tried_peers) < min(eligible_count, 3)
    and eligible_count > 1
):
    log.debug(
        "Headers == blocks; trying another peer to check for new blocks",
        extra={...},
    )
    tried_peers.add(peer.remote)
    continue  # Try another peer
```

### 2. Reduced Stall Timeout for Headers==Blocks

**Location**: `p2p/node/p2p_service.py`, line 9453

When `headers == blocks` specifically, use a **reduced timeout** (half of normal):
- Normal stall timeout: ~30 seconds
- Reduced timeout for headers==blocks: ~15 seconds
- Faster detection → faster peer rotation → faster recovery

```python
# Use a reduced timeout (half of stall timeout) to detect this condition faster
reduced_timeout = self._sync_stall_timeout / 2.0
if (
    best_header_height == best_block_height
    and best_block_height > 0
    and not self._sync_inflight_headers
    and not self._sync_inflight_blocks
    and not self._sync_block_queue
    and now - self._sync_last_progress_at > reduced_timeout  # ← Changed from full timeout
    and self._peers
):
```

### 3. Existing "at_tip" Error Clearing

**Location**: `p2p/node/p2p_service.py`, line 8796 (already implemented)

When forced sync is triggered, the "at_tip" error is cleared:
- Allows retrying headers from different peers
- Works in combination with the stall detection above

## Impact

### Before Fix
1. Node at height 6495, network at 6497
2. All connected peers report height 6495 (lag in height propagation)
3. Sync stops immediately: "at_tip" error
4. Waits 30 seconds for stall detection
5. Finally rotates peers and discovers new blocks
6. **Total delay: ~30-40 seconds**

### After Fix
1. Node at height 6495, network at 6497
2. First peer reports height 6495
3. Tries second peer (still 6495)
4. Tries third peer (now reports 6497!) ← **Catches new blocks immediately**
5. Syncs new blocks
6. **Total delay: ~1-2 seconds (normal sync time)**

Alternatively, if all 3 peers still report 6495:
1. Node tries 3 peers
2. Sets "at_tip" error after trying multiple peers
3. Stall detection triggers after **15 seconds** (reduced timeout)
4. Forced sync rotates to new peers, discovers blocks
5. **Total delay: ~15-18 seconds (half of before)**

## Testing

### New Test Suite: `test_sync_headers_blocks_equal_fix.py`

**Test 1**: Headers==blocks tries multiple peers
- ✅ Verifies we try up to 3 peers before giving up

**Test 2**: Reduced stall timeout
- ✅ Verifies headers==blocks uses reduced timeout (15s vs 30s)

**Test 3**: "at_tip" error cleared on force sync
- ✅ Verifies forced sync clears the blocking error state

**Test 4**: Stall triggers forced sync
- ✅ Verifies stall detection triggers aggressive peer rotation

**Test 5**: Integration test - stuck near tip scenario
- ✅ Full scenario: stuck at 6495, network at 6497, recovers via multi-peer + rotation

### Existing Tests Maintained

- ✅ `test_sync_stall_fix.py` - all 4 tests pass
- ✅ `test_sync_skip_stuck_blocks.py` - all 7 tests pass

## Files Modified

1. **`p2p/node/p2p_service.py`**
   - Lines 8873-8896: Multi-peer retry when headers==blocks
   - Line 9453: Reduced timeout for headers==blocks stall detection

2. **`test_sync_headers_blocks_equal_fix.py`** (new)
   - Comprehensive test suite for the fix

## Configuration

No configuration changes required. The fix uses existing:
- `self._sync_stall_timeout` (typically 30 seconds)
- Automatically calculates reduced timeout as `stall_timeout / 2.0`

## Backwards Compatibility

✅ **Fully backwards compatible**
- No breaking changes to sync protocol
- No changes to RPC or API interfaces
- Existing sync behavior maintained for normal cases (headers > blocks)
- Only affects the specific stuck case (headers == blocks)

## Performance Considerations

### Positive Impacts
- **Faster sync recovery**: 50% faster stall detection (15s vs 30s)
- **Fewer wasted cycles**: Catches new blocks immediately by trying multiple peers
- **Better resource utilization**: Less time spent waiting, more time syncing

### Minimal Overhead
- Extra peer tries: Only when headers==blocks (rare case, typically at tip)
- At most 2 additional header requests (3 total instead of 1)
- Each request is async and non-blocking

## Monitoring

To monitor the effectiveness of this fix, watch for:

**Log messages indicating fix is working:**
```
DEBUG: Headers == blocks; trying another peer to check for new blocks
  local_height=6495, best_header_height=6495, tried_peers=1, eligible_peers=3

WARNING: Sync stalled: headers == blocks with no progress
  height=6495, stall_elapsed_s=15.2, peers=3
```

**Metrics to track:**
- Reduction in "headers == blocks" stall events
- Faster sync-to-tip times when joining network
- Fewer long sync pauses near network tip

## Related Issues

This fix addresses the core issue mentioned in:
- Previous sync stall fixes in `SYNC_STALL_FIX_SUMMARY.md`
- Headers==blocks detection logic in `SYNC_STUCK_FIX_SUMMARY.md`
- Fork resolution improvements in `SYNC_FORK_RESOLUTION_FIX.md`

## Conclusion

This fix significantly improves the user experience when syncing near the network tip by:
1. **Proactively checking multiple peers** before concluding we're at tip
2. **Detecting stuck states faster** with reduced timeout for headers==blocks
3. **Maintaining backwards compatibility** and existing sync behavior

The result is a more responsive sync process that gets unstuck 2x faster and often avoids getting stuck altogether by catching new blocks immediately.
