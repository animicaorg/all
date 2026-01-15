# Fix Summary: Syncing Only Occurs for a Short While on Startup

## Problem Statement
Nodes were syncing for only a short while on startup, then stopping permanently even when new blocks were available on the network.

## Root Cause Analysis

### The Issue
After initial sync, nodes could enter states where they thought they were "at tip" but actually weren't:

1. **Stale peer information**: Peers haven't updated their heights yet
2. **Missed block announcements**: Block announcements not received or processed properly
3. **Peer backoffs**: All peers marked with backoff states ("headers_empty", "peer_behind", "at_tip")
4. **No periodic verification**: No mechanism to periodically verify node is truly synced

### Why It Happens
When a node starts up:
1. It syncs headers and blocks successfully
2. Eventually reaches a state where all connected peers report same height
3. Node enters SYNCED or TARGET_REACHED phase
4. Even though new blocks are being produced on the network:
   - Peer heights might not update immediately
   - Block announcements might be missed
   - All peers get marked with backoff preventing new requests
5. **Node stops syncing permanently** - sync loop continues but no new requests are made

## The Solution

### Periodic Health Check
Added a **periodic health check** that runs every 30 seconds to verify the node is truly synced.

### How It Works
```python
# Configuration constant (line 87)
PERIODIC_HEALTH_CHECK_INTERVAL_SEC: float = 30.0

# In sync loop (lines 9740-9770)
periodic_health_check = False
if (
    phase in ("SYNCED", "TARGET_REACHED", "IDLE")  # Node thinks it's at tip
    and now - last_progress > PERIODIC_HEALTH_CHECK_INTERVAL_SEC  # Stale for 30s
    and not inflight_headers  # Not already fetching
    and not inflight_blocks   # Not already fetching
    and has_peers  # Have peers to query
):
    periodic_health_check = True
    
    # Clear peer backoffs that might be preventing requests
    cleared = 0
    cleared += _clear_sync_backoff_reason("headers_empty")
    cleared += _clear_sync_backoff_reason("peer_behind")
    cleared += _clear_sync_backoff_reason("at_tip")
    
    log.info("Periodic sync health check triggered", ...)

# Integrate into force_sync flag (line 9773)
force_sync = (
    stalled 
    or sync_force_always 
    or sync_requested 
    or at_tip_but_behind 
    or periodic_health_check  # NEW: Include periodic check
)

# Pass to sync attempt (line 9774)
await _sync_once(force=force_sync)
```

### What This Does
1. **Every 30 seconds** when node is idle at tip
2. **Clears peer backoffs** to allow retrying header requests
3. **Forces a sync attempt** even when node thinks it's at tip
4. **Verifies** by requesting headers from peers
5. **Discovers** any new blocks that were missed

### Safety Features
- ✅ Only triggers when truly idle (no progress for 30s)
- ✅ Respects inflight requests (doesn't create duplicates)
- ✅ Requires at least one peer (doesn't run solo)
- ✅ Only applies to terminal phases (SYNCED/TARGET_REACHED/IDLE)
- ✅ Configurable via constant

## Files Modified

### p2p/node/p2p_service.py
**Lines 87**: Added `PERIODIC_HEALTH_CHECK_INTERVAL_SEC` constant
**Lines 9740-9773**: Added periodic health check logic
- Checks if node is idle at tip
- Clears peer backoffs
- Sets `periodic_health_check = True`
- Integrates into `force_sync` flag

### Test Files (New)
**verify_periodic_health_check.py**: 7 unit tests verifying trigger conditions
**test_periodic_health_check_integration.py**: 4 integration tests demonstrating the fix

## Testing Results

### Unit Tests (7 tests)
✅ Test 1: SYNCED phase, stale → triggers
✅ Test 2: TARGET_REACHED phase, stale → triggers
✅ Test 3: SYNCED phase, recent progress → doesn't trigger
✅ Test 4: SYNCING phase → doesn't trigger
✅ Test 5: SYNCED with inflight headers → doesn't trigger
✅ Test 6: IDLE phase, stale → triggers
✅ Test 7: No peers → doesn't trigger

### Integration Tests (4 tests)
✅ Bug scenario: Node gets stuck without fix
✅ Fix scenario: Periodic check recovers from stuck state
✅ No false positives test
✅ Respects inflight requests test

## Verification

### Run Tests
```bash
# Unit tests
python3 verify_periodic_health_check.py

# Integration tests
python3 test_periodic_health_check_integration.py

# Both should output "ALL TESTS PASSED ✓"
```

### Monitor in Production
Watch for these log messages:
```
Periodic sync health check triggered
  phase: SYNCED
  local_height: 100
  time_since_progress: 35.2
  peers: 5

Cleared peer backoffs for periodic health check
  cleared_peers: 3
```

## Benefits

### Before Fix
```
Node starts → syncs to height 100 → enters SYNCED phase
Network produces blocks 101, 102, 103...
Peer heights stale / announcements missed
All peers in backoff
→ NODE STUCK AT HEIGHT 100 FOREVER ❌
```

### After Fix
```
Node starts → syncs to height 100 → enters SYNCED phase
Network produces blocks 101, 102, 103...
30 seconds pass with no progress
→ Periodic health check triggers
→ Clears peer backoffs
→ Requests headers from peers
→ Discovers new blocks 101+
→ NODE CONTINUES SYNCING ✅
```

### Key Improvements
- ✅ Prevents permanent sync stoppage
- ✅ Automatic recovery from stale states
- ✅ Works even if announcements missed
- ✅ Minimal overhead (30s intervals)
- ✅ No duplicate requests
- ✅ Configurable
- ✅ Safe and backward compatible

## Performance Impact
- **CPU**: Negligible (one condition check per sync loop tick, actual work only every 30s)
- **Memory**: None
- **Network**: Minimal (one getheaders request every 30s when idle)
- **Latency**: None (only triggers when already idle)

## Code Review
- ✅ All review comments addressed
- ✅ Configuration constant added for maintainability
- ✅ Clear comments explaining logic
- ✅ Comprehensive test documentation
- ✅ Python syntax validated
- ✅ Ready for deployment

## Deployment
- **No configuration changes required**
- **No database migrations needed**
- **Takes effect immediately**
- **Safe to deploy to production**

## Related Fixes
This complements previous sync improvements:
- `FIX_SUMMARY_CONTINUOUS_SYNC.md` - at_tip_but_behind check
- `NETWORK_HEIGHT_PROPAGATION_FIX.md` - Multi-hop height propagation
- This fix - Periodic health check for missed states

Together, these ensure nodes:
1. Stay synced when target height updates (at_tip_but_behind)
2. Learn about network height from peers-of-peers (multi-hop)
3. **Recover automatically when everything else fails (periodic check)** ← THIS FIX

## Conclusion
This fix ensures nodes continue syncing indefinitely by adding a safety net that periodically verifies sync state and recovers from any stuck conditions, even when block announcements are missed and peer information is stale.

The periodic health check is the "last line of defense" that guarantees nodes stay synchronized with the network.
