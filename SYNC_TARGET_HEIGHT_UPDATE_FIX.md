# Fix: Syncing Stops After Reaching Head Height

## Problem Statement

When a node successfully syncs to head height (e.g., height 100) and then a new block (height 101) is announced, the node would not download the new block. This caused syncing to effectively stop after reaching the initial target height, preventing the node from staying synchronized with the network.

## Root Cause

The issue was in the interaction between block announcements and the block scheduling logic:

1. **Initial state**: Node syncs to height 100
   - `local_height = 100`
   - `_sync_target_height = 100`

2. **Block announcement**: New block at height 101 is announced via P2P
   - `_handle_block_announce()` adds block to queue with `height_hint = 101`
   - Calls `_schedule_block_requests()` to download the block

3. **Target height cap** (BUG): In `_schedule_block_requests()` at lines 8639-8640:
   ```python
   if self._sync_target_height is not None:
       target_height = min(target_height, int(self._sync_target_height))
   ```
   - `target_height` is capped at 100

4. **Block deferral**: At lines 8703-8705:
   ```python
   if height_hint is not None and height_hint > target_height:
       deferred.append((h, height_hint))
       continue
   ```
   - Block check: `height_hint (101) > target_height (100)` → **block is deferred**
   - Deferred block goes back into queue but is never downloaded

5. **Result**: The block remains in the queue indefinitely because `_sync_target_height` is never updated by block announcements, and `_schedule_block_requests()` keeps deferring it.

## Solution

Update `_sync_target_height` in `_handle_block_announce()` when a block announcement arrives with a height higher than the current target.

### Code Changes

**File**: `p2p/node/p2p_service.py`  
**Location**: Lines 6920-6932 (after queueing the announced block)

```python
# Update sync target height if announced block is higher than current target
# This ensures blocks announced after reaching previous target height are not deferred
announced_height = int(announce.height)
if (
    self._sync_target_height is None
    or announced_height > self._sync_target_height
):
    self._sync_target_height = announced_height
    log.debug(
        "Updated sync target height from block announcement",
        extra={
            "new_target": announced_height,
            "block_hash": announce.header_hash.hex(),
        },
    )
```

### Key Design Points

1. **Only increases target**: The fix never decreases `_sync_target_height`, preventing issues with stale announcements
2. **Immediate update**: Target is updated as soon as the block is announced, allowing immediate download
3. **Safe interaction with sync loop**: The sync loop recalculates `_sync_target_height` on each iteration based on network state, so temporary overshoots are harmless
4. **Handles None initialization**: Properly handles the case where `_sync_target_height` is initially `None`

## Impact

### Before Fix
```
1. Node syncs to height 100 (_sync_target_height = 100)
2. New block (height 101) announced
3. Block queued with height_hint = 101
4. _schedule_block_requests() called
5. target_height = min(..., 100) = 100
6. Check: 101 > 100 → block deferred
7. Block never downloaded (infinite loop)
8. Node stuck at height 100
```

### After Fix
```
1. Node syncs to height 100 (_sync_target_height = 100)
2. New block (height 101) announced
3. Block queued with height_hint = 101
4. _sync_target_height updated to 101  ← FIX
5. _schedule_block_requests() called
6. target_height = min(..., 101) = 101
7. Check: 101 > 101 → FALSE, block not deferred
8. Block downloaded and imported
9. Node continues to height 101
```

## Testing

### Unit Tests

Created `test_sync_target_update_on_announce.py` with 5 test cases:

1. **test_sync_target_updates_on_announce**: Verifies target is updated when higher block announced
2. **test_sync_target_not_decreased**: Ensures target not decreased by stale announcements
3. **test_block_not_deferred_after_target_update**: Confirms blocks not deferred after target update
4. **test_continuous_syncing**: Tests syncing across multiple sequential blocks
5. **test_none_target_height_initialization**: Verifies proper None initialization

**Results**: ✓ All 5 tests passing

### Regression Tests

Verified existing sync tests still pass:
- ✓ `test_sync_stall_fix.py` - 5 tests passing
- ✓ No regressions in sync behavior

### Integration Testing

The fix has been tested with:
- Initial sync to head height
- Continuous block reception after reaching head
- Multiple sequential block announcements
- Edge cases (None initialization, stale announcements)

## Backwards Compatibility

✅ **Fully backwards compatible**
- No changes to P2P protocol or message formats
- No changes to RPC or API interfaces
- Only affects internal sync target height tracking
- Existing sync behavior maintained for all normal cases

## Performance Considerations

### Positive Impacts
- **Eliminates sync stalls**: Blocks are no longer deferred after reaching head
- **Continuous syncing**: Node stays synchronized with network in real-time
- **Better resource utilization**: No wasted time waiting for stalled sync recovery

### Minimal Overhead
- Single integer comparison and update per block announcement
- Negligible CPU and memory impact
- No additional network traffic

## Related Issues

This fix complements existing sync improvements:
- **SYNC_HEADERS_BLOCKS_EQUAL_FIX.md**: Handles stalls when headers==blocks during initial sync
- **SYNC_STALL_FIX_SUMMARY.md**: Handles general sync stall detection and recovery
- **NETWORK_HEIGHT_PROPAGATION_FIX.md**: Handles multi-hop height propagation

Together, these fixes ensure robust syncing behavior across all scenarios.

## Monitoring

To monitor this fix in production:

**Debug logs to watch for:**
```
DEBUG: Updated sync target height from block announcement
  new_target=101, block_hash=0x...
```

**Metrics to track:**
- Reduction in sync stalls after reaching head height
- Continuous block import without gaps
- Faster response to new blocks on the network

## Conclusion

This fix resolves a critical bug where nodes would stop syncing after reaching head height. By updating `_sync_target_height` when block announcements arrive, nodes can now continue syncing indefinitely, maintaining real-time synchronization with the network.

The fix is:
- ✅ Simple and focused (single logical change)
- ✅ Well-tested (unit tests + regression tests)
- ✅ Backwards compatible
- ✅ Zero performance impact
- ✅ Complements existing sync infrastructure

