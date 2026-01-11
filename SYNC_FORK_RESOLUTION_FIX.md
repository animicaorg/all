# Sync Fork Resolution Fix

## Problem

Nodes could get permanently stuck when syncing if they were on a long fork (more than 10 blocks diverged from the network). This manifested as:

- Sync phase stuck in `HEADERS`
- Headers being received but not accepted (`last_headers_accepted_count: 0`)
- Repeated "not_anchored" errors
- No automatic recovery

### Example from Bug Report

```
Phase: HEADERS
Head height: 5420
Network best: 6593
Last matched ancestor: 5156
Headers accepted: 0 (33 received)
In flight headers: 1
```

The node was at height 5420, but the chain diverged from the network starting at block 5157. Headers from peers were rejected because they didn't match the local chain, and the node had no way to recover automatically.

## Root Cause

The existing recovery mechanism only worked for forks very close to genesis:

```python
should_reset = (
    anchor_height <= 10  # Only works if fork is at height 10 or below
    and not_anchored_attempts >= 3
    and stalled > 20 seconds
)
```

For longer forks (like the 264-block fork in the bug report), this condition never triggered, leaving the node permanently stuck.

## Solution

Added a new fork resolution mechanism that rolls back to the last matched ancestor instead of resetting all the way to genesis:

### New Logic

```python
should_reset_to_ancestor = (
    not_anchored_attempts >= 3
    and stalled > 20 seconds
    and matched_ancestor_height is not None
    and matched_ancestor_height < anchor_height
)
```

This condition:
- Works for forks at **any height**, not just near genesis
- Uses the **matched ancestor** tracked during sync
- Only requires 3 failed attempts and 20 seconds of stall time
- Is **less destructive** than resetting to genesis

### Implementation Details

#### 1. New Function: `_reset_chain_to_ancestor()`

```python
def _reset_chain_to_ancestor(self, *, height: int, reason: str) -> bool:
    """
    Reset chain to a specific ancestor height to resolve forks.
    This is less drastic than resetting to genesis.
    """
```

This function:
- Gets the canonical hash at the ancestor height
- Sets that as the new chain head
- Prunes all blocks and headers above the ancestor
- Clears sync state for heights above the ancestor
- Triggers immediate re-sync from the correct position

#### 2. Helper Function: `_header_height()`

```python
def _header_height(self, block_hash: bytes) -> Optional[int]:
    """Get the height of a header by its hash."""
```

Used to safely filter blocks/headers by height with null safety.

#### 3. Modified: `_note_not_anchored()`

Added the ancestor rollback logic alongside the existing genesis reset:

```python
if should_reset and self._reset_chain_to_genesis(reason="not_anchored"):
    action = "reset_to_genesis"
elif should_reset_to_ancestor and self._reset_chain_to_ancestor(
    height=self._sync_last_matched_ancestor_height,
    reason="fork_resolution",
):
    action = "reset_to_ancestor"
```

## Benefits

### Before the Fix
- **Long forks**: Permanently stuck, no recovery
- **Recovery method**: Manual intervention or reset to genesis
- **Data loss**: All blocks above genesis lost on reset

### After the Fix
- **Long forks**: Automatic recovery within 60 seconds
- **Recovery method**: Intelligent rollback to fork point
- **Data preservation**: All blocks up to fork point preserved

## Testing

### Test Coverage

Run the test suite to verify the fix:

```bash
python3 test_sync_fork_resolution.py
```

Tests verify:
- ✓ `_reset_chain_to_ancestor` method exists
- ✓ `_header_height` helper method exists
- ✓ Fork resolution condition added
- ✓ Uses matched ancestor height for rollback
- ✓ Rollback to ancestor implemented
- ✓ Null safety in height filtering
- ✓ Genesis reset correctly disabled for long forks
- ✓ Ancestor reset correctly enabled for long forks
- ✓ Recovery action tracking

### Manual Testing

To verify the fix resolves the reported issue:

1. Start a node that's on a fork
2. Wait for 3+ "not_anchored" errors (check logs)
3. After 20 seconds of stall, the rollback should trigger
4. Check logs for: `"Resetting chain to ancestor to resolve fork"`
5. Verify sync resumes from the rolled-back height

## Monitoring

### Log Messages

**When rollback is triggered:**
```
WARNING: Resetting chain to ancestor to resolve fork
  height: 5156
  hash: 0x0000421b...
  reason: fork_resolution
  matched_ancestor: 5156
```

**When rollback completes:**
```
WARNING: Chain reset to ancestor complete
  new_head_height: 5156
  new_head_hash: 0x0000421b...
```

### Metrics to Watch

- `recovery_attempts`: Should increase when fork is detected
- `last_recovery_action`: Should show "reset_to_ancestor"
- `sync_head_height`: Should decrease to ancestor height after rollback
- `last_progress_at`: Should update after rollback completes

## Comparison with Genesis Reset

| Aspect | Reset to Genesis | Reset to Ancestor |
|--------|------------------|-------------------|
| **Trigger** | Fork at height ≤10 | Fork at any height |
| **Data loss** | All blocks above 0 | Only forked blocks |
| **Sync time** | Full re-sync required | Resume from fork point |
| **Destructiveness** | Very high | Minimal |
| **Use case** | Network mismatch | Chain fork/reorg |

## Configuration

The fork resolution uses existing configuration:

- `ANIMICA_P2P_NOT_ANCHORED_RESET_THRESHOLD`: Number of attempts before reset (default: 3)
- `ANIMICA_SYNC_STALL_TIMEOUT_S`: Stall duration before reset (default: 20 seconds)

No new configuration is required for this fix.

## Future Improvements

Potential enhancements:
1. Add metrics for fork resolution events
2. Expose fork detection via RPC
3. Add configurable ancestor selection strategy
4. Implement gradual rollback for very large forks
5. Add fork recovery to sync status endpoint

## References

- **Bug Report**: Sync stuck at height 5420 with in_flight_headers=1
- **Implementation**: `p2p/node/p2p_service.py`
- **Test Suite**: `test_sync_fork_resolution.py`
- **Related Fixes**: 
  - Sync stall timeout reduction
  - Network best height staleness detection
  - Header batch size optimization
