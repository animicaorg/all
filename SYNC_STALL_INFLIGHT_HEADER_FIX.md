# Sync Stall Fix: Inflight Header Expiry Guard Condition

## Problem Statement

Nodes were getting stuck in HEADERS sync phase with 1 inflight header request that never cleared, blocking all sync operations indefinitely. This occurred when:

1. A header request was sent to a peer
2. The peer never responded (or the response was lost)
3. The node reached tip (same height as best peer)
4. No further "progress" was made, so the guard condition prevented expiry checks
5. The inflight header request remained forever, blocking all future header requests

Example from production:
```
Sync phase:       HEADERS
In-flight:        headers=1 blocks=0
Local head:       5270 (0x00015555...)
Best peer head:   5270 (00015555...) from 84.211.73.155:37956
Last recovery:    watchdog_snapshot_recovery (attempt 0)
```

## Root Cause

In `p2p/node/p2p_service.py` at line 3556-3560, the `_enforce_sync_invariants` function had a guard condition:

```python
if (
    self._sync_inflight_headers
    and now - self._sync_last_progress_at > max(1.0, self._sync_request_timeout)
):
    self._expire_inflight_headers()
```

This guard prevented `_expire_inflight_headers()` from being called unless:
- There were inflight headers AND
- No progress had been made for at least `_sync_request_timeout` seconds

### Why This Was Wrong

When a node is at tip (synced with peers):
1. No "progress" is made (head height doesn't advance)
2. `_sync_last_progress_at` stays recent
3. The guard condition evaluates to `False`
4. `_expire_inflight_headers()` is never called
5. Stuck header requests never expire

The guard was unnecessary because `_expire_inflight_headers()` already:
- Checks if there are any inflight requests (returns early if not)
- Uses `time.monotonic()` internally to check request deadlines
- Only expires requests that have actually timed out

## Solution

Removed the guard condition to always check for expired inflight headers when they exist:

```python
# Always check for expired inflight headers, regardless of progress
# The function itself checks deadlines internally
if self._sync_inflight_headers:
    self._expire_inflight_headers()
```

### Why This Is Safe

1. **Performance**: The function returns immediately if there are no inflight requests
2. **Correctness**: Internal deadline checks use `time.monotonic()` which is immune to clock adjustments
3. **Efficiency**: Only processes requests that have actually exceeded their deadline
4. **Consistency**: Matches the pattern used for `_expire_inflight_blocks()` in the same sync loop

## Testing

Added test `test_inflight_header_expiry_at_tip` in `p2p/tests/test_sync_loop_behavior.py` that verifies:
1. A node at tip (recent progress within timeout)
2. With an expired inflight header request (deadline passed)
3. Correctly expires the request when `_enforce_sync_invariants` is called
4. Requeues the request for retry
5. Penalizes the peer appropriately

## Impact

This fix resolves:
- Nodes stuck indefinitely at tip with 1 inflight header
- Sync loops that can't recover via watchdog (since watchdog only helps after 3+ attempts)
- Manual intervention requirements (previously needed `animica sync force`)

The fix is minimal, surgical, and follows existing patterns in the codebase.

## Related Files

- `p2p/node/p2p_service.py` - Main fix
- `p2p/tests/test_sync_loop_behavior.py` - Test coverage
- `SYNC_STALLS.md` - Diagnosis guide (no changes needed)
- `python/animica/cli/debug.py` - Debug command (works as-is)
