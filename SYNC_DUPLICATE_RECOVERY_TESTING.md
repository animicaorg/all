# Sync Duplicate Recovery - Testing Guide

## Problem Being Fixed

Nodes getting stuck at 99.3% sync with symptoms:
- Local height: 7468
- Network height: 7520  
- Last matched ancestor: 6436
- All peers returning "duplicate" headers
- Node rotating through all peers infinitely without progress

## Root Cause

When all peers return headers that are "all_known" (already in local store by hash):
1. Headers are rejected as duplicates
2. Locator depth hint increases (makes locator less detailed)
3. Node rotates to next peer
4. Repeat indefinitely → **Stuck forever**

## Fix Implementation

### 1. All-Peers-Duplicate Recovery (lines ~8823-8867)
When no eligible peers remain:
- **Condition:** All peers tried AND last error is "duplicate_headers" AND stalled > 60s
- **Action:** 
  - Reset `_sync_locator_depth_hint` to 0
  - Clear `duplicate_headers` error
  - Clear all peer backoffs for duplicate_headers
  - Clear duplicate header range tracking

### 2. Extended-Stall Reset (lines ~9192-9225)  
When receiving duplicate headers from a peer:
- **Condition:** Stalled > 60s AND depth_hint > 0 AND duplicate count >= threshold
- **Action:**
  - Reset depth hint to 0 instead of increasing
  - Don't penalize peer (may be giving correct headers)
  - Rotate to next peer

## Manual Testing

### Prerequisites
- Running Animica node
- Access to node logs and CLI commands
- Network with peers at higher height than local node

### Test Scenario 1: Verify Recovery Triggers

1. **Observe stalled sync:**
   ```bash
   animica sync status
   # Should show stuck at same height for > 60s
   ```

2. **Check for duplicate header errors:**
   ```bash
   tail -f ~/.animica/logs/node.log | grep -i duplicate
   # Look for "duplicate_headers" errors
   ```

3. **Wait for recovery trigger:**
   Look for log message:
   ```
   All peers returned duplicate headers with no progress; resetting sync state
   ```
   
   Should include:
   - `tried_peers`: Number of peers tried
   - `eligible_peers`: Total eligible peer count
   - `stall_duration_s`: How long stalled
   - `locator_depth_hint`: Current depth (should reset to 0)

4. **Verify recovery actions:**
   ```bash
   # Check sync continues
   animica sync status
   # Height should start increasing within 1-2 minutes
   ```

### Test Scenario 2: Verify Normal Operation Unchanged

1. **Fresh sync (not stalled):**
   ```bash
   # Start node from scratch or reset
   animica node start
   ```

2. **Monitor sync progress:**
   ```bash
   watch -n 5 'animica sync status'
   # Should progress normally without recovery triggers
   ```

3. **Check logs don't show unnecessary resets:**
   ```bash
   grep "resetting sync state" ~/.animica/logs/node.log
   # Should be empty or minimal during normal sync
   ```

### Test Scenario 3: Reproduce Original Bug (Before Fix)

To verify the bug exists without the fix:

1. **Git checkout before fix:**
   ```bash
   git checkout <parent-commit>
   ```

2. **Start node and wait for stall:**
   ```bash
   animica node start
   # Wait for sync to reach ~99% and stall
   ```

3. **Observe infinite loop:**
   ```bash
   tail -f ~/.animica/logs/node.log | grep -E "duplicate_headers|Selecting peer"
   # Should see continuous peer rotation with no progress
   ```

4. **Checkout fix and verify resolution:**
   ```bash
   git checkout copilot/fix-sync-progress-issue
   animica node restart
   # Should recover and complete sync
   ```

## Expected Log Messages

### Recovery Triggered (Good)
```
All peers returned duplicate headers with no progress; resetting sync state
  tried_peers: 5
  eligible_peers: 5
  stall_duration_s: 124.5
  locator_depth_hint: 32
```

### Extended Stall Reset (Good)
```
Duplicate headers with extended stall; resetting locator depth
  peer: 89.85.40.184:49508
  duplicate_count: 3
  stall_duration_s: 87.2
  old_depth_hint: 24
```

### Normal Progress (Good)
```
Sync cycle
  phase: SYNCING
  head_height: 7469
  best_header_height: 7520
  ...
```

### Still Stuck (Bad - Fix Not Working)
```
# Repeating pattern without height increase:
Sync cycle
  phase: SYNCING
  head_height: 7468  # Same height
  ...
  stall_elapsed_s: 180  # Increasing
```

## Success Criteria

✅ **Fix is working if:**
1. Node completes sync to network height
2. Recovery log messages appear when stalled
3. Locator depth hint resets to 0 after trigger
4. Height increases within 1-2 minutes of recovery
5. Normal syncs (not stalled) don't trigger unnecessary resets

❌ **Fix is not working if:**
1. Node still stuck at 99.3% after 5+ minutes
2. No recovery log messages appear
3. Locator depth keeps increasing past 64
4. Height stays constant despite recovery triggers

## Debug Commands

```bash
# Check current sync state
animica debug sync-dump

# Check peer status
animica node status

# Force sync trigger (if stuck)
animica sync force

# Check recent blocks
animica chain head -n 5

# Monitor real-time logs
tail -f ~/.animica/logs/node.log | grep -E "Sync|duplicate|recovery"
```

## Rollback Plan

If the fix causes issues:

```bash
# Revert to previous version
git revert <this-commit-hash>

# Or checkout main
git checkout main

# Restart node
animica node restart
```

## Related Files

- **Implementation:** `p2p/node/p2p_service.py`
- **Unit Tests:** `test_sync_duplicate_recovery.py`
- **Integration Tests:** `p2p/tests/test_sync_loop_behavior.py`

## Additional Notes

- Recovery timeout is controlled by `_sync_stall_timeout` (default 60s)
- Duplicate threshold is controlled by `ANIMICA_P2P_DUPLICATE_HEADERS_THRESHOLD` (default 2)
- Locator depth hint range: 0-64
- Maximum depth increase per duplicate: 8
- Genesis blocks are always included in locators

## Contact

For issues or questions about this fix:
- GitHub Issue: [Link to issue]
- Pull Request: [Link to PR]
