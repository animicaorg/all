# Sync Expected Height Bug Fix - Technical Summary

## Problem

When the blockchain node had blocks queued for download but those blocks were not being actively requested from peers, causing synchronization to stall.

**Symptoms:**
- Local head at block N, peer head at block N+119
- 119 blocks queued in `_sync_block_queue`
- 0 blocks in flight (`_sync_inflight_blocks` empty)
- Sync phase showing "BLOCKS" but no progress
- `animica debug sync-dump` showing queued blocks but no requests being made

## Root Cause

In `p2p/node/p2p_service.py`, the `_schedule_block_requests()` method has a bug in its block scheduling logic:

### The Bug

When iterating through queued blocks, the method uses an `expected_height` variable to enforce sequential block downloads. This variable starts at `local_height + 1` and is supposed to increment as blocks are processed.

However, when blocks are **skipped** (because they're already in flight, already imported, or cached), the code was NOT updating `expected_height`. This caused all subsequent blocks to be incorrectly deferred.

### Example Scenario

```
Local height: 11043
Expected height: 11044 (local_height + 1)
Queued blocks: 11044-11162 (119 blocks)
In-flight blocks: 11044-11050 (7 blocks being processed)
```

**What happens WITHOUT the fix:**

1. Loop through queued blocks (sorted by height):
   - Block 11044: In flight → Skip, expected_height stays 11044
   - Block 11045: In flight → Skip, expected_height stays 11044
   - Block 11046: In flight → Skip, expected_height stays 11044
   - ...
   - Block 11050: In flight → Skip, expected_height stays 11044
   - Block 11051: NOT in flight, but 11051 > 11044 → DEFER
   - Block 11052: NOT in flight, but 11052 > 11044 → DEFER
   - All blocks 11051-11162 get deferred

2. Deferred blocks are re-queued
3. Next sync tick: Repeat step 1 → Infinite loop, no progress

## The Fix

Update `expected_height` when skipping blocks that match the current `expected_height`:

```python
# OLD CODE (buggy):
if (self._has_block(h) or h in self._sync_inflight_blocks or h in self._sync_block_buffer):
    self._sync_block_queue_set.discard(h)
    self._sync_block_queue_heights.pop(h, None)
    continue  # BUG: expected_height not updated!

# NEW CODE (fixed):
if (self._has_block(h) or h in self._sync_inflight_blocks or h in self._sync_block_buffer):
    self._sync_block_queue_set.discard(h)
    self._sync_block_queue_heights.pop(h, None)
    # FIX: Update expected_height when skipping blocks at expected height
    if height_hint is not None and height_hint == expected_height:
        expected_height += 1
    continue
```

**What happens WITH the fix:**

1. Loop through queued blocks:
   - Block 11044: In flight, height matches expected (11044), skip and increment → expected_height = 11045
   - Block 11045: In flight, height matches expected (11045), skip and increment → expected_height = 11046
   - ...
   - Block 11050: In flight, height matches expected (11050), skip and increment → expected_height = 11051
   - Block 11051: NOT in flight, height matches expected (11051) → REQUEST, increment → expected_height = 11052
   - Block 11052: NOT in flight, height matches expected (11052) → REQUEST, increment → expected_height = 11053
   - Continue requesting blocks 11051-11162

2. Result: 112 blocks (11051-11162) requested, 0 deferred
3. Sync progresses normally

## Files Changed

- **`p2p/node/p2p_service.py`**: Two locations in `_schedule_block_requests()` method
  - Lines 8654-8660: Fix for cached blocks
  - Lines 8676-8687: Fix for in-flight/existing blocks

## Tests

### Test 1: `test_sync_expected_height_fix.py`
Reproduces the exact bug scenario from the issue:
- ✓ Old version: 0 blocks requested (shows bug)
- ✓ New version: 112 blocks requested (fixed)

### Test 2: `test_sync_logic_verification.py`
Comprehensive verification of 7 different scenarios:
- ✓ Normal sequential sync
- ✓ Blocks in flight (the bug scenario)
- ✓ Some blocks already exist
- ✓ Gap in sequence
- ✓ Mix of inflight and gaps
- ✓ All blocks in flight
- ✓ Out of order queue

All tests pass.

## Impact

This fix resolves a critical sync stall issue that could cause nodes to stop synchronizing even when:
- Peers are connected
- Headers are available
- Blocks are queued for download
- Network connectivity is fine

The fix ensures that block downloads continue making progress even when some blocks are already being processed or have been processed.

## Related Issues

This fix specifically addresses scenarios where:
- Blocks are queued but not being requested
- Sync status shows "BLOCKS" phase with no progress
- `in_flight_blocks = 0` despite having `queued_blocks > 0`
- Last progress timestamp is stale despite peer connectivity

## Deployment

This is a critical bug fix that should be deployed as soon as possible to prevent nodes from getting stuck during synchronization.

No configuration changes or migration steps are required.
