# Sync Completion Reproduction & Verification

## Repro pattern (pre-fix)
1. Start leader/follower and allow follower to sync partially.
2. Observe follower head advancing (e.g. 1 → 2 → 3).
3. Observe stale/contradictory sync fields:
   - stale `target_height`
   - `next_block_needed_height=None` while behind
   - stale completion phase vs best known network height

## Verification checks (post-fix)
- `sync_status_snapshot.target_height` tracks computed best target (including network best).
- `_sync_once()` does not stop at `TARGET_REACHED` when network best is still ahead.
- `next_block_needed_height` reports `head+1` during catch-up even if queue is transiently empty.
- `chain head` prints `Timestamp: 0` when timestamp is zero.

## New diagnostics to inspect
- `SYNC_TARGET_SET`
- `SYNC_TARGET_CLEARED`
- `HEADER_ACCEPTED`
- `HEADER_DISCARDED`
- `BLOCK_SCHEDULED`
- `BLOCK_IMPORTED`
- `SYNC_CONVERGED`
- `STATUS_CACHE_REFRESH`
- `STATUS_STALE_FIELD_DETECTED`
