# Sync Stall Fix - Implementation Summary

## Problem Statement

Nodes experiencing sync stalls where they get stuck alternating between `SYNCING_HEADERS` and `SYNCING_BLOCKS` states without making progress.

### Observed Symptoms
```
Height:    6495
Status:    SYNCING_BLOCKS
Headers:   6906 | Blocks: 6495

(After force sync command)

Height:    6495  
Status:    SYNCING_HEADERS
Headers:   6495 | Blocks: 6495

(No progress, loops forever)
```

### Root Cause Analysis

The stall occurs when:
1. Local node is at height 6495
2. Network actually has blocks up to height 6906
3. But all connected peers only report height 6495
4. Node marks headers as "at_tip" because connected peers match local height
5. Block queue can't be seeded because `best_header_height <= local_height`
6. No peer rotation happens because stall handler requires `_sync_best_header` to exist
7. Node gets stuck in infinite loop

## Solution

### 1. Clear "at_tip" Error on Force Sync

**File:** `p2p/node/p2p_service.py`  
**Lines:** 7706-7717

```python
# When forcing sync, clear "at_tip" error to allow re-requesting headers
if force and self._sync_last_header_error == "at_tip":
    self._sync_last_header_error = None
    self._sync_last_header_error_at = None
    self._sync_last_header_error_peer = None
    log.info("Cleared 'at_tip' error state due to forced sync")
```

**Impact:**
- Allows headers to be re-requested even if previously marked as "at_tip"
- Enables retry with different peers
- User can trigger with `animica sync force`

### 2. Detect Headers == Blocks Stall

**File:** `p2p/node/p2p_service.py`  
**Lines:** 8085-8108

```python
# Detect when headers == blocks and we're not making progress
if (
    best_header_height == best_block_height
    and best_block_height > 0
    and not self._sync_inflight_headers
    and not self._sync_inflight_blocks
    and not self._sync_block_queue
    and now - self._sync_last_progress_at > self._sync_stall_timeout
    and self._peers
):
    log.warning("Sync stalled: headers == blocks with no progress")
    self._sync_block_stalled_reason = "headers_blocks_equal_stall"
    self._sync_requested = True
```

**Impact:**
- Automatically detects when stuck in headers == blocks state
- Triggers stall handling and peer rotation
- Forces sync on next iteration to try different peers

### 3. Allow Stall Handling in Edge Cases

**File:** `p2p/node/p2p_service.py`  
**Lines:** 3333-3337, 3379

```python
def _handle_sync_stall(self, *, reason: str) -> None:
    now = time.time()
    # Allow stall handling even when _sync_best_header is None or equals local height
    # (Removed early return when _sync_best_header is None)
    
    # Later in the function:
    best_header_height = self._sync_best_header.height if self._sync_best_header else local_height
```

**Impact:**
- Enables stall handler to work when headers == blocks
- Allows peer rotation in edge cases
- Ensures recovery even when `_sync_best_header` is None

## Recovery Flow

```
1. Node stuck at height 6495
   ↓
2. User runs "animica sync force"
   ↓
3. Force clears "at_tip" error
   ↓
4. Headers requested from current peer (may still return empty)
   ↓
5. Still stuck: headers == blocks stall detection triggers
   ↓
6. Stall handler rotates to different peer
   ↓
7. Headers requested from new peer
   ↓
8. New peer has higher height (6906)
   ↓
9. Headers downloaded successfully
   ↓
10. Block queue seeded from new headers
    ↓
11. Blocks downloaded and applied
    ↓
12. Progress resumes!
```

## Testing

### Unit Tests
Created `test_sync_stall_fix.py` with 4 test cases:
- ✓ "at_tip" error clearing on force
- ✓ headers == blocks stall detection  
- ✓ Stall handler with None _sync_best_header
- ✓ Normal sync not affected

### Manual Testing
See `SYNC_STALL_FIX_TESTING_GUIDE.md` for detailed testing procedures.

## Files Changed

1. `p2p/node/p2p_service.py` - Main sync logic fix
2. `test_sync_stall_fix.py` - Unit tests for the fix
3. `SYNC_STALL_FIX_TESTING_GUIDE.md` - Manual testing guide
4. `SYNC_STALL_FIX_SUMMARY.md` - This file

## Backward Compatibility

✓ No breaking changes  
✓ Existing sync behavior preserved  
✓ Only adds recovery paths for stuck states  
✓ No RPC or protocol changes  

## Performance Impact

- Minimal: Only adds checks when sync is already stalled
- No impact on normal sync operation
- Adds one log warning when stall is detected
- Peer rotation was already part of stall handling

## Future Improvements

Potential enhancements (not in scope for this fix):
1. More aggressive peer discovery when all peers are at same height
2. DHT or DNS-based discovery of higher-height peers
3. Metrics/monitoring for sync stall frequency
4. Automatic snapshot sync when far behind

## Verification Checklist

- [x] Code changes are minimal and targeted
- [x] Logic tests pass
- [x] Syntax check passes
- [x] Code review completed
- [x] Documentation created
- [x] No breaking changes
- [x] Backward compatible

## How to Use

### For Users
When sync appears stalled:
```bash
# Check current status
animica sync status

# Force sync to clear errors and retry
animica sync force

# Monitor logs for recovery
tail -f ~/.animica/logs/node.log | grep -i sync
```

### For Developers
The fix automatically activates when:
- Force sync is triggered (clears errors)
- Headers == blocks with no progress for > 5 seconds (auto-detects stall)
- Stall handling rotates peers and retries

No additional configuration or changes needed.

## References

- Original Issue: Sync stalled with headers == blocks
- PR: Fix sync stall when headers equals blocks
- Related: P2P peer rotation, sync stall detection
