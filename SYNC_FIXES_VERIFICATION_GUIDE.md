# Manual Verification Guide for Sync Fixes

This guide helps verify the sync recovery improvements work correctly in a real environment.

## Prerequisites
- Node with the sync fixes deployed
- Access to node logs
- Ability to monitor sync status via RPC or CLI

## Test Scenarios

### Scenario 1: Large Gap Block Enqueue
**Goal**: Verify blocks are enqueued even with header gaps

**Steps**:
1. Start a node that is significantly behind (100+ blocks)
2. Monitor logs for: "Enqueuing block despite missing parent due to large gap"
3. Verify sync continues to make progress despite warning
4. Check that `gap_size` in log is > 10 (LARGE_GAP_THRESHOLD)

**Success Criteria**:
- Warning appears when expected
- Sync continues making progress
- No indefinite stall

### Scenario 2: Headers==Blocks Stall Recovery
**Goal**: Verify stall detection clears error states

**Steps**:
1. Wait for or create a headers==blocks situation (all peers at same height)
2. Wait for stall timeout (~10-15 seconds)
3. Monitor logs for: "Sync stalled: headers == blocks with no progress"
4. Check for: "Clearing header error state to retry sync"
5. Verify `_sync_kick(aggressive=True)` triggered

**Success Criteria**:
- Stall detected within expected timeout
- Error states cleared
- Aggressive peer rotation triggered
- Sync retries with different peers

### Scenario 3: Extended Stall Snapshot Recovery
**Goal**: Verify snapshot recovery triggers on extended stall

**Steps**:
1. Create or wait for a persistent headers==blocks stall (90+ seconds)
2. Monitor logs for: "Extended headers==blocks stall - considering snapshot recovery"
3. Verify snapshot recovery attempt starts
4. Check `stall_duration_s` >= `threshold_s` in logs

**Success Criteria**:
- Snapshot recovery triggered after 90s
- Recovery attempt logged
- Node eventually recovers via snapshot

### Scenario 4: Expired Block Re-queueing
**Goal**: Verify expired blocks are re-queued

**Steps**:
1. During active sync, monitor for: "Block request expired"
2. Check immediately after for: "Re-queued expired blocks for retry"
3. Verify `requeued_count` > 0
4. Confirm block eventually downloads successfully

**Success Criteria**:
- Expired blocks logged
- Re-queue count reported
- Blocks successfully downloaded on retry

### Scenario 5: Header Gap Diagnostics
**Goal**: Verify diagnostic logging for header gaps

**Steps**:
1. During sync with few headers available
2. Monitor for: "Few headers available despite being behind"
3. Check `available_headers` < 10 and `gap` > 5
4. Verify additional header requests triggered

**Success Criteria**:
- Warning appears when gap exists
- Includes useful diagnostics (gap, height, threshold)
- Triggers corrective action

## Log Patterns to Monitor

### Normal Operation
```
"Seeded block queue from headers" - Block queue populated
"Selected sync peer for blocks" - Peer selected
"Block sync progress" - Making progress
```

### Warning Conditions
```
"Enqueuing block despite missing parent due to large gap" - Expected during catch-up
"Few headers available despite being behind" - Need more headers
"Block request expired" - Timeout, should be retried
```

### Recovery Actions
```
"Clearing header error state to retry sync" - Error cleared
"Re-queued expired blocks for retry" - Blocks recovered
"Extended headers==blocks stall" - Snapshot recovery
"Block sync stall handled" - Peer rotation
```

## Metrics to Track

### Before Fix
- Frequency of indefinite stalls
- Average time to recover from stall
- Percentage of sync attempts that fail

### After Fix
- Should see decrease in indefinite stalls
- Faster recovery (<90s with snapshot backup)
- Higher sync success rate
- More informative logs for debugging

## Common Issues

### Issue: Too Many "Enqueuing despite missing parent"
**Diagnosis**: LARGE_GAP_THRESHOLD may be too low
**Action**: Consider increasing threshold or improving header sync

### Issue: Frequent Snapshot Recovery
**Diagnosis**: P2P sync not working well
**Action**: Check peer quality, network connectivity, or peer selection logic

### Issue: Blocks Still Getting "Lost"
**Diagnosis**: Re-queue logic not working
**Action**: Check logs for "Re-queued expired blocks" and investigate peers

### Issue: Headers Not Available
**Diagnosis**: Header sync stalled
**Action**: Check "Few headers available" warnings and header request logic

## Success Indicators

✅ Node syncs to tip without manual intervention
✅ Stalls self-recover within 90 seconds
✅ Logs provide clear diagnosis of issues
✅ Snapshot recovery used as last resort (not primary mechanism)
✅ Block queue stays populated when headers available

## Failure Indicators

❌ Indefinite stalls (>5 minutes) with no recovery
❌ Same stall repeats after manual restart
❌ Logs show errors but no recovery actions
❌ Snapshot recovery fails or loops
❌ Block queue empty when headers available

## Reporting Issues

When reporting issues, include:
1. Node logs with timestamps
2. Sync status before/during/after stall
3. Peer count and quality
4. Network conditions (latency, packet loss)
5. Specific log patterns from this guide
6. Time to recover (or if no recovery)

## Quick Verification Checklist

- [ ] Node syncs from genesis successfully
- [ ] Node recovers from network interruption
- [ ] Large gaps don't cause indefinite stalls
- [ ] Headers==blocks stall recovers automatically
- [ ] Extended stalls trigger snapshot recovery
- [ ] Expired requests are retried
- [ ] Logs provide useful diagnostics
- [ ] No manual intervention needed for normal stalls

If all items checked, sync fixes are working correctly!
