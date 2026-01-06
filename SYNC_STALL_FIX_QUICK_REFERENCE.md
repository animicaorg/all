# Sync Stall Fix - Quick Reference

## Problem
Animica nodes were getting stuck at random heights during blockchain synchronization and not reaching the network's highest block.

## Root Cause
Two issues in `p2p/node/p2p_service.py`:

1. **Inflight Block Gating (line 7316):** Sync stopped requesting blocks when any were already in-flight
2. **Premature Completion (line 7003):** Sync marked as "SYNCED" even with pending blocks in the queue

## Solution
**Two minimal code changes:**

### Change 1: Remove inflight gating condition
```python
# Before
if network_best_height > local_height and not self._sync_inflight_blocks:
    await self._schedule_block_requests()

# After  
if network_best_height > local_height:  # Removed: and not self._sync_inflight_blocks
    await self._schedule_block_requests()
```

### Change 2: Check pending blocks before completion
```python
# Before
if self._sync_best_header.height <= local_height:
    self._sync_phase = "SYNCED"
    return result

# After
if (self._sync_best_header.height <= local_height 
    and not self._sync_block_queue 
    and not self._sync_inflight_blocks):
    self._sync_phase = "SYNCED"
    return result
elif self._sync_block_queue or self._sync_inflight_blocks:
    log.debug("Continuing sync for pending blocks")
    return result
```

## Impact

| Aspect | Before | After |
|--------|--------|-------|
| Sync Completion | 🔴 Random heights | 🟢 All nodes at network tip |
| Block Requests | 🔴 Stop when inflight | 🟢 Continue continuously |
| Recovery | 🔴 None (stuck forever) | 🟢 Automatic |
| Status Accuracy | 🔴 "SYNCED" when behind | 🟢 Only when truly synced |

## Files Changed
- `p2p/node/p2p_service.py`: 2 locations, +17 lines, -2 lines
- Documentation: 3 new files explaining the fix

## Testing
To verify the fix works:
```bash
# Start multiple nodes
animica node up --network devnet --data-dir ~/.animica/node1
animica node up --network devnet --data-dir ~/.animica/node2

# Monitor sync progress
watch -n 1 'animica sync status --json | jq ".height"'

# Expected: All nodes reach same height, no stalls
```

## Monitoring
Watch for these log messages:
- ✅ `"Continuing sync for pending blocks"` - Fix is working
- ✅ `"Block persisted"` - Blocks downloading
- ✅ `"Head advanced"` - Progress continuing
- ❌ `"SYNCED"` when height < network - Would indicate issue

## Documentation
- `SYNC_STALL_FIX_SUMMARY.md` - Comprehensive technical analysis
- `SYNC_STALL_FIX_VISUAL.md` - Visual diagrams and timelines
- `SYNC_STALL_FIX_QUICK_REFERENCE.md` - This file

## Commit
- Branch: `copilot/fix-node-sync-issues`
- Commits: d81e18db (docs), 9b0ab03e (docs), fe6e26e2 (fix)

## Backward Compatibility
✅ Fully compatible - no API, protocol, or schema changes

## Rollback
If needed: `git revert d81e18db 9b0ab03e fe6e26e2`

---

**TL;DR:** Removed a restrictive condition preventing block downloads when inflight blocks exist, and added checks to ensure sync only marks as complete when truly caught up. Result: nodes no longer get stuck at random heights.
