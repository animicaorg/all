# Sync Stall Fix Summary - Never Catches Up at 99.3%

## Issue Description

Nodes becoming stuck during sync at approximately 99.3% completion (e.g., 7468/7520 blocks), unable to progress despite:
- Having 20 active peers
- Network being ahead (7520 blocks vs 7468 local)
- Last matched ancestor being far behind (6436 vs 7468 local)
- All operations appearing normal in diagnostics

**Symptom:** Node infinitely rotates through all peers, each returning "duplicate" headers, never making progress.

## Root Cause Analysis

### The Duplicate Header Loop

1. **Locator Building:** Node builds header locator from height 7468 using exponential backoff
   - First 10 entries: consecutive blocks (7468, 7467, ..., 7458)
   - After 10: exponential steps (7456, 7440, 7408, 7344, ...)
   - With large gaps, peers find match at 6436

2. **Headers Response:** Peers return headers starting from 6437 onwards

3. **Duplicate Detection:** Node checks if all returned headers are "known":
   ```python
   all_known = all(
       self._has_header(bytes(h.hash))
       or bytes(h.hash) in self._sync_headers
       for h in headers
   )
   ```

4. **The Problem:** When `all_known = True`:
   - Headers are rejected as duplicates
   - Locator depth hint increases by 8 (makes locator less detailed)
   - Peer is penalized and backoff applied
   - Node rotates to next peer
   - **Repeat indefinitely** if all peers have the same chain

5. **Why It Fails:** The code assumes "all known" means "already synced", but actually means:
   - Headers exist locally at same heights
   - But might be on a different chain/fork
   - OR there's a gap in block data despite having headers
   - Increasing locator depth makes the problem WORSE (less detailed matching)

### The Infinite Loop

```
Try Peer 1 → Duplicate (depth: 0→8) → Rotate
Try Peer 2 → Duplicate (depth: 8→16) → Rotate  
Try Peer 3 → Duplicate (depth: 16→24) → Rotate
Try Peer 4 → Duplicate (depth: 24→32) → Rotate
Try Peer 5 → Duplicate (depth: 32→40) → Rotate
All peers exhausted → Retry Peer 1 → Duplicate (depth: 40→48) → Rotate
... (continues forever, depth caps at 64)
```

## Solution Implementation

### Fix #1: All-Peers-Duplicate Recovery

**Location:** `p2p/node/p2p_service.py` lines ~8837-8867

When peer selection fails (all peers tried):

```python
if (
    len(tried_peers) >= eligible_count
    and eligible_count > 0
    and self._sync_last_header_error == "duplicate_headers"
    and now - self._sync_last_progress_at > self._sync_stall_timeout
):
    # RECOVERY: Reset everything to try again fresh
    self._sync_locator_depth_hint = 0  # Get detailed locators again
    self._sync_last_header_error = None  # Clear error state
    self._sync_duplicate_header_ranges.clear()  # Reset tracking
    # Clear backoffs for duplicate_headers reason only
    for backoff_key in list(self._sync_peer_backoff.keys()):
        if self._sync_peer_backoff_reason.get(backoff_key) == "duplicate_headers":
            self._sync_peer_backoff.pop(backoff_key, None)
```

**Effect:** After trying all peers with no progress for 60+ seconds, reset to initial state and retry with fresh, detailed locators.

### Fix #2: Extended-Stall Depth Reset

**Location:** `p2p/node/p2p_service.py` lines ~9198-9227

When processing duplicate headers from a peer:

```python
if duplicate_count >= self._sync_duplicate_headers_threshold:
    stall_duration = now - self._sync_last_progress_at
    
    # If stalled too long, RESET instead of increasing depth
    if stall_duration > self._sync_stall_timeout and self._sync_locator_depth_hint > 0:
        self._sync_locator_depth_hint = 0  # Reset instead of increase
        # Don't penalize - peer may be giving correct headers
    else:
        # Normal path: increase depth and penalize
        self._sync_locator_depth_hint = min(
            self._sync_locator_depth_hint + 8, 64
        )
        self._penalize_peer(peer, "duplicate_headers")
```

**Effect:** Instead of continuously increasing depth (making problem worse), reset to 0 after extended stall to get detailed locators that might find the real common ancestor.

## Why This Fixes The Issue

1. **Breaks the Loop:** After trying all peers, state is reset, allowing fresh attempts
2. **Better Locators:** Resetting depth to 0 creates more detailed locators with closer spacing
3. **Timeout-Based:** Only triggers after genuine stall (60s), not during normal sync
4. **Preserves Normal Operation:** Short-term duplicates still increase depth normally
5. **No Peer Punishment:** During reset, peers aren't penalized (they may be correct)

## Trade-offs & Considerations

### Pros
- ✅ Fixes infinite loop stalls
- ✅ Minimal code change (2 small additions)
- ✅ No breaking changes to protocol
- ✅ Preserves existing behavior when not stalled
- ✅ Self-recovers without manual intervention

### Cons  
- ⚠️ Adds 60s delay before recovery triggers
- ⚠️ May cause extra peer rotation during genuine forks
- ⚠️ Doesn't address underlying locator algorithm limitations

### Future Improvements

1. **Smarter Locator Algorithm:** Use more adaptive spacing based on gap size
2. **Fork Detection:** Explicitly detect when local chain differs from network consensus
3. **Forced Reorg:** When all peers agree on different chain, force adoption
4. **Checkpoint Sync:** Use network checkpoints to validate chain branches
5. **Metrics:** Track duplicate header patterns to detect systematic issues

## Testing

### Unit Tests
`test_sync_duplicate_recovery.py` validates:
- ✅ Locator depth resets when stalled with duplicates
- ✅ Backoff state clears when all peers return duplicates
- ✅ Normal duplicate handling still increases depth
- ✅ Depth caps at 64 properly

### Manual Testing
See `SYNC_DUPLICATE_RECOVERY_TESTING.md` for comprehensive testing procedures.

### Integration Testing
Requires live network or devnet simulation with:
- Multiple peers at consistent height
- Local node behind by 50+ blocks
- Monitoring for stall and recovery patterns

## Success Metrics

**Before Fix:**
- Sync permanently stuck at ~99%
- Continuous peer rotation logged
- No progress despite active peers
- Manual intervention required

**After Fix:**
- Sync completes to 100%
- Recovery logs appear when stalled
- Height increases within 1-2 minutes
- No manual intervention needed

## Deployment Notes

- **Backward Compatible:** No protocol changes, works with all peer versions
- **Configuration:** Uses existing `_sync_stall_timeout` (60s default)
- **Monitoring:** Watch for "resetting sync state" log messages
- **Rollback:** Safe to revert, no persistent state changes

## Related Issues & PRs

- Original Issue: Sync stuck at 99.3%
- Related: Sync stall detection and recovery  
- See also: Locator algorithm optimization (future work)

## Files Changed

1. `p2p/node/p2p_service.py` - Sync loop recovery logic
2. `test_sync_duplicate_recovery.py` - Unit tests
3. `SYNC_DUPLICATE_RECOVERY_TESTING.md` - Testing guide
4. `SYNC_DUPLICATE_RECOVERY_SUMMARY.md` - This document

---

**Status:** ✅ Implementation Complete  
**Testing:** ⚠️ Manual verification required  
**Deployment:** 🟡 Ready for staging/testnet
