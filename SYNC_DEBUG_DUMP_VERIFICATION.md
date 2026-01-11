# Verification Guide: Sync Debug Dump Fix

## What Changed

Fixed the `animica debug sync-dump` command to show accurate sync state by clearing stale `not_anchored` errors after successful recovery.

## Before the Fix

Running `animica debug sync-dump` would show:
```
Last header error: not_anchored
Last block error:  not_anchored
Last recovery:    retry_blocks_new_peer (attempt 4)
```

**Problem**: The errors persist even though recovery succeeded, causing confusion.

## After the Fix

Now when recovery succeeds, stale errors are cleared:
```
Last recovery:    retry_blocks_new_peer (attempt 4)
```

**Benefit**: Debug dump shows accurate current state.

## How to Verify

### Scenario 1: Normal Operation (Expected)

1. Run a node that experiences a temporary `not_anchored` stall
2. Wait for automatic recovery (should happen within 30-60 seconds)
3. Run `animica debug sync-dump`
4. ✅ **Expected**: No `not_anchored` errors shown if recovery succeeded
5. ✅ **Expected**: `Last recovery: retry_blocks_new_peer` shown

### Scenario 2: Persistent Issues (Also Expected)

1. If ALL peers are unable to anchor (very rare)
2. Run `animica debug sync-dump`
3. ✅ **Expected**: Errors ARE shown because recovery hasn't succeeded
4. ✅ **Expected**: `Last recovery: stall_no_peer` or similar

### Scenario 3: New Error After Recovery

1. Node recovers from `not_anchored`
2. A new, different error occurs (e.g., `missing_parent`)
3. Run `animica debug sync-dump`
4. ✅ **Expected**: New error shown (not the old `not_anchored`)
5. ✅ **Expected**: Accurate reflection of current state

## Manual Test Commands

```bash
# Start a node
animica node start

# If you encounter a stall, check the dump
animica debug sync-dump

# Wait for automatic recovery (30-60 seconds)
sleep 60

# Check again - stale errors should be cleared if recovery succeeded
animica debug sync-dump

# Check sync status to verify node is progressing
animica sync status
```

## What to Look For

### Good Signs (Fix Working)
- ✅ After recovery, no stale `not_anchored` errors
- ✅ Debug dump accurately reflects current sync state
- ✅ `Last recovery` action shown clearly

### Bad Signs (Issue Persists)
- ❌ `not_anchored` errors shown alongside `retry_blocks_new_peer`
- ❌ Error timestamps are very old but still displayed
- ❌ Confusing mix of success and error indicators

## Testing the Test

The unit test can be run with:
```bash
python -m pytest p2p/tests/test_sync_status.py::test_handle_sync_stall_clears_not_anchored_errors_on_recovery -xvs
```

**Note**: Requires full dev environment with all dependencies installed.

## Edge Cases Handled

1. ✅ **Recovery succeeds**: Errors cleared
2. ✅ **Recovery fails (no peer)**: Errors remain (correct behavior)
3. ✅ **Only header error**: Only header error cleared
4. ✅ **Only block error**: Only block error cleared
5. ✅ **Both errors**: Both cleared
6. ✅ **Non-not_anchored errors**: Not cleared (correct - different issue)

## Code Changes Summary

**File**: `p2p/node/p2p_service.py`
**Function**: `_handle_sync_stall()`
**Lines**: 3471-3479

Added logic to clear `_sync_last_header_error` and `_sync_last_block_error` when:
1. Recovery succeeds (new peer found)
2. Error is specifically `"not_anchored"`

**Impact**: Minimal, surgical change to improve diagnostic accuracy.
