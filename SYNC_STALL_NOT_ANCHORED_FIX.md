# Sync Stall Fix: not_anchored Backoff Recovery

## Problem Statement

Nodes experience permanent sync stalls with error:
```
Sync phase:       STALLED
Stall reason:     not_anchored
Last block error:  not_anchored
Last recovery:    watchdog_requeue (attempt 1)
```

The node gets stuck indefinitely and requires manual intervention (`animica sync force`) to recover.

## Root Cause Analysis

### The Catch-22 Scenario

When checkpoint anchor validation fails for all connected peers, the node enters an unrecoverable stalled state:

1. **Checkpoint anchor probe fails** → Peer gets `not_anchored` backoff (30s default, exponential)
2. **Multiple peers fail** → All peers have `not_anchored` backoff
3. **Stall detected** → `_sync_block_stalled_reason = "not_anchored"` (line 8886)
4. **Recovery attempted** → `_handle_sync_stall()` called (line 8941)
5. **Peer selection fails** → `_select_block_peer()` finds no eligible peers (all have backoff)
6. **Stall persists** → `_sync_block_stalled_reason` remains set
7. **Sync blocked** → No block requests are made
8. **Manual intervention required** → User must run `animica sync force`

### Code Location: `p2p/node/p2p_service.py`

**Stall Detection (lines 8868-8890):**
```python
if (
    network_best_height is not None
    and int(network_best_height) - best_block_height >= 3
    and now - self._sync_last_progress_at > self._sync_stall_timeout
):
    inflight_stuck = bool(
        self._sync_inflight_blocks or self._sync_inflight_headers
    )
    queues_empty = not self._sync_block_queue and not self._sync_header_queue
    anchor_invalid = (
        self._sync_last_header_error == "not_anchored"
        or not any(self._peer_is_anchored(p) for p in self._peers.values())
    )
    if inflight_stuck or queues_empty or anchor_invalid:
        stall_reason = "inflight_stuck"
        if queues_empty:
            stall_reason = "queue_empty"
        if anchor_invalid:
            stall_reason = "not_anchored"  # ← Stall reason set
        self._sync_block_stalled_reason = stall_reason
        self._sync_last_block_error = stall_reason
        self._sync_last_block_error_at = now
        self._sync_kick(reason=f"stall:{stall_reason}", aggressive=True)
```

**Stall Handler (lines 3430-3492):**
```python
def _handle_sync_stall(self, *, reason: str) -> None:
    # ... penalize old peer, clear inflight blocks ...
    
    needed_height, _ = self._next_block_needed()
    new_peer = self._select_block_peer(  # ← Tries to find new peer
        needed_height=needed_height,
        require_anchored=self._should_enforce_checkpoint_anchor(),
    )
    if new_peer is None and self._should_enforce_checkpoint_anchor():
        new_peer = self._select_block_peer(
            needed_height=needed_height,
            require_anchored=False,
        )
    if new_peer:
        self._sync_active_block_peer = new_peer.remote
        self._sync_last_recovery_action = "retry_blocks_new_peer"
        self._sync_block_stalled_reason = None  # ← Only cleared if peer found
        self._stats["stall_recoveries"] += 1
        self._sync_wakeup.set()
    else:
        self._sync_last_recovery_action = "stall_no_peer"  # ← Stuck here!
```

**The Missing Fallback:**

`_select_sync_peer()` (for headers) has fallback logic (line 9378):
```python
def _select_sync_peer(...) -> Optional[_PeerState]:
    eligible, _ = self._eligible_sync_peers(...)
    if not eligible:
        # Fallback: retry ignoring not_anchored backoff
        eligible, _ = self._eligible_sync_peers(ignore_backoff_reason="not_anchored")
    # ...
```

But `_select_block_peer()` (for blocks) did NOT have this fallback, causing permanent stalls.

## Solution Implemented

### Changes Made

1. **Updated `_block_peer_eligibility()` (lines 9216-9236)**
   - Added `ignore_backoff_reason: Optional[str] = None` parameter
   - Pass through to `_sync_peer_eligibility()`

2. **Updated `_eligible_block_peers()` (lines 9238-9252)**
   - Added `ignore_backoff_reason: Optional[str] = None` parameter
   - Pass through to `_block_peer_eligibility()`

3. **Updated `_select_block_peer()` (lines 9438-9449)**
   - Added fallback logic to retry with `ignore_backoff_reason="not_anchored"`

### Code Changes

**Before:**
```python
def _select_block_peer(
    self,
    *,
    needed_height: Optional[int] = None,
    require_anchored: bool = False,
) -> Optional[_PeerState]:
    eligible, _ = self._eligible_block_peers()
    if require_anchored:
        anchored = [peer for peer in eligible if self._peer_is_anchored(peer)]
        if anchored:
            eligible = anchored
    if not eligible:
        return None  # ← No fallback, permanent stall
    # ...
```

**After:**
```python
def _select_block_peer(
    self,
    *,
    needed_height: Optional[int] = None,
    require_anchored: bool = False,
) -> Optional[_PeerState]:
    eligible, _ = self._eligible_block_peers()
    # Fallback: if no peers are eligible due to not_anchored backoff, retry ignoring it
    # This prevents permanent stalls when all peers temporarily fail checkpoint anchor validation
    if not eligible:
        eligible, _ = self._eligible_block_peers(ignore_backoff_reason="not_anchored")
    if require_anchored:
        anchored = [peer for peer in eligible if self._peer_is_anchored(peer)]
        if anchored:
            eligible = anchored
    if not eligible:
        return None
    # ...
```

## Expected Behavior

### Before the Fix

**Scenario:** All peers temporarily fail checkpoint anchor validation
```
1. Node at height 5,294, network best at 5,731
2. Checkpoint probe fails for all 3 connected peers
3. All peers get not_anchored backoff (30s each)
4. Stall detected: _sync_block_stalled_reason = "not_anchored"
5. _handle_sync_stall() called
6. _select_block_peer() finds no eligible peers
7. _sync_block_stalled_reason remains set
8. Sync stuck indefinitely
9. User must run: animica sync force
```

**Logs:**
```
WARN: Block sync stalled (last_block_request_at=..., stall_reason=not_anchored)
INFO: Block sync stall handled (old_peer=peer1, new_peer=None)
DEBUG: Skipped block requests: no eligible block peer
INFO: Sync phase: STALLED
```

### After the Fix

**Scenario:** Same situation, but now recovers automatically
```
1. Node at height 5,294, network best at 5,731
2. Checkpoint probe fails for all 3 connected peers
3. All peers get not_anchored backoff (30s each)
4. Stall detected: _sync_block_stalled_reason = "not_anchored"
5. _handle_sync_stall() called
6. _select_block_peer() first try finds no eligible peers
7. _select_block_peer() FALLBACK: retry ignoring not_anchored backoff
8. Peer selected despite backoff
9. _sync_block_stalled_reason cleared
10. Sync recovers automatically
11. No manual intervention needed
```

**Logs:**
```
WARN: Block sync stalled (last_block_request_at=..., stall_reason=not_anchored)
INFO: Block sync stall handled (old_peer=peer1, new_peer=peer2)
DEBUG: Selected sync peer for blocks (remote=peer2)
INFO: Block persisted (hash=0x..., height=5295)
INFO: Head advanced (height=5295)
INFO: Sync phase: BLOCKS
```

## Testing

### Unit Test Added

**Test:** `test_select_block_peer_ignores_not_anchored_backoff_on_retry`
**File:** `p2p/tests/test_sync_status.py`

**Scenario:**
1. Two peers both have `not_anchored` backoff
2. `_eligible_block_peers()` returns empty list
3. `_select_block_peer()` uses fallback
4. Peer is successfully selected

**Verification:**
```python
# Verify no eligible peers initially
eligible, ineligible = node._eligible_block_peers()
assert len(eligible) == 0
assert ineligible.get(peer_a.remote) == "not_anchored"

# But _select_block_peer should find a peer by ignoring not_anchored backoff
selected_peer = node._select_block_peer(needed_height=50)
assert selected_peer is not None
```

### Manual Testing

To verify on a stuck node:

```bash
# Check current state (before fix)
animica debug sync-dump

# Expected output showing stall:
# Sync phase:       STALLED
# Stall reason:     not_anchored
# In-flight:        headers=1 blocks=0

# After deploying the fix, wait 10-30 seconds
# The node should automatically recover

# Verify recovery
animica debug sync-dump

# Expected output showing recovery:
# Sync phase:       BLOCKS
# In-flight:        headers=0 blocks=5
# (height advancing)
```

## Impact Assessment

### Benefits
✅ Eliminates permanent sync stalls from `not_anchored` errors
✅ Reduces need for manual `animica sync force` intervention
✅ Improves node reliability and uptime
✅ Matches existing pattern used for header peer selection
✅ Allows temporary checkpoint validation issues to self-heal

### Risks
⚠️ **Low Risk** - The change is minimal and follows existing patterns

**Potential Concerns:**
- Peers with `not_anchored` backoff will be retried sooner than intended
- However, the backoff still exists and will be respected on subsequent attempts
- The penalty system still works (misbehavior scores, exponential backoff)

**Mitigations:**
- Backoff is only ignored when NO eligible peers exist
- Once a peer is selected, normal backoff rules apply again
- Peer penalties and scoring remain unchanged
- Existing tests verify peer selection logic

### Performance
- **CPU/Memory:** Negligible (one additional function call in fallback path)
- **Network:** Slightly increased retry attempts, but prevents long stalls
- **Sync Time:** Significantly improved (no permanent stalls)

## Backward Compatibility

✅ **Fully backward compatible:**
- No API changes
- No protocol changes  
- No database schema changes
- No configuration changes
- Only internal sync logic modified

## Files Modified

1. **`p2p/node/p2p_service.py`** (13 lines changed)
   - `_block_peer_eligibility()`: +2 lines (added parameter)
   - `_eligible_block_peers()`: +2 lines (added parameter)
   - `_select_block_peer()`: +3 lines (added fallback logic)

2. **`p2p/tests/test_sync_status.py`** (45 lines added)
   - New test: `test_select_block_peer_ignores_not_anchored_backoff_on_retry`

## Monitoring Recommendations

After deployment, monitor:

1. **Stall frequency**: Check logs for "Block sync stalled" with `not_anchored` reason
2. **Recovery success rate**: How often stalls clear automatically vs. manual intervention
3. **Peer backoff patterns**: Ensure backoff is still working as expected
4. **Sync completion rate**: % of nodes reaching network height
5. **Average sync time**: Should improve with fewer stalls

### Metrics to Track

```bash
# Count not_anchored stalls
grep "stall_reason=not_anchored" /var/log/animica/node.log | wc -l

# Count successful recoveries
grep "Block sync stall handled.*new_peer=peer" /var/log/animica/node.log | wc -l

# Check if nodes are synced
animica sync status --json | jq '{phase, height, network_best_height, stall_reason}'
```

## Rollback Plan

If issues arise, revert with:
```bash
git revert <commit-hash>
```

The change is minimal (13 lines) and isolated to block peer selection logic.

## Related Issues

This fix addresses:
- Permanent sync stalls with `not_anchored` error
- Nodes stuck requiring manual `animica sync force`
- Poor user experience during checkpoint sync
- Asymmetry between header and block peer selection logic

## Conclusion

This minimal fix resolves a critical usability issue where nodes would permanently stall when all peers temporarily failed checkpoint anchor validation. By adding the same fallback logic that header peer selection already uses, we enable automatic recovery from transient anchor validation issues.

The change is:
- ✅ Minimal (13 lines modified)
- ✅ Well-tested (new unit test added)
- ✅ Backward compatible (no API/protocol changes)
- ✅ Low risk (follows existing pattern)
- ✅ High impact (eliminates permanent stalls)

This transforms the user experience from "sync randomly breaks and needs manual force" to "sync automatically recovers from transient issues."
