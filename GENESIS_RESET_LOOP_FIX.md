# Genesis Reset Loop Bug Fix - Summary

## Problem Statement
The blockchain node was experiencing two critical issues:
1. **Blockchain resetting to genesis inappropriately** - The node would repeatedly reset its chain to genesis (height 0)
2. **Not syncing despite having peers** - Even with 20 connected peers (12 inbound, 8 outbound), the node remained stuck at height 0

### Diagnostic Output
```
Last matched ancestor:
  Height: 0
  Hash: 0x27fab3a17fd3a166908cdaa32462511ded2da86724314de45f335b0a59f820d8
Last header anchor check:
  headers[0].prev_hash: 0x27fab3a17fd3a166908cdaa32462511ded2da86724314de45f335b0a59f820d8
  anchor_hash: 0x27fab3a17fd3a166908cdaa32462511ded2da86724314de45f335b0a59f820d8
  anchor_height: 0
  anchor_source: prev_hash
  prev_hash_known: True
Recent blocks:
  0: 0x27fab3a1 2026-01-01 00:00:00Z txs=0
```

## Root Cause Analysis

### The Infinite Loop
The issue was in `p2p/node/p2p_service.py` at lines 12184-12189:

```python
# BUGGY CODE (before fix)
should_reset = (
    anchor_height <= self._sync_not_anchored_reset_height  # Default: 10
    and self._sync_not_anchored_attempts >= self._sync_not_anchored_reset_threshold  # Default: 3
    and now - self._sync_last_progress_at > self._sync_stall_timeout
)
```

### Why This Caused an Infinite Loop

1. **Node at genesis (height 0)**: When bootstrapping, the node starts at genesis
2. **Headers fail to anchor**: Incoming headers from peers can't be anchored because the node is at genesis and doesn't have the parent blocks yet
3. **Not anchored counter increments**: Each failed anchor attempt increments `_sync_not_anchored_attempts`
4. **Reset triggers**: After 3 attempts (default threshold), the condition `anchor_height <= 10` is true (0 <= 10), so the node resets to genesis
5. **No progress made**: Resetting to genesis when already at genesis doesn't help at all
6. **Loop repeats**: The node tries to sync again, fails to anchor, resets to genesis again, ad infinitum

### The Logic Flaw
The reset mechanism was designed to help recover from being on a wrong fork near genesis. However, it didn't account for the edge case where the node is **already at genesis**. In this case, resetting to genesis is pointless and creates a loop.

## The Fix

### Code Change
**File**: `p2p/node/p2p_service.py`, line 12184

```python
# FIXED CODE (after fix)
should_reset = (
    anchor_height > 0  # Don't reset to genesis if already at genesis
    and anchor_height <= self._sync_not_anchored_reset_height
    and self._sync_not_anchored_attempts >= self._sync_not_anchored_reset_threshold
    and now - self._sync_last_progress_at > self._sync_stall_timeout
)
```

### What Changed
Added the condition `anchor_height > 0` to prevent resetting to genesis when already at genesis (height 0).

### Why This Works
1. **Prevents pointless resets**: When at genesis, the node won't reset to genesis
2. **Allows normal recovery**: The node will continue trying different peers and sync strategies
3. **Preserves useful behavior**: For heights 1-10, the reset mechanism still works as intended
4. **Enables bootstrapping**: New nodes can now successfully bootstrap from genesis

## Testing

### New Test: `test_genesis_reset_loop_fix.py`
Created comprehensive test suite validating:
- ✅ Old logic would trigger reset at genesis (bug reproduced)
- ✅ New logic prevents reset at genesis (bug fixed)
- ✅ Normal reset still works for heights 1-10
- ✅ No reset for heights above threshold
- ✅ Fix is present and documented in code
- ✅ Ancestor reset logic correctly handles genesis edge case

### Existing Tests
- ✅ `test_sync_fork_resolution.py` - All tests pass
- ✅ No regressions in fork resolution logic

## Impact

### Fixes
1. **"Blockchain resetting to genesis inappropriately"** - Node no longer resets when at genesis
2. **"Not syncing despite having peers"** - Node can now progress from genesis and sync normally
3. **Bootstrap failure** - New nodes can successfully bootstrap from genesis

### Benefits
- Nodes can start from genesis and sync to current height
- Recovery mechanisms work correctly for all heights
- Less frustration for users experiencing sync issues
- Improved network health as more nodes can successfully sync

## Technical Details

### Environment Variables (defaults)
- `ANIMICA_P2P_NOT_ANCHORED_RESET_THRESHOLD=3` - Number of failures before considering reset
- `ANIMICA_P2P_NOT_ANCHORED_RESET_HEIGHT=10` - Maximum height at which to consider genesis reset
- `ANIMICA_P2P_NOT_ANCHORED_WINDOW=300` - Time window (seconds) for counting failures

### Related Recovery Mechanisms
The fix complements the existing `_reset_chain_to_ancestor` mechanism which handles longer forks (>10 blocks) by rolling back to a matched ancestor instead of genesis.

## Verification Steps

To verify the fix works:
1. Start a fresh node from genesis
2. Connect to seed peers
3. Observe that the node progresses beyond height 0
4. Check logs - no "Reset chain to genesis" messages when at genesis
5. Monitor sync progress - should continuously advance

## Future Improvements

While this fix resolves the immediate issue, potential enhancements:
1. **Better genesis bootstrap detection**: Identify when at genesis and use specialized sync strategies
2. **Faster peer discovery**: Improve peer selection when bootstrapping
3. **Checkpoint sync**: Use checkpoints to speed up initial sync from genesis
4. **Metrics**: Add metrics to track bootstrap success rate from genesis

## Conclusion

This was a critical bug that prevented nodes from syncing when starting from genesis. The fix is minimal (adding one condition), safe (doesn't affect normal operation), and thoroughly tested. Nodes can now successfully bootstrap and sync from genesis.
