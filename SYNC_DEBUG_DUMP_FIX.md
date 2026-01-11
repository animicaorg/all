# Sync Debug Dump Fix: Clear Stale not_anchored Errors

## Problem

When running `animica debug sync-dump`, users would see persistent `not_anchored` errors even after successful recovery:

```
Last header error: not_anchored
Last block error:  not_anchored
Last recovery:    retry_blocks_new_peer (attempt 4)
```

This was confusing because the recovery action (`retry_blocks_new_peer`) indicates success, but the errors remained displayed.

## Root Cause

In `p2p/node/p2p_service.py`, the `_handle_sync_stall()` method would:

1. Successfully find a new peer during stall recovery
2. Set `_sync_last_recovery_action = "retry_blocks_new_peer"`
3. Clear `_sync_block_stalled_reason = None`
4. **BUT NOT clear `_sync_last_header_error` or `_sync_last_block_error`**

This left stale error messages that were displayed by the debug dump, creating confusion about the actual sync state.

## Solution

Modified `_handle_sync_stall()` to clear the error states when recovery succeeds:

```python
if new_peer:
    self._sync_active_block_peer = new_peer.remote
    self._sync_last_recovery_action = "retry_blocks_new_peer"
    self._sync_block_stalled_reason = None
    # Clear stale error states on successful recovery to prevent confusing diagnostics
    if self._sync_last_header_error == "not_anchored":
        self._sync_last_header_error = None
        self._sync_last_header_error_at = None
        self._sync_last_header_error_peer = None
    if self._sync_last_block_error == "not_anchored":
        self._sync_last_block_error = None
        self._sync_last_block_error_at = None
        self._sync_last_block_error_peer = None
    self._stats["stall_recoveries"] += 1
    self._sync_wakeup.set()
```

## Expected Behavior

### Before Fix
```
Last header error: not_anchored
Last block error:  not_anchored  
Last recovery:    retry_blocks_new_peer (attempt 4)
```
*Confusing: shows errors but also successful recovery*

### After Fix
```
Last recovery:    retry_blocks_new_peer (attempt 4)
```
*Clear: errors are cleared, only recovery status shown*

Or if new errors occur:
```
Last header error: <new_error>
Last recovery:    retry_blocks_new_peer (attempt 5)
```
*Accurate: shows current errors, not stale ones*

## Files Changed

1. **p2p/node/p2p_service.py** (lines 3471-3479)
   - Added logic to clear `not_anchored` errors on successful recovery

2. **p2p/tests/test_sync_status.py**
   - Added test: `test_handle_sync_stall_clears_not_anchored_errors_on_recovery`

## Testing

The test verifies:
1. Error states are set before recovery
2. `_handle_sync_stall()` is called
3. A new peer is successfully selected
4. All error states are cleared
5. Recovery action is set correctly

## Impact

- **User Experience**: Debug dump now shows accurate current state instead of stale errors
- **Debugging**: Easier to diagnose actual sync issues vs. recovered issues
- **Backward Compatible**: No API or protocol changes
- **Minimal Risk**: Only affects diagnostic display, not sync logic

## Related

- Previous fix: `SYNC_STALL_NOT_ANCHORED_FIX.md` - Added fallback logic to prevent permanent stalls
- This fix: Improves diagnostic accuracy after recovery from stalls
