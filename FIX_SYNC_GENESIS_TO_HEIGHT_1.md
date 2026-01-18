# Sync Genesis to Height 1 Fix - Summary

## Problem Statement
Node stuck at genesis (height 0) with `target_height: 1`, showing:
- `sync_status_reason: 'no_fresh_peer_tips'`
- `last_headers_accepted_count: 0` 
- Sync phase: SYNCING but not progressing

## Root Cause Analysis

### The Bug
In `p2p/node/p2p_service.py` at line 10832-10833, the sync logic had:

```python
if target_height is not None and local_height >= max(0, target_height - 1):
    self._sync_phase = "SYNCED" if local_height > 0 else "IDLE"
    # Returns early without fetching headers
```

### Why It Failed
When `local_height = 0` and `target_height = 1`:
- Condition: `0 >= max(0, 1 - 1)` 
- Simplifies to: `0 >= 0`
- Result: `True` ❌
- Action: Sets phase to IDLE and returns without syncing

The `max(0, target_height - 1)` was trying to allow syncing within 1 block of the target, but this backfired at genesis because `max(0, 0) = 0`, making the node think it was already close enough.

## The Fix

### Changed Condition
```python
# Before:
if target_height is not None and local_height >= max(0, target_height - 1):
    
# After:
if target_height is not None and local_height >= target_height:
```

### Why It Works
With `local_height = 0` and `target_height = 1`:
- New condition: `0 >= 1`
- Result: `False` ✓
- Action: Continues to sync headers and blocks

The node now only considers itself at the target when `local_height >= target_height`, which is the correct and intuitive behavior.

## Testing

### Unit Tests Added
Created `p2p/tests/test_sync_genesis_to_height_1.py` with:

1. **test_sync_progresses_from_genesis_to_height_1**
   - Tests the specific bug scenario (height 0 → target 1)
   - Verifies node continues syncing instead of going IDLE

2. **test_sync_with_higher_target_heights**
   - Tests various combinations:
     - (0, 1) → should continue ✓
     - (0, 10) → should continue ✓
     - (5, 10) → should continue ✓
     - (9, 10) → should continue ✓
     - (10, 10) → should stop ✓
     - (11, 10) → should stop ✓
     - (0, 0) → should stop ✓

Both tests pass successfully.

## Consistency Check

Other target_height checks in the codebase are consistent:

```python
# Line 10653 - Early exit when target reached (correct):
if (
    self._sync_target_height is not None
    and local_height >= self._sync_target_height  # ✓ Correct
    and not force
):
    self._sync_phase = "TARGET_REACHED"
    return result
```

## Impact Assessment

### Affected Scenarios
- **Primary**: Nodes starting from genesis (height 0)
- **Secondary**: Any node exactly 1 block behind target
- **Frequency**: Common during initial sync or after chain resets

### Risk Level: Low
- **Minimal change**: Single comparison operator
- **No side effects**: Only affects the specific edge case
- **Backward compatible**: Improves behavior, doesn't break existing functionality
- **Well-tested**: New tests ensure correctness

## Expected Behavior After Fix

When a node starts at genesis with `target_height = 1`:

1. ✅ Sync phase remains SYNCING (not IDLE)
2. ✅ Node fetches headers from peers
3. ✅ Node accepts valid headers for height 1
4. ✅ Node downloads and imports block at height 1
5. ✅ Sync phase changes to TARGET_REACHED when local_height reaches 1

## Verification Steps

To verify the fix works:

1. Start a fresh node at genesis
2. Connect to peers with blocks at height 1+
3. Check sync status:
   ```bash
   animica node status
   ```
4. Expected to see:
   - `sync_phase: SYNCING` (not IDLE)
   - `last_headers_accepted_count: > 0`
   - `head_height` increasing from 0 to 1

## Review Status

- ✅ Code review: No issues found
- ✅ Security scan: No vulnerabilities detected  
- ✅ Unit tests: 2/2 passing
- ✅ Minimal change principle: Only 1 line changed

## Files Modified

1. `p2p/node/p2p_service.py` (1 line changed)
2. `p2p/tests/test_sync_genesis_to_height_1.py` (new file, 85 lines)
