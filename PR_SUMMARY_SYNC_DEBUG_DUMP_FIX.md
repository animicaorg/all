# PR Summary: Fix Stale not_anchored Errors in Sync Debug Dump

## Issue
The `animica debug sync-dump` command was displaying stale `not_anchored` errors even after successful recovery, causing confusion about the actual sync state.

## Root Cause
In `p2p/node/p2p_service.py`, the `_handle_sync_stall()` function would successfully recover by finding a new peer, but only cleared `_sync_block_stalled_reason` without clearing the associated `_sync_last_header_error` and `_sync_last_block_error` fields.

## Solution
Added logic to clear the error states when recovery succeeds and the error is specifically `"not_anchored"`.

## Files Changed

### 1. p2p/node/p2p_service.py (9 lines added)
**Lines 3471-3479**: Added error clearing in `_handle_sync_stall()`

```python
# Clear stale error states on successful recovery to prevent confusing diagnostics
if self._sync_last_header_error == "not_anchored":
    self._sync_last_header_error = None
    self._sync_last_header_error_at = None
    self._sync_last_header_error_peer = None
if self._sync_last_block_error == "not_anchored":
    self._sync_last_block_error = None
    self._sync_last_block_error_at = None
    self._sync_last_block_error_peer = None
```

### 2. p2p/tests/test_sync_status.py (47 lines added)
Added comprehensive unit test: `test_handle_sync_stall_clears_not_anchored_errors_on_recovery`

**Test verifies**:
- Error states are set before recovery
- `_handle_sync_stall()` successfully selects a new peer
- All `not_anchored` error states are cleared
- Recovery action is correctly recorded

### 3. Documentation (2 new files)
- **SYNC_DEBUG_DUMP_FIX.md**: Detailed explanation of the issue and solution
- **SYNC_DEBUG_DUMP_VERIFICATION.md**: Manual verification guide

## Impact

### Before Fix
```
🧪 Sync Debug Dump
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Last header error: not_anchored
Last block error:  not_anchored
Last recovery:    retry_blocks_new_peer (attempt 4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
**Problem**: Confusing - shows both errors and successful recovery

### After Fix
```
🧪 Sync Debug Dump
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Last recovery:    retry_blocks_new_peer (attempt 4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
**Benefit**: Clear - only shows current state, errors are gone after recovery

## Benefits

1. ✅ **Improved UX**: Debug dump now accurately reflects current sync state
2. ✅ **Better Debugging**: Easier to identify actual issues vs. recovered issues
3. ✅ **No Breaking Changes**: Backward compatible, no API or protocol changes
4. ✅ **Minimal Risk**: Surgical 9-line change in error display logic
5. ✅ **Well Tested**: Unit test ensures correct behavior

## Edge Cases Handled

1. ✅ Recovery succeeds → Errors cleared
2. ✅ Recovery fails (no peer) → Errors remain (correct)
3. ✅ Only header error → Only header cleared
4. ✅ Only block error → Only block cleared
5. ✅ Both errors → Both cleared
6. ✅ Non-not_anchored errors → Not cleared (different issue)

## Code Review

- ✅ All feedback addressed
- ✅ Test comment clarified
- ✅ No additional issues

## Testing

### Unit Test
```bash
python -m pytest p2p/tests/test_sync_status.py::test_handle_sync_stall_clears_not_anchored_errors_on_recovery -xvs
```

### Manual Verification
See `SYNC_DEBUG_DUMP_VERIFICATION.md` for step-by-step manual testing guide.

## Related Work

This complements the previous fix in `SYNC_STALL_NOT_ANCHORED_FIX.md` which added fallback logic to prevent permanent stalls. That fix prevented the stall; this fix improves the diagnostic display after recovery.

## Conclusion

This minimal, focused change significantly improves the accuracy of sync diagnostics without touching any sync logic. Users will now see clear, accurate information about their node's current state instead of confusing stale error messages.

**Total Changes**: 265 lines (9 code, 47 test, 209 documentation)
**Risk Level**: Very Low
**Impact**: High (better UX and debugging)
