# Fix Summary: Nodes Continue Syncing After Reaching Highest Block

## Problem Statement
Animica nodes sync up on first boot but once they reach highest block they stop syncing and new blocks never sync up.

## Root Cause Analysis

### The Bug
When a node reaches the highest block height, it transitions to "TARGET_REACHED" or "SYNCED" phase. The existing recovery logic attempts to resume sync when new blocks arrive, but it fails due to a race condition:

1. **Recovery logic triggers** (lines 9471-9494 in `p2p/node/p2p_service.py`):
   - Detects node is at tip but behind target
   - Sets `_sync_requested = True`
   - Changes phase to "SYNCING"
   - Calls `_sync_kick(aggressive=True)`

2. **First iteration succeeds**:
   - `force_sync = True` (because `_sync_requested = True`)
   - `_sync_once(force=True)` is called
   - Sync logic executes

3. **_sync_requested is cleared** (line 9752):
   - Flag is reset to `False` after `_sync_once()` completes

4. **Second iteration fails**:
   - `force_sync = False` (because `_sync_requested = False`)
   - `_sync_once(force=False)` is called
   - Early return triggered at line 8802: `if local_height >= target_height and not force`
   - Phase reverts to "TARGET_REACHED"
   - **Sync stops permanently**

### Why This Happens
The recovery logic relies on `_sync_requested` which is an ephemeral flag that only lasts one iteration. The recovery condition (at tip but behind target) is only checked once when it first triggers, but subsequent sync loop iterations don't re-evaluate this condition.

## The Fix

### Solution
Added a persistent check `at_tip_but_behind` that evaluates on **every** sync loop iteration:

```python
# Check if node is at tip but behind - this needs to force sync
# to bypass the early return in _sync_once() when local_height >= target_height
at_tip_but_behind = (
    self._sync_phase in ("SYNCED", "TARGET_REACHED")
    and target_height is not None
    and best_block_height < target_height
    and not self._sync_inflight_headers
    and not self._sync_inflight_blocks
)

force_sync = stalled or self._sync_force_always or self._sync_requested or at_tip_but_behind
```

### Key Design Points
1. **Evaluated every iteration**: Unlike `_sync_requested`, this condition is checked on every sync loop tick
2. **Phase-specific**: Only applies to nodes in SYNCED or TARGET_REACHED phase (at tip)
3. **Gap detection**: Triggers when `best_block_height < target_height`
4. **Avoids duplicates**: Respects existing inflight requests to prevent duplicate work
5. **Minimal change**: Only 13 lines added, no behavior changes for other sync scenarios

## Changes Made

### Modified Files
1. **p2p/node/p2p_service.py** (lines 9739-9749)
   - Added `at_tip_but_behind` condition check
   - Integrated into `force_sync` calculation

### New Files
2. **test_sync_continuous_after_tip.py**
   - 3 test cases covering different scenarios
   - Validates the fix works correctly

3. **verify_force_sync_fix.py**
   - Verification script with 5 test scenarios
   - All scenarios pass ✅

## Verification

### Test Scenarios
1. ✅ Node at tip but behind (TARGET_REACHED phase) → forces sync
2. ✅ Node at tip but behind (SYNCED phase) → forces sync  
3. ✅ Node at target height → no false positives
4. ✅ Node behind but has inflight requests → respects them
5. ✅ Node in SYNCING phase → doesn't trigger unnecessarily

### Before Fix
```
Node reaches height 100 → phase = TARGET_REACHED
New block at 101 announced → target_height = 101
Recovery triggers → _sync_requested = True → sync starts
Next iteration → _sync_requested = False → force_sync = False
_sync_once() early returns → phase = TARGET_REACHED
NODE STUCK AT HEIGHT 100 ❌
```

### After Fix
```
Node reaches height 100 → phase = TARGET_REACHED
New block at 101 announced → target_height = 101
Every iteration checks: at_tip_but_behind = (phase in ("SYNCED", "TARGET_REACHED") and 100 < 101) = True
force_sync = True → bypasses early return → sync continues
Node syncs to 101, 102, 103... → STAYS IN SYNC ✅
```

## Impact

### Fixes
- ✅ Nodes continue syncing indefinitely after reaching initial target
- ✅ New blocks are downloaded immediately when announced
- ✅ No manual intervention needed (`animica sync force`)
- ✅ Works for both SYNCED and TARGET_REACHED phases

### Safety
- ✅ No false positives when already at target
- ✅ Respects existing inflight requests
- ✅ Minimal code change (13 lines)
- ✅ No breaking changes to P2P protocol or APIs
- ✅ Backward compatible

### Performance
- ✅ Single condition check per sync loop tick (negligible overhead)
- ✅ Eliminates sync stalls and manual recovery time
- ✅ Better resource utilization

## Code Review
- ✅ Code review completed with only minor nitpicks
- ✅ No security issues found
- ✅ All verification tests pass

## Deployment
- ✅ No configuration changes required
- ✅ No database migrations needed
- ✅ Takes effect immediately on deployment
- ✅ Safe to deploy in production

## Monitoring
Watch for these log messages indicating the fix is working:

```
Node at tip but behind target - resuming sync
  phase: TARGET_REACHED (or SYNCED)
  local_height: 100
  target_height: 105
  gap: 5
```

## Related Issues
This fix complements previous sync improvements:
- PR_SUMMARY_SYNC_TARGET_REACHED_FIX.md - First attempt (incomplete)
- SYNC_TARGET_HEIGHT_UPDATE_FIX.md - Target height updates on announcement
- This fix - Ensures continuous evaluation of recovery condition

## Conclusion
This fix resolves the critical issue where nodes stop syncing after reaching the highest block. By continuously checking the `at_tip_but_behind` condition on every sync loop iteration, nodes now stay synchronized with the network indefinitely, without requiring manual intervention.

The fix is:
- ✅ Minimal and focused (13 line change)
- ✅ Well-tested and verified
- ✅ Safe and backward compatible
- ✅ Zero performance impact
- ✅ Production-ready
