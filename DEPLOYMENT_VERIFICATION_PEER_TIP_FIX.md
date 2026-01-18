# Deployment and Verification Guide

## Fix: Node Sync Issue - Peer Tip Hello Fallback

This guide explains how to deploy and verify the fix for the sync issue where nodes got stuck with "no_fresh_peer_tips".

## Pre-Deployment Checklist

- [x] Code changes committed to branch `copilot/fix-node-sync-issue-yet-again`
- [x] All changes in `p2p/node/p2p_service.py`
- [x] Test created and validated (`test_peer_tip_hello_fallback.py`)
- [x] Code review completed
- [x] Summary documentation created
- [x] No breaking changes
- [x] No configuration changes required

## Deployment Steps

### 1. Update Repository

```bash
cd /path/to/animica
git fetch origin
git checkout copilot/fix-node-sync-issue-yet-again
git pull origin copilot/fix-node-sync-issue-yet-again
```

### 2. Restart Node

**Option A: Using systemd service**
```bash
sudo systemctl restart animica
```

**Option B: Using manual restart**
```bash
# Stop the node
animica stop

# Start the node
animica start
```

**Option C: Docker deployment**
```bash
docker-compose down
docker-compose up -d
```

### 3. Wait for Node Initialization

Give the node 10-30 seconds to:
- Initialize P2P connections
- Complete peer handshakes
- Begin sync process

## Verification Steps

### Step 1: Check Node Status

```bash
animica node status
```

**Expected Output Changes:**

**Before Fix:**
```
peer_tips_total: 0
peer_tips_fresh: 0
peer_tips_stale: 0
sync_status_reason: 'no_fresh_peer_tips'
best_remote_height: None
```

**After Fix:**
```
peer_tips_total: 1          # Or more, depending on peer count
peer_tips_fresh: 1          # At least 1 with connected peer
peer_tips_stale: 0
sync_status_reason: 'behind' or 'synchronized'
best_remote_height: 100     # Actual network height
```

### Step 2: Check Logs for Fallback Usage

```bash
# Check for hello fallback messages
grep "Using peer hello head_height as fallback" logs/animica.log

# Check for freshness fallback
grep "Peer counted as fresh using hello age" logs/animica.log

# Check for auto-poll triggers
grep "No network best height available - polling peer heads" logs/animica.log
```

**Expected:** You should see these log messages if fallback logic is being used.

### Step 3: Monitor Sync Progress

```bash
# Watch sync status in real-time
watch -n 5 'animica node status | grep -A 10 "Sync status"'
```

**Expected Behavior:**
- `head_height` should increase steadily
- `sync_status_reason` should change from "behind" to "synchronized" eventually
- `peer_tips_fresh` should remain > 0

### Step 4: Check Diagnostic Logs

If issues persist, check diagnostic logs:

```bash
# Look for peer filtering diagnostics
grep "No peers passed tip freshness filters" logs/animica.log

# Check for chain mismatch issues
grep "Peer filtered due to chain mismatch" logs/animica.log
```

**If you see these messages:**
- Check chain_id configuration
- Verify genesis file matches network
- Ensure network_magic is correct

## Troubleshooting

### Issue: Still showing "no_fresh_peer_tips"

**Possible Causes:**
1. **No peers connected**
   - Check: `animica network peers`
   - Solution: Verify P2P port is open, check seed nodes

2. **Chain ID mismatch**
   - Check logs for "Peer filtered due to chain mismatch"
   - Solution: Verify `ANIMICA_CHAIN_ID` environment variable matches network

3. **Genesis mismatch**
   - Check logs for "genesis_mismatch" errors
   - Solution: Ensure genesis file matches network genesis

4. **Identity issues**
   - Check logs for "identity_ok" false messages
   - Solution: Verify peer handshake is completing

### Issue: Peers not connecting

**Check P2P Status:**
```bash
animica network status
```

**Expected:**
```
P2P running: True
Peers: total=1+ inbound=X outbound=Y
```

**If no peers:**
```bash
# Check P2P port
netstat -tlnp | grep 30333

# Check seed nodes
grep "seed" ~/.animica/config.toml

# Check firewall
sudo ufw status | grep 30333
```

### Issue: Slow sync after fix

**Check Performance:**
```bash
# Monitor sync rate
animica node status | grep "head_height"
# Run multiple times to check if height increases
```

**If sync is slow but working:**
- This is expected! The fix gets sync STARTED
- Sync speed depends on:
  - Network bandwidth
  - Peer performance
  - Block validation time
  - Number of blocks to sync

## Rollback Procedure

If issues occur after deployment:

```bash
# Switch back to main branch
git checkout main

# Restart node
animica stop
animica start
```

**Note:** Rollback should only be needed if new issues are introduced. The original stuck state was already broken, so rollback would return to the broken state.

## Success Criteria

✅ Node syncs from genesis without manual intervention
✅ `peer_tips_fresh` > 0 with connected peers
✅ `best_remote_height` has valid value
✅ `sync_status_reason` NOT "no_fresh_peer_tips"
✅ Head height increases steadily
✅ No new errors in logs

## Performance Impact

- **CPU:** Negligible increase (one additional dict lookup per peer)
- **Memory:** Negligible increase (a few diagnostic counters)
- **Network:** Potential increase if auto-poll triggers frequently (5s min interval)
- **Latency:** No impact on sync latency

## Monitoring Recommendations

After deployment, monitor for 24-48 hours:

```bash
# Every hour, check:
animica node status | grep -E "(peer_tips_|best_remote_height|sync_status_reason)"

# Look for patterns:
# - peer_tips_fresh should stay > 0
# - best_remote_height should match network
# - sync_status_reason should not be "no_fresh_peer_tips"
```

**Set up alerts for:**
- `peer_tips_total == 0` with `peers_total > 0` (indicates filtering issue)
- `sync_status_reason == "no_fresh_peer_tips"` for > 5 minutes
- `head_height` not increasing for > 10 minutes

## Related Documentation

- `FIX_PEER_TIP_HELLO_FALLBACK_SUMMARY.md` - Detailed technical explanation
- `FIX_NODE_SYNC_HIGHEST_HEIGHT.md` - Related network height fix
- `SYNC_STALL_FIX_SUMMARY.md` - General sync stall recovery
- `GENESIS_SYNC_FIX_SUMMARY.md` - Genesis-specific sync issues

## Support

If issues persist after deployment:
1. Collect logs: `animica node status > status.txt && cat logs/animica.log > debug.log`
2. Check diagnostics: Look for "No peers passed tip freshness filters" messages
3. Report issue with:
   - Node status output
   - Relevant log excerpts
   - Network configuration (chain_id, seeds, etc.)
   - Steps already attempted

## Summary

This fix should:
- **Eliminate** "no_fresh_peer_tips" stuck state with valid peers
- **Enable** automatic recovery from missing tip tracker data
- **Provide** clear diagnostics when peers are filtered
- **Require** no configuration changes or manual intervention

The fix is backward compatible and can be deployed with confidence.
