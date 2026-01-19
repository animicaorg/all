# Genesis Peer Eligibility Fix - Implementation Summary

## Problem Statement

Node is stuck at genesis (height 0) and cannot progress with the following symptoms:

```
Head height: 0
Sync status: SYNCING
Sync progress: 0.0% (0/1)
sync_status_reason: 'no_fresh_peer_tips'
peer_tips_fresh: 0
peer_tips_total: 0
Peers: 1 connected (but showing as 'pending [dialing]')
```

## Root Cause Analysis

### The Chicken-and-Egg Problem

In `p2p/node/p2p_service.py`, the `_sync_peer_eligibility()` function rejects peers with `head_height <= 0`:

```python
# OLD CODE (lines 12345-12346)
elif head_height <= 0:
    return False, "no_chain_data"
```

This creates a deadlock scenario:

1. **Initial State**: Two nodes both start at genesis (height 0)
2. **Handshake**: They connect and exchange Hello messages showing `head_height=0`
3. **Rejection**: Each node rejects the other as "no_chain_data" due to height 0
4. **Stuck**: Without syncing, neither node can progress to height 1
5. **Penalty Cycle**: Failed sync attempts lead to peer penalties and disconnections
6. **Permanent Stall**: Even when blocks become available, peers remain ineligible

### Secondary Issue

Similar filtering in `_select_block_peer()` (line 12698-12699) compounds the problem:

```python
# OLD CODE
if head_height <= 0:
    continue  # Skip peer entirely
```

This prevents selecting height 0 peers for block requests, even when requesting block 1 (the genesis transition).

## Solution

### Change 1: Allow Height 0 Peers at Genesis

Modified `_sync_peer_eligibility()` to check if the local node is also at genesis:

```python
# NEW CODE (lines 12339-12362)
caps = peer.hello.get("capabilities")
head_height = int(peer.hello.get("head_height") or 0)

# FIX: Allow peers at height 0 when local node is also at genesis
local_height, _ = self._local_head()
at_genesis = (int(local_height or 0) == 0)

if isinstance(caps, list) and caps:
    if "sync" not in caps and "blocks" not in caps and "headers" not in caps:
        if head_height <= 0:
            # If we're at genesis, allow peers at height 0 even without sync caps
            if not at_genesis:
                return False, "no_sync_capability"
elif head_height <= 0:
    # Allow height 0 peers when we're also at genesis
    if not at_genesis:
        return False, "no_chain_data"
return True, "eligible"
```

**Key Points:**
- At genesis (local height 0): Accept peers at height 0 as eligible
- After genesis (local height > 0): Reject peers at height 0 (existing behavior)
- Preserves security: Only relaxes restriction when both nodes are bootstrapping

### Change 2: Allow Height 0 Peers for Genesis Transitions

Modified `_select_block_peer()` to not filter height 0 peers when `needed_height <= 1`:

```python
# NEW CODE (lines 12698-12720)
if head_height <= 0:
    # Allow if we're transitioning from genesis (needed_height <= 1)
    # or if we have no better information (needed_height is None)
    if needed_height is not None and needed_height > 1:
        continue
    # For genesis transition, keep the peer as a candidate with height 0

if needed_height is not None and head_height < needed_height and head_height > 0:
    # Peer's advertised height is too low
    continue
candidates.append((head_height, peer))
```

**Key Points:**
- When requesting block 1: Keep height 0 peers as candidates
- When requesting block 10+: Skip height 0 peers (existing behavior)
- Accounts for stale hello messages where peer may have mined block 1

## Testing

### Unit Tests

Created `test_genesis_peer_eligibility_fix.py` with comprehensive test coverage:

1. ✓ Peers at height 0 are eligible when local node is at genesis
2. ✓ Peers at height 0 are rejected when local node is past genesis
3. ✓ Block peer selection allows height 0 peers for genesis transition
4. ✓ Block peer selection skips height 0 peers for higher blocks
5. ✓ Genesis bootstrap scenario works (mutual eligibility)
6. ✓ Height 0 peers without sync caps are eligible at genesis

All tests pass successfully.

### Manual Verification

Created `verify_genesis_peer_eligibility_fix.sh` for deployment testing:

1. Check nodes at genesis can see each other as eligible
2. Verify sync progresses when one node mines a block
3. Confirm continuous sync works for subsequent blocks
4. Test for regressions in normal sync behavior

## Expected Behavior After Fix

### Genesis Bootstrap Scenario

```
Time 0:
  Node A: height=0, eligible_peers=[Node B]
  Node B: height=0, eligible_peers=[Node A]
  Status: Both nodes see each other as eligible (FIX APPLIED)

Time 1 (Node B mines block 1):
  Node A: height=0, requests block 1 from Node B
  Node B: height=1, serves block 1 to Node A
  Status: Node A can sync from Node B (FIX APPLIED)

Time 2:
  Node A: height=1, synced successfully
  Node B: height=1
  Status: Both nodes at height 1, sync progressing normally

Time 3+ (subsequent blocks):
  Normal sync behavior, no special handling needed
```

### Before vs After

| Scenario | Before Fix | After Fix |
|----------|-----------|-----------|
| Local at genesis, peer at height 0 | ❌ Rejected ("no_chain_data") | ✅ Eligible |
| Local at height 5, peer at height 0 | ❌ Rejected ("no_chain_data") | ❌ Rejected ("no_chain_data") |
| Need block 1, peer at height 0 | ❌ Skipped | ✅ Candidate |
| Need block 10, peer at height 0 | ❌ Skipped | ❌ Skipped |

## Impact Assessment

### What Changed
- Peer eligibility check now considers local node height
- Block peer selection allows height 0 peers for genesis transitions
- No changes to sync logic, consensus, or protocol

### What Didn't Change
- After genesis, height 0 peers are still rejected (security preserved)
- Higher height peers are still preferred (performance preserved)
- All other eligibility checks remain unchanged (compatibility preserved)

### Risk Analysis
- **Low Risk**: Changes are minimal and well-scoped
- **Safety**: Only affects genesis bootstrap scenario
- **Compatibility**: Backward compatible with existing nodes
- **Testing**: Comprehensive unit tests + manual verification guide

## Deployment Checklist

- [x] Code changes implemented
- [x] Unit tests created and passing
- [x] Manual verification script created
- [ ] Deploy to test environment
- [ ] Run manual verification steps
- [ ] Monitor for peer connection stability
- [ ] Verify sync progresses from genesis
- [ ] Check for regressions in normal sync
- [ ] Deploy to production

## Rollback Plan

If issues arise:

1. **Immediate**: Revert the two changes in `p2p/node/p2p_service.py`
2. **Monitor**: Check for "no_chain_data" errors in logs
3. **Verify**: Ensure normal (non-genesis) sync still works
4. **Investigate**: Collect logs from affected nodes

Rollback is safe because changes are isolated to peer eligibility logic.

## Related Issues

This fix addresses the core issue reported in the problem statement:
- Syncing not progressing past genesis
- `no_fresh_peer_tips` even with peers connected
- Peers stuck in "pending [dialing]" state

## References

- Modified file: `p2p/node/p2p_service.py`
- Test file: `test_genesis_peer_eligibility_fix.py`
- Verification script: `verify_genesis_peer_eligibility_fix.sh`
- Lines changed: 12339-12362, 12698-12720
