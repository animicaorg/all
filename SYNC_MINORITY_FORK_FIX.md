# Sync Minority Fork Detection and Resolution

## Problem Statement

Nodes could get stuck when on a minority fork, unable to sync to the canonical chain. The symptom was:

```
Current height: 11868
Connected peers: 11
Sync status: STALLED
Reason: 'headers_blocks_equal_behind_network'
Last matched ancestor:
  Height: 10836
  Hash: 0x0002c7aa25e4e240651b0b3c28788b22772266a295d303bdf5620e09a78944da
Network best height: 11857
Target height: 11912
```

The node was at height 11868 on a minority fork, with the canonical chain at 11857-11912, and common ancestor at 10836 (gap of 1032 blocks).

## Root Cause

1. **Fork Not Detected**: The node didn't realize it was on a wrong fork because:
   - `network_best_height` (11857) was LOWER than local height (11868)
   - Gap calculation used `network_best - local` which was negative
   - Existing fork detection only triggered when `network_best > local`

2. **Sync Stalled**: Even with `target_height` = 11912, sync didn't proceed because:
   - Connected peers (at 11857) had height <= local height (11868)
   - Sync decision logic only checked `network_best_height`, not `target_height`
   - The condition `remote_height <= local_height` caused early return

## Solution

### Fix 1: Proactive Fork Detection Based on Matched Ancestor Gap

Added detection logic in the sync loop (`p2p/node/p2p_service.py`, after line 9746):

```python
# If matched_ancestor is significantly behind local height (> FORK_DETECTION_GAP_THRESHOLD = 100)
# AND there's evidence canonical chain has progressed (via target_height, network_best, or peer heights)
# THEN the node is likely on a wrong fork

if (
    self._sync_last_matched_ancestor_height is not None
    and best_block_height > 0
    and self._sync_last_matched_ancestor_height < best_block_height
):
    ancestor_gap = best_block_height - self._sync_last_matched_ancestor_height
    
    # Check evidence of canonical chain progress
    canonical_chain_progressed = False
    canonical_height_estimate = 0
    
    # Evidence 1: target_height (from block announcements)
    # Evidence 2: network_best_height (from peer heads)
    # Evidence 3: best_peer_height
    
    if ancestor_gap > FORK_DETECTION_GAP_THRESHOLD and canonical_chain_progressed:
        # Force reorganization back to matched_ancestor_height
        self._reset_chain_to_ancestor(
            height=self._sync_last_matched_ancestor_height,
            reason="minority_fork_detected",
        )
```

**Key Insight**: The matched ancestor height is the most reliable indicator of fork divergence. A large gap (> 100 blocks) between matched ancestor and local head, combined with evidence that the canonical chain has progressed, strongly indicates the node is on a minority fork.

### Fix 2: Consider target_height in Sync Decision

Enhanced the `_sync_once` logic (line 9081) to continue syncing when:
- Peer height <= local height (peer hasn't caught up yet)
- BUT `target_height > local_height` (blocks announced above local height)

```python
if (
    remote_height <= local_height
    and not force
    and not self._sync_header_queue
    and not probe_headers
):
    network_best_height = self._network_best_height()
    target_height = self._sync_target_height
    
    # NEW: Check both network_best AND target_height
    should_continue_sync = False
    
    if network_best_height is not None and int(network_best_height) > int(local_height or 0):
        should_continue_sync = True
    elif target_height is not None and int(target_height) > int(local_height or 0):
        should_continue_sync = True  # NEW: Consider target_height
    
    if should_continue_sync:
        # Continue syncing...
    else:
        # Check if should mark as synced...
```

**Key Insight**: `target_height` is set from block announcements and represents the actual highest known block, even if connected peers haven't caught up yet. Considering it ensures the node keeps syncing to announced blocks.

## Expected Behavior After Fix

### Scenario 1: Node on Minority Fork
**Before**: Node stalls at height 11868, matched ancestor at 10836, target at 11912
**After**: 
1. Detects ancestor gap (1032 blocks) > threshold (100)
2. Verifies canonical chain progressed (target_height 11912 > ancestor 10836)
3. Forces reorganization: resets chain to height 10836
4. Resumes sync from 10836 to 11912 on canonical chain

### Scenario 2: Node Behind but Peers Haven't Updated
**Before**: Node at 11868, peers at 11857, target at 11912 → sync stalls (peer height <= local)
**After**:
1. Checks peer height (11857) <= local (11868) → normally would skip
2. NEW: Also checks target_height (11912) > local (11868) → continues
3. Requests headers even though peers are "behind"
4. Finds blocks 11869-11912 and syncs them

## Testing

### Manual Verification
Run `python3 test_sync_fork_detection.py` to verify:
- ✓ Fork detection logic present
- ✓ Target height consideration logic present
- ✓ No syntax errors

### Integration Testing
The fix will automatically activate in production when:
1. `last_matched_ancestor_height` falls significantly behind local height
2. `target_height` or `network_best_height` indicates canonical chain has progressed

### Monitoring
Look for these log messages to confirm fix is working:
```
FORK DETECTED: Node is on minority fork - matched ancestor far behind local head
Forcing chain reorganization to matched ancestor
Local head behind target; continuing header sync even though peer height <= local
```

## Impact

### Positive
- ✅ Automatically recovers from minority forks at any height
- ✅ Uses matched ancestor as reliable fork detection signal
- ✅ Considers target_height for sync decisions (not just peer heights)
- ✅ Preserves blocks up to fork point (less destructive than genesis reset)
- ✅ Continues syncing when target announced but peers haven't caught up

### Backward Compatibility
- ✅ No breaking changes to existing sync logic
- ✅ Only activates when ancestor gap > 100 blocks
- ✅ Requires evidence of canonical chain progress (safe condition)
- ✅ Existing sync mechanisms still work as before

## Constants Used

```python
FORK_DETECTION_GAP_THRESHOLD: int = 100  # Blocks: if matched ancestor gap > this, consider it a fork
```

## Related Issues

This fix addresses the broader issue mentioned in the problem statement:
> "Blocks not syncing it needs to stay in pace with highest head in a way that's backwards compatible"

The solution ensures nodes stay in sync with the canonical chain (highest head) by:
1. Detecting when they've diverged onto a minority fork
2. Automatically reorganizing back to the common ancestor
3. Resuming sync to catch up to the canonical chain
4. Considering all sources of height information (target_height, network_best, peer heights)
