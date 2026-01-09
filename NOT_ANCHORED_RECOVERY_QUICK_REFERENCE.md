# Quick Reference: Not_Anchored Sync Recovery

## Problem Symptoms
```bash
animica debug sync-dump
```
Output shows:
- `Sync phase: HEADERS`
- `Last header error: not_anchored`
- `Last recovery: retry_blocks_new_peer (attempt 100+)`
- Node stuck at same height despite connected peers

## Solution Applied
Progressive 3-stage recovery mechanism:

### Recovery Stages

#### Stage 1: Backtracking (attempts 5-9)
**What it does**: Searches deeper in chain history to find common ancestor
**Indicator**: `last_recovery_action: backtrack_depth_N`
**Expected behavior**: Node requests headers from further back in history

#### Stage 2: Block Skipping (attempts 10-19)
**What it does**: Bypasses problematic block ranges
**Indicator**: `last_recovery_action: skip_range_X_to_Y`
**Expected behavior**: Node skips contentious blocks and syncs from beyond

#### Stage 3: Aggressive Recovery (attempts 20+)
**What it does**: Clears all state and forces peer rotation
**Indicator**: `last_recovery_action: aggressive_recovery_clear_and_rotate`
**Expected behavior**: Node clears queues and tries different peers

#### Timeout Recovery (continuous)
**What it does**: Clears stuck in-flight requests
**Indicator**: `last_recovery_action: timeout_clear_inflight_headers`
**Expected behavior**: Prevents indefinite waiting

## Monitoring

### Check Recovery Progress
```bash
# View detailed sync status
animica debug sync-dump

# Look for these fields:
# - last_recovery_action: Shows current recovery stage
# - recovery_attempts: Number of recovery attempts
# - sync_phase: Should eventually move past HEADERS
# - last_progress_at: Should update when recovery succeeds
```

### Expected Timeline
- **Seconds 0-30**: Normal retries
- **Seconds 30-90**: Backtracking attempts
- **Seconds 90-180**: Skip range attempts  
- **Seconds 180+**: Aggressive recovery
- **Progress**: Node should make progress within 3-5 minutes

### Success Indicators
✅ `sync_phase` transitions from `HEADERS` to `BLOCKS` or `SYNCING`
✅ `last_progress_at` timestamp updates
✅ Local head height increases
✅ `last_header_error` clears

### If Still Stuck
After 30+ attempts (10+ minutes), node will reset to genesis as last resort.

To manually force recovery earlier:
```bash
animica sync force --clear-cache
```

## Configuration Tuning

### Faster Recovery (Aggressive)
```bash
# Reduce backoff times for faster attempts
export ANIMICA_P2P_NOT_ANCHORED_BACKOFF=10.0
export ANIMICA_P2P_NOT_ANCHORED_BACKOFF_CAP=15.0
```

### More Patient Recovery (Conservative)
```bash
# Increase backoff for slower but more careful recovery
export ANIMICA_P2P_NOT_ANCHORED_BACKOFF=60.0
export ANIMICA_P2P_NOT_ANCHORED_BACKOFF_CAP=120.0
export ANIMICA_P2P_NOT_ANCHORED_RESET_THRESHOLD=50  # More attempts before reset
```

### Disable Auto-Reset (Debug)
```bash
# Prevent genesis reset for debugging
export ANIMICA_P2P_NOT_ANCHORED_RESET_HEIGHT=0
```

## Troubleshooting

### Node still stuck after 10 minutes
1. Check peer count: `animica peer list`
   - Need at least 1 healthy peer
2. Try manual sync: `animica sync force --clear-cache`
3. Check logs for network issues
4. Verify peers are on same network/chain

### Recovery keeps resetting to Stage 1
- This is normal if peers keep sending incompatible headers
- Check if majority of peers are on a fork
- Verify your genesis hash matches network

### Aggressive recovery triggers immediately
- Indicates recent prior stuck state
- Recovery state persists for 5 minutes
- Wait or restart node to reset state

## Log Interpretation

### Normal Recovery Log
```
[INFO] Stage 1 Recovery: Backtracking to find common ancestor
      backtrack_depth=1, anchor_height=6495, header_height=6496
[INFO] Received headers response count=100
[INFO] Stage 1 Recovery: Backtracking to find common ancestor
      backtrack_depth=2, anchor_height=6495, header_height=6496
[INFO] Header progress noted
```

### Skip Recovery Log
```
[WARN] Stage 2 Recovery: Skipping problematic block range
       skip_range_start=6496, skip_range_end=6596, removed_blocks=50
[INFO] Selected sync peer for blocks
```

### Aggressive Recovery Log
```
[WARN] Stage 3 Recovery: Aggressive state cleanup and peer rotation
       cleared_inflight_headers=1, cleared_inflight_blocks=0
[INFO] Block sync stall handled old_peer=peer1, new_peer=peer2
```

## Quick Fixes

### Clear stuck state
```bash
# Force clear and retry
animica sync force --clear-cache

# Or restart node
systemctl restart animica  # or your node service
```

### Add more peers
```bash
# Bootstrap from seed nodes
animica peer bootstrap

# Or manually add peers
animica peer add <peer_multiaddr>
```

### Check network connectivity
```bash
# Verify RPC is working
animica node status

# Check P2P connectivity
animica network info
```

## Related Documentation
- Full Implementation: [NOT_ANCHORED_RECOVERY_IMPLEMENTATION.md](./NOT_ANCHORED_RECOVERY_IMPLEMENTATION.md)
- Sync Troubleshooting: [SYNC_TROUBLESHOOTING.md](./SYNC_TROUBLESHOOTING.md)
- Sync Stalls Guide: [SYNC_STALLS.md](./SYNC_STALLS.md)
