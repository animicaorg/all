# Genesis Sync Fix - Quick Verification Guide

## What Was Fixed
Node was getting stuck at genesis (height 0) with no way to recover when headers failed to anchor.

## Root Cause
- Genesis reset disabled → prevents loop ✓
- Ancestor reset impossible at genesis → no ancestor exists ✗
- Result: **Deadlock** when anchor fails repeatedly

## Solution
Added special recovery for genesis that triggers aggressive peer rotation and state clearing instead of reset.

## Quick Test

### 1. Test Genesis Recovery Logic
```bash
# Run unit tests
python test_genesis_reset_loop_fix.py
python test_genesis_sync_fixes.py
python test_genesis_sync_integration.py

# Expected: All tests pass
```

### 2. Test Real Node Sync
```bash
# Start fresh node at genesis
rm -rf ~/.animica/chain-*
animica node start

# Expected:
# - Node connects to peers within 10 seconds
# - Sync starts within 1-2 seconds
# - Progress past genesis within 30-60 seconds
# - No "Reset chain to genesis" messages in logs
```

### 3. Monitor Genesis Recovery
```bash
# Watch for recovery events
tail -f ~/.animica/logs/node.log | grep -i "genesis_not_anchored\|genesis_peer_rotation"

# Expected (if anchor fails):
# - "Genesis sync: cannot anchor headers after multiple attempts"
# - "Force peer refresh: genesis_not_anchored"
# - "Sync kick requested: genesis_not_anchored_recovery"
```

### 4. Verify No Reset Loop
```bash
# Check for any genesis reset attempts
tail -f ~/.animica/logs/node.log | grep -i "reset.*to.*genesis"

# Expected: NO output (genesis reset is completely disabled)
```

## Key Behaviors

### ✅ What Works Now
- Genesis sync starts automatically
- Anchor failures trigger recovery (not reset)
- Peer rotation happens automatically
- Sync progresses without manual intervention
- No deadlock at genesis
- No reset loop

### ✅ What's Preserved
- Genesis reset still disabled (no loop)
- Ancestor reset works for heights > 0
- All genesis optimizations (watchdog, ticks, rotation)
- Fork resolution for non-genesis heights

## Success Criteria

**✅ Node syncs past genesis**: Height increases from 0 to 1+ within 60 seconds  
**✅ No manual intervention**: Sync happens automatically  
**✅ No reset messages**: Genesis reset never triggers  
**✅ Recovery works**: Peer rotation happens when anchor fails  
**✅ Tests pass**: All 19 unit tests + integration tests pass

## Troubleshooting

### If Sync Still Stuck
1. **Check peer connections**: `animica peer list`
   - Need at least 1-2 connected peers
   
2. **Check logs for errors**: `tail -100 ~/.animica/logs/node.log | grep -i error`
   - Look for network, database, or peer issues
   
3. **Force sync kick**: `animica sync force --clear-cache`
   - Manually trigger sync if needed
   
4. **Check peer quality**: `animica debug sync-dump`
   - Verify peers have blocks (best_peer_height > 0)

### Common Issues
- **No peers**: Run `animica peer bootstrap` to connect to seed nodes
- **Bad peers**: All connected peers also at genesis - need to find peers with blocks
- **Network issues**: Check firewall, port forwarding, connectivity

## Performance Expectations

### Genesis Sync Timeline
- **0-10s**: Node starts, connects to peers
- **10-15s**: Sync kick triggers, headers requested
- **15-30s**: If anchor fails, recovery triggers
- **30-60s**: Should be past genesis (height > 0)

### Recovery Behavior
- **Trigger**: After 3 anchor failures
- **Actions**: Peer refresh + state clear + sync kick
- **Counter Reset**: Prevents infinite escalation
- **Retry**: Tries different peer automatically

## Files Changed
1. `p2p/node/p2p_service.py` - Genesis recovery logic (31 lines)
2. `test_genesis_reset_loop_fix.py` - Updated tests
3. `test_genesis_sync_integration.py` - New integration test
4. `GENESIS_SYNC_DEADLOCK_FIX.md` - Detailed documentation

## References
- Full documentation: `GENESIS_SYNC_DEADLOCK_FIX.md`
- Unit tests: `test_genesis_reset_loop_fix.py`, `test_genesis_sync_fixes.py`
- Integration test: `test_genesis_sync_integration.py`
