# Sync Fix Summary: Nodes Not Syncing When in SYNCED Phase

## Problem Statement
Nodes were showing `SYNCED` phase but were actually behind peers. For example:
- Local head: 11242
- Best peer head: 11258 (16 blocks behind)
- Sync phase: SYNCED
- Sync was not automatically triggering to catch up

## Root Cause Analysis

The issue had two related causes:

### 1. Premature SYNCED Phase
In `_sync_once()` (around line 8928), the node would mark itself as SYNCED when:
- `local_height >= target_height - 1`
- `target_height` was determined from `self._sync_target_height` or `remote_height`

The problem: If `remote_height` came from a stale peer hello message, the node would incorrectly think it was at the tip, even though higher blocks were available on the network.

### 2. No Detection in Sync Loop
Once marked as SYNCED, the sync loop didn't have logic to detect when the node was actually behind. The existing checks at line 9542-9549 only triggered when:
- `best_block_height < network_best_height`
- AND no inflight work
- AND stalled for > timeout

But if the node was "cleanly" in SYNCED phase (no errors, no stalls), it would stay stuck even when behind.

## Solution Implemented

### Fix 1: Check network_best_height in _sync_once()
Enhanced target height calculation (lines 8925-8936) to check `network_best_height()` in addition to `remote_height`:

```python
# Ensure target_height is an int if set
if target_height is not None:
    target_height = int(target_height)
# Also check network_best_height to avoid premature SYNCED phase
network_best = self._network_best_height()
if network_best is not None:
    network_best = int(network_best)
    if target_height is None:
        target_height = network_best
    else:
        target_height = max(target_height, network_best)
```

This prevents premature SYNCED when the direct peer's height is stale but the network has higher blocks.

### Fix 2: Detect SYNCED-but-behind in Sync Loop
Added explicit check in sync loop (lines 9445-9467) to detect when node is in SYNCED phase but behind target:

```python
if (
    self._sync_phase == "SYNCED"
    and target_height is not None
    and best_block_height < target_height
    and not self._sync_inflight_headers
    and not self._sync_inflight_blocks
):
    gap = target_height - best_block_height
    log.info("Node in SYNCED phase but behind target - resuming sync", ...)
    # Change phase to trigger sync resumption
    self._sync_phase = "SYNCING"
    self._sync_kick(reason="synced_but_behind", aggressive=True)
```

This ensures that even if the node was incorrectly marked SYNCED, it will detect the gap and resume syncing.

## Testing

### Unit Test
Created `test_sync_synced_but_behind.py` with two scenarios:
1. Node at height 5, peer at height 10 → should resume sync
2. Node at height 5, peer at height 5 → should stay SYNCED

### Verification Scripts
1. `verify_sync_fix.py` - Tests the primary fix with various scenarios
2. `verify_network_best_fix.py` - Tests the secondary fix

All tests pass successfully.

## Impact

### Before Fix
- Node shows SYNCED at height 11242
- Peers at height 11258
- Node stays stuck, never catches up
- Requires manual intervention (`animica sync force`)

### After Fix
- Node detects gap (11242 < 11258)
- Changes phase from SYNCED to SYNCING
- Automatically resumes header and block sync
- Catches up without manual intervention

## Files Changed
- `p2p/node/p2p_service.py`: 40 lines added/changed
- `test_sync_synced_but_behind.py`: 245 lines (new test)
- `verify_sync_fix.py`: 163 lines (verification script)
- `verify_network_best_fix.py`: 148 lines (verification script)

Total: ~596 lines added across 4 files

## Deployment Notes
- No configuration changes required
- No database migrations needed
- Fix is backward compatible
- Will automatically apply on next sync loop iteration
- Log message "Node in SYNCED phase but behind target - resuming sync" will appear when fix triggers
