# Not_Anchored Sync Recovery Implementation

## Problem
Nodes could get stuck in the HEADERS sync phase with repeated "not_anchored" errors, making no progress even with connected peers. The error occurs when received headers don't anchor to the local chain, causing the sync to retry indefinitely with the same peer.

## Solution: Progressive Recovery Strategy

We've implemented a three-stage progressive recovery mechanism that escalates based on the number of consecutive not_anchored errors:

### Stage 1: Backtracking (5-9 attempts)
When not_anchored errors persist for 5+ attempts, the node enters backtracking mode:
- **Increases locator depth** by adding 10 entries per backtrack level
- **Searches further back** in chain history to find a common ancestor
- **Clears in-flight headers** to allow fresh requests
- **Sets probe hash** to the parent of the problematic header

This allows the node to find a connection point deeper in the chain where both nodes agree on history.

### Stage 2: Block Skipping (10-19 attempts)
When backtracking doesn't resolve the issue (10+ attempts), the node skips problematic ranges:
- **Marks a problematic range** from anchor_height+1 to header.height+100
- **Removes queued blocks** in the problematic range
- **Clears in-flight requests** to reset the pipeline
- **Attempts to sync** from beyond the problematic range

This allows the node to bypass contentious or corrupted chain segments.

### Stage 3: Aggressive Recovery (20+ attempts)
When both backtracking and skipping fail (20+ attempts), the node performs aggressive cleanup:
- **Clears all in-flight requests** (headers and blocks)
- **Forces peer rotation** by clearing active peers
- **Resets recovery state** (backtrack depth, skip ranges)
- **Penalizes unanchored peers** to try different peers

This is a last resort before resetting to genesis.

### Timeout-Based Recovery
In addition to attempt-based recovery, we also implemented timeout detection:
- **Monitors in-flight requests** for stuck states
- **Clears stuck requests** after 3x normal timeout
- **Runs in every sync cycle** to catch hanging requests early

This prevents the node from waiting indefinitely for responses that will never come.

## Configuration
The recovery behavior can be tuned via environment variables:
- `ANIMICA_P2P_NOT_ANCHORED_BACKOFF`: Base delay between not_anchored retries (default: 30s)
- `ANIMICA_P2P_NOT_ANCHORED_BACKOFF_CAP`: Maximum backoff delay (default: 30s)
- `ANIMICA_P2P_NOT_ANCHORED_RESET_THRESHOLD`: Attempts before reset (increased to 30, was 3)
- `ANIMICA_P2P_NOT_ANCHORED_RESET_HEIGHT`: Max height for reset (default: 10)
- `ANIMICA_P2P_NOT_ANCHORED_WINDOW`: Time window for attempt counting (default: 300s)

## Testing
A comprehensive test suite was added in `p2p/tests/test_not_anchored_recovery.py`:
- `test_backtrack_recovery_increases_depth`: Verifies backtrack depth increases
- `test_skip_recovery_marks_range`: Verifies skip ranges are marked correctly
- `test_aggressive_recovery_clears_state`: Verifies state is cleared
- `test_progressive_recovery_escalation`: Verifies proper escalation through stages
- `test_header_locator_uses_backtrack_depth`: Verifies locator respects backtrack depth

## Usage
The recovery mechanism is automatic and requires no user intervention. However, users can monitor recovery progress:

```bash
# Monitor sync status
animica debug sync-dump

# Check for recovery actions
# Look for: last_recovery_action field showing:
# - backtrack_depth_N
# - skip_range_X_to_Y
# - aggressive_recovery_clear_and_rotate
# - timeout_clear_inflight_headers
```

## Impact
This implementation resolves the node stuck issue by:
1. **Automatically finding common ancestors** through progressive backtracking
2. **Bypassing problematic ranges** when necessary
3. **Forcing state cleanup** when all else fails
4. **Preventing indefinite hangs** through timeout detection

The node can now recover from stuck states without manual intervention while maintaining sync correctness.
