# PR Summary: Fix Sync Stall for Nodes in TARGET_REACHED Phase

## Issue
Nodes that reach the highest block height transition to "TARGET_REACHED" phase and stop syncing. When new blocks arrive on the network, these nodes don't automatically resume syncing, causing them to fall behind and requiring manual intervention.

**Problem Statement:**
> Syncing stalls and stops when reaching highest block height please fix it so it stays in step with the highest block

## Root Cause
The existing recovery logic at line 9449 in `p2p/node/p2p_service.py` only checked for nodes in "SYNCED" phase:

```python
if (
    self._sync_phase == "SYNCED"  # ❌ Only checks SYNCED, misses TARGET_REACHED
    and target_height is not None
    and best_block_height < target_height
    ...
):
```

However, when a node reaches the target height, `_sync_once()` sets the phase to "TARGET_REACHED" (line 8781):

```python
if (
    self._sync_target_height is not None
    and local_height >= self._sync_target_height
    and not force
):
    self._sync_phase = "TARGET_REACHED"
    return result  # Early exit - no sync activity
```

This created a gap where nodes in "TARGET_REACHED" phase would not resume syncing even when new blocks were announced.

## Solution
Extended the recovery logic to handle both "SYNCED" and "TARGET_REACHED" phases:

**Before:**
```python
if (
    self._sync_phase == "SYNCED"
    and target_height is not None
    and best_block_height < target_height
    and not self._sync_inflight_headers
    and not self._sync_inflight_blocks
):
```

**After:**
```python
if (
    self._sync_phase in ("SYNCED", "TARGET_REACHED")  # ✅ Now handles both phases
    and target_height is not None
    and best_block_height < target_height
    and not self._sync_inflight_headers
    and not self._sync_inflight_blocks
):
```

## Changes

### 1. Core Fix (p2p/node/p2p_service.py)
- **Line 9449**: Changed condition from `== "SYNCED"` to `in ("SYNCED", "TARGET_REACHED")`
- **Line 9457**: Updated log message to show which phase triggered resumption
- **Line 9459**: Added `"phase": self._sync_phase` to log extras
- **Line 9468**: Updated reason from `"synced_but_behind"` to `"at_tip_but_behind"` for clarity

### 2. Test Updates (test_sync_synced_but_behind.py)
- Updated existing tests to use new condition
- Added new test case: `test_sync_resumes_when_target_reached_but_behind()`
- Tests verify both SYNCED and TARGET_REACHED phases resume correctly

### 3. Verification Script (verify_sync_target_reached_fix.py)
- Demonstrates that both phases now resume when behind
- Shows that old condition would miss TARGET_REACHED phase
- 5 test scenarios all pass

## How It Works

### Flow When New Blocks Arrive

1. **Node at tip**: Node reaches highest block → phase set to "TARGET_REACHED" or "SYNCED"
2. **New block announced**: Peer announces new block → `_sync_target_height` updated (line 6928)
3. **Sync loop wakes**: `_sync_wakeup.set()` called (line 6946) → sync loop immediately checks
4. **Gap detected**: Fix at line 9449 detects `best_block_height < target_height`
5. **Resume sync**: Phase changes to "SYNCING" → `_sync_kick(aggressive=True)` → sync resumes

### Key Components
- **Block announcement handler** (line 6920-6948): Updates target height and wakes sync loop
- **Sync loop** (line 9297-9639): Checks recovery condition every tick or when woken
- **Recovery logic** (line 9448-9468): Detects gap and forces sync resumption

## Impact

### Before Fix
```
Scenario: Node reaches highest block (height 100)
1. Phase set to TARGET_REACHED
2. New block arrives (height 101) → target_height = 101
3. Sync loop checks condition at line 9449
4. Condition: self._sync_phase == "SYNCED" → FALSE ❌
5. Node stays in TARGET_REACHED, never resumes sync
6. Falls behind as more blocks arrive
7. Manual intervention required: animica sync force
```

### After Fix
```
Scenario: Node reaches highest block (height 100)
1. Phase set to TARGET_REACHED
2. New block arrives (height 101) → target_height = 101
3. Sync loop wakes up immediately
4. Condition: self._sync_phase in ("SYNCED", "TARGET_REACHED") → TRUE ✅
5. Phase changed to SYNCING → sync resumes automatically
6. Node stays in sync with network
7. No manual intervention needed
```

## Testing

### Automated Tests
✅ **test_sync_resumes_when_synced_but_behind** - SYNCED phase resumes when behind  
✅ **test_sync_resumes_when_target_reached_but_behind** - TARGET_REACHED phase resumes when behind (NEW)  
✅ **test_sync_does_not_resume_when_synced_and_at_target** - No false positives at target  

### Verification Script
```bash
$ python3 verify_sync_target_reached_fix.py
✓ Test 1 PASSED: SYNCED phase resumes when behind target
✓ Test 2 PASSED: TARGET_REACHED phase resumes when behind target
✓ Test 3 PASSED: Node stays SYNCED when at target
✓ Test 4 PASSED: Node doesn't duplicate work when inflight requests exist
✓ Test 5 PASSED: New condition catches TARGET_REACHED, old condition missed it
✓ All tests PASSED
```

## Risk Assessment

**Risk Level**: Low

**Rationale**:
- Minimal code change (5 lines modified)
- Only affects nodes at tip (SYNCED or TARGET_REACHED phase)
- Preserves existing safety guards (no inflight requests check)
- Well-tested with multiple scenarios
- Backward compatible - no API changes

**Safety Guards**:
- Only triggers when `target_height` is known and higher
- Only triggers when no sync work already in flight
- Aggressive sync kick ensures quick recovery
- Respects sync lock and existing sync state

## Verification Steps

### For Reviewers
1. Review code diff: Only 5 lines changed in core logic
2. Run verification script: `python3 verify_sync_target_reached_fix.py`
3. Check test coverage: 3 test cases covering SYNCED, TARGET_REACHED, and edge cases
4. Review logs: Clear diagnostic messages show which phase triggered resumption

### For Deployment
1. Deploy to test node experiencing the issue
2. Monitor logs for:
   ```
   Node at tip but behind target - resuming sync
   phase: TARGET_REACHED (or SYNCED)
   gap: N
   ```
3. Verify sync resumes within 1-2 seconds of new block announcement
4. Confirm node stays in sync as new blocks arrive
5. Monitor for 24-48 hours to ensure no regressions

## Success Criteria
✅ Nodes in TARGET_REACHED phase automatically resume sync when new blocks arrive  
✅ Nodes in SYNCED phase continue to resume as before  
✅ No false positives when already at target  
✅ No duplicate work when sync already in progress  
✅ Clean logs with clear diagnostic messages  

## Related Issues
- **Original issue**: "Syncing stalls and stops when reaching highest block height"
- **Previous fix**: PR_SUMMARY_SYNC_SYNCED_BUT_BEHIND.md (only handled SYNCED phase)
- **This fix**: Extends previous fix to also handle TARGET_REACHED phase

## Deployment
- ✅ No configuration changes required
- ✅ No database migrations required
- ✅ Backward compatible
- ✅ Takes effect immediately on next sync loop tick
- ✅ Safe to deploy in production

## Conclusion
This fix ensures that nodes continuously stay in sync with the network by catching the TARGET_REACHED phase case that was previously missed. The change is minimal, well-tested, and low-risk.
