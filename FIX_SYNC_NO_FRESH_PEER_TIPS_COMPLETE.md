# Fix Complete: Sync Stuck at Genesis with no_fresh_peer_tips

## Problem Summary

Nodes were getting stuck at genesis (height 0) with the error `sync_status_reason: 'no_fresh_peer_tips'` despite:
- Being connected to peers that had completed handshakes
- The network having newer blocks available (e.g., height 3)
- Headers being received from peers

This prevented:
- New nodes from syncing with the network
- Mining from functioning across multiple nodes (blocks not propagating)

## Root Cause

Two locations in `p2p/node/p2p_service.py` incorrectly assumed a node was "at_tip" when `network_best_height is None` (no fresh peer tips available):

### 1. Sync Loop (lines 11317-11330)
When receiving empty headers with `network_best_height=None`, the code had:
```python
elif (
    network_best_height is None
    and best_header_height <= int(local_height or 0)
):
    at_tip = True  # BUG: Assumes at_tip when network info missing!
```

This caused the node to:
- Set `at_tip=True`
- Transition sync phase to IDLE
- Stop requesting headers and blocks
- Never recover even when peers became available

### 2. _empty_headers_reason Function (lines 14370-14375)
The function returned "at_tip" when `network_best_height is None`:
```python
if (
    remote_height <= local_height
    and (network_best_height is None or network_best_height <= local_height)  # BUG!
    and (max_observed_height is None or max_observed_height <= local_height + 1)
):
    return "at_tip"
```

This caused sync to believe it was at the network tip and stop trying.

## Why This is Wrong

When `network_best_height is None`, it means:
- We have **no reliable information** about the network state
- Peer tips are stale (not updated in the last 600 seconds)
- Peer connections may be unstable or incomplete

In this case, we should **NOT** assume we're at the tip. We should:
- Keep trying to sync
- Continue polling peers
- Wait for fresh peer tip information
- Only declare "at_tip" when we have **confirmed** network information

## The Fix

### 1. Sync Loop (lines 11317-11330)
**Removed** the `elif` block that set `at_tip=True` when `network_best_height is None`:

```python
at_tip = False
# FIX: Only consider at_tip if we have reliable network info
# Do NOT assume at_tip when network_best_height is None (no fresh peer tips)
# This prevents premature IDLE state when peer connections are unstable
if (
    network_best_height is not None
    and int(network_best_height) <= int(local_height or 0)
):
    at_tip = True
# REMOVED: elif block that assumed at_tip when network_best_height is None
```

### 2. _empty_headers_reason Function (lines 14370-14375)
**Changed** condition to require `network_best_height is not None`:

```python
# FIX: Only return "at_tip" when we have reliable network info
# Do NOT assume at_tip when network_best_height is None (no fresh peer tips)
# This prevents premature sync stoppage when peer connections are unstable
if (
    remote_height <= local_height
    and network_best_height is not None  # CHANGED: require valid network height
    and network_best_height <= local_height
    and (max_observed_height is None or max_observed_height <= local_height + 1)
):
    return "at_tip"
```

## Behavior Changes

### Before Fix (Buggy)
```
Scenario: Node at height 0, network_best_height=None
├─ at_tip = True (WRONG!)
├─ sync_phase = IDLE (WRONG!)
└─ Result: Node stops syncing permanently
```

### After Fix (Correct)
```
Scenario: Node at height 0, network_best_height=None
├─ at_tip = False (CORRECT)
├─ sync_phase = SYNCING (CORRECT)
└─ Result: Node continues trying to sync
    ├─ Polls peers periodically
    ├─ Requests headers when peers respond
    └─ Eventually syncs when fresh peer tips become available
```

## Testing

### Unit Tests
Created comprehensive test suite: `test_sync_no_fresh_peer_tips_fix.py`

**Test 1: Sync loop logic**
- ✅ With `network_best_height=None`: `at_tip=False`, `sync_phase=SYNCING`
- ✅ With `network_best_height=0`: `at_tip=True`, `sync_phase=IDLE` (correctly at tip)

**Test 2: _empty_headers_reason logic**
- ✅ With `network_best_height=None`: returns "headers_empty" (not "at_tip")
- ✅ With `network_best_height=0`: returns "at_tip" (correctly at tip)

**Test 3: Bug scenario reproduction**
- ✅ Verifies old buggy behavior would set `at_tip=True` and go IDLE
- ✅ Verifies new fixed behavior sets `at_tip=False` and stays SYNCING

All tests pass ✅

### Code Review
- ✅ Completed with 3 minor comment corrections
- ✅ No logic issues found
- ✅ Changes are minimal and surgical

### Security Scan
- ✅ No security vulnerabilities detected
- ✅ CodeQL analysis: no issues

## Impact Analysis

### Risk: **LOW**
- Only affects behavior when `network_best_height is None`
- Existing correct "at_tip" detection still works when network info is available
- No breaking changes to protocol or data structures

### Benefit: **HIGH**
- Fixes critical sync stuck issue preventing network operation
- Enables nodes to sync from genesis successfully
- Allows mining to work across multiple nodes
- Improves network resilience to temporary peer connection issues

## Files Changed

1. **p2p/node/p2p_service.py** (2 locations)
   - Line 11317-11330: Sync loop logic fix
   - Line 14370-14375: _empty_headers_reason logic fix

2. **test_sync_no_fresh_peer_tips_fix.py** (new)
   - Comprehensive test suite
   - Documents buggy vs fixed behavior
   - All tests pass

## Manual Testing Recommendations

While unit tests verify the logic is correct, manual testing is recommended to confirm the fix works in a real environment:

### Test Scenario 1: Fresh Node Sync
1. Start a mining node (Node A) and mine 3 blocks
2. Start a fresh node (Node B) configured to connect to Node A
3. **Expected**: Node B should sync to height 3 (not get stuck at height 0)
4. Verify with `animica node status` on Node B

### Test Scenario 2: Cross-Node Mining
1. Start two nodes connected to each other
2. Mine blocks on Node A
3. **Expected**: Node B should receive and validate those blocks
4. Mine blocks on Node B
5. **Expected**: Node A should receive and validate those blocks

### Test Scenario 3: Recovery from Stale Peers
1. Start Node A at height 0
2. Connect Node A to Node B (also at height 0)
3. Disconnect Node B
4. Mine blocks on Node B (offline from Node A)
5. Reconnect Node B to Node A
6. **Expected**: Node A should sync to Node B's height (not stay at genesis)

## Rollout Recommendations

1. **Deploy to testnet first** - Verify fix works in testnet environment
2. **Monitor sync metrics** - Watch for `no_fresh_peer_tips` occurrences
3. **Check sync phase transitions** - Ensure nodes don't get stuck in IDLE
4. **Verify cross-node block propagation** - Test mining on multiple nodes

## Related Documentation

- **Original Issue**: Problem statement in PR description
- **Peer Tip Freshness**: `PEER_TIP_FRESHNESS_SEC = 600.0` (10 minutes)
- **HEAD_STATUS Polling**: Every 20 seconds (`_peer_head_poll_interval_sec`)
- **Network Best Height**: Computed from fresh peer tips (<600s old)

## Conclusion

This fix addresses a critical bug where nodes would incorrectly assume they were "at_tip" when peer information was unavailable. The fix is minimal, well-tested, and has low risk with high benefit. Nodes will now continue trying to sync even when peer connections are temporarily unstable, leading to a more resilient network.
