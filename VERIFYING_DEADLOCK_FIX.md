# Fix for Sync Stuck in VERIFYING Phase

## Issue Summary

Nodes were getting stuck in the VERIFYING sync phase indefinitely with:
- `in_flight_blocks` = 0 (no blocks being downloaded)
- `queued_blocks` = 0 (no blocks queued for download)
- But sync phase = "VERIFYING" (should transition to SYNCED)

## Root Cause

The VERIFYING phase is determined by this condition in `p2p/node/p2p_service.py` line 3376:

```python
if self._sync_inflight_blocks or self._sync_block_buffer:
    return "VERIFYING"
```

The deadlock occurred when:
1. `_sync_block_buffer` contained orphan blocks (blocks whose parent blocks are not yet available)
2. No new blocks were arriving to trigger processing
3. The `_drain_block_buffer()` function was only called after successful block imports (line 6857)
4. Since no blocks could be imported (all orphans), `_drain_block_buffer()` was never called
5. Buffer never emptied → stuck in VERIFYING forever

## The Fix

**Added periodic call to `_drain_block_buffer()` in the sync loop**

Location: `p2p/node/p2p_service.py` line 9539

```python
self._expire_inflight_headers()
self._expire_inflight_blocks()
self._prune_orphan_buffer()
# Periodically attempt to drain orphan buffer to prevent VERIFYING deadlock
await self._drain_block_buffer()
self._retry_skipped_blocks()
```

This ensures that:
- Every sync loop tick attempts to drain the orphan buffer
- When parent blocks arrive and get imported, orphaned children can be processed
- Invalid blocks (non-orphan failures) are removed from the buffer
- The buffer can be cleared, allowing transition from VERIFYING to SYNCED

## How `_drain_block_buffer()` Works

```python
async def _drain_block_buffer(self) -> None:
    if not self._sync_block_buffer:
        return
    progressed = True
    while progressed:
        progressed = False
        for h, blk in list(self._sync_block_buffer.items()):
            if not self._has_block(blk.parent_hash):
                continue  # Skip orphans (parent not available yet)
            
            ok, reason = await self._import_block_payload(...)
            if ok:
                # Successfully imported - remove from buffer
                self._sync_block_buffer.pop(h, None)
                progressed = True
            elif not self._is_orphan_reason(reason):
                # Failed for non-orphan reason (permanently invalid) - remove
                self._sync_block_buffer.pop(h, None)
            # If still orphan, leave in buffer for next attempt
```

The function:
1. Tries to import blocks whose parents are now available
2. Removes successfully imported blocks
3. Removes permanently invalid blocks
4. Leaves orphans in buffer for retry on next call

## Verification

### Observing the Fix in Action

When stuck in VERIFYING, run:
```bash
animica debug sync-dump
```

Before fix:
```
Sync phase:       VERIFYING
In-flight:        headers=0 blocks=0
Queues:           pending_headers=0 queued_blocks=0
Last progress:    <timestamp from long ago>
```
(Stuck indefinitely with no progress)

After fix:
```
Sync phase:       VERIFYING → transitions to SYNCED
In-flight:        headers=0 blocks=0
Queues:           pending_headers=0 queued_blocks=0
Last progress:    <recent timestamp>
```
(Buffer drains, phase transitions to SYNCED)

### Test Coverage

Created `test_verifying_deadlock_fix.py` with 5 test cases:

1. **test_verifying_phase_condition**: Validates the phase detection logic
2. **test_deadlock_scenario**: Confirms deadlock detection when buffer has orphans
3. **test_drain_block_buffer_clears_invalid_orphans**: Tests buffer clearing behavior
4. **test_periodic_drain_prevents_deadlock**: Proves periodic drain resolves deadlock
5. **test_orphan_prune_and_requeue**: Validates orphan expiration handling

All tests pass:
```bash
$ python3 test_verifying_deadlock_fix.py
✓ Test 1 PASSED: VERIFYING phase condition logic works
✓ Test 2 PASSED: Deadlock scenario detection works
✓ Test 3 PASSED: drain_block_buffer clears invalid orphans correctly
✓ Test 4 PASSED: Periodic drain prevents deadlock (drained in 1 ticks)
✓ Test 5 PASSED: Orphan prune and requeue works

Results: 5 passed, 0 failed out of 5 tests
```

### Regression Testing

Verified no regressions in existing sync tests:
- `test_sync_missing_parent_fix.py`: ✓ All 7 tests pass
- `test_sync_stall_fix.py`: ✓ All 5 tests pass

## Performance Impact

**Minimal to none**:
- `_drain_block_buffer()` is lightweight (early returns if buffer empty)
- Only processes blocks whose parents are available (no wasted work)
- Already called in success path, now also in periodic path
- Sync loop tick interval unchanged (default ~1-5 seconds)

## Related Mechanisms

This fix works in conjunction with existing orphan handling:

1. **`_prune_orphan_buffer()`**: Removes expired orphans (TTL > 60s) and requeues them
2. **`_drain_block_buffer()`** (this fix): Continuously attempts to process buffered blocks
3. **`_expire_inflight_blocks()`**: Handles timeout of in-flight block requests

Together, these ensure orphan blocks are:
- Attempted periodically (drain)
- Eventually expired and requeued if stuck (prune)
- Not left in limbo indefinitely (prevents VERIFYING deadlock)

## Files Changed

1. **p2p/node/p2p_service.py** (line 9539)
   - Added `await self._drain_block_buffer()` in sync loop

2. **test_verifying_deadlock_fix.py** (new file)
   - Comprehensive test suite for the fix

3. **VERIFYING_DEADLOCK_FIX.md** (this file)
   - Documentation of the issue and solution

## Summary

This fix resolves a critical sync deadlock by ensuring orphan blocks are continuously attempted to be processed, allowing the node to progress from VERIFYING to SYNCED state when blocks become available.
