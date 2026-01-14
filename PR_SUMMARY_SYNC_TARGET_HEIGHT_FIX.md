# PR Summary: Fix Syncing Stops After Reaching Head Height

## Issue
Nodes successfully sync to head height but then stop processing new blocks announced via P2P, causing them to fall behind the network.

## Root Cause
When a new block is announced after the node reaches `_sync_target_height`, the block is queued but then deferred in `_schedule_block_requests()` because its height exceeds the stale target height. The target was never updated by block announcements, causing an infinite deferral loop.

## Solution
Update `_sync_target_height` in `_handle_block_announce()` when an announced block has a height higher than the current target. This is a minimal 17-line change that allows continuous syncing.

## Changes
- **Modified**: `p2p/node/p2p_service.py` (17 lines added at line 6920)
- **Added**: `test_sync_target_update_on_announce.py` (5 unit tests)
- **Added**: `SYNC_TARGET_HEIGHT_UPDATE_FIX.md` (comprehensive documentation)

## Testing
✓ All 5 new unit tests passing  
✓ All existing sync tests passing (no regressions)  
✓ Python syntax validation passed  

## Impact
- **Before**: Node stuck at height 100 when block 101 announced
- **After**: Node continuously syncs to 101, 102, 103, etc.

## Safety
- Only increases target (never decreases)
- Works correctly with sync loop's target recalculation
- No protocol or API changes
- Fully backwards compatible
- Zero performance impact

## Review Focus
1. Logic correctness in `_handle_block_announce()` (lines 6920-6932)
2. Unit test coverage in `test_sync_target_update_on_announce.py`
3. Integration with existing sync loop target update (line 9424)

## Related Fixes
This complements:
- SYNC_HEADERS_BLOCKS_EQUAL_FIX.md
- SYNC_STALL_FIX_SUMMARY.md
- NETWORK_HEIGHT_PROPAGATION_FIX.md
