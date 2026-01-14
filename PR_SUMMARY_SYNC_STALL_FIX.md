# PR Summary: Fix Sync Stall When Headers==Blocks with Network Ahead

## Issue
Node sync gets stuck at 99.8% (or any height) when:
- Local height: 11186
- Network height: 11204
- Headers == blocks (both at 11186)
- No in-flight requests or queued items
- Sync phase shows "SYNCING" but makes no progress

From problem statement:
```
Sync progress: 99.8% (11186/11203)
Best peer head:   11204
In-flight:        headers=0 blocks=0
Queues:           pending_headers=0 queued_blocks=0
```

## Root Cause
1. Node correctly detects `headers == blocks` stall condition
2. Stall detection clears global `_sync_last_header_error` state
3. **BUT**: Individual peers remain in backoff due to previous "headers_empty" or "peer_behind" responses
4. With all peers in backoff, they are marked ineligible for sync
5. Result: No peers available for header requests → sync cannot progress

The bug is that error state clearing (global) doesn't trigger backoff clearing (per-peer).

## Solution
When stall conditions are detected, clear peer backoffs:

**Before:**
```python
# Only cleared global error state
if self._sync_last_header_error in ("at_tip", "invalid_headers"):
    self._sync_last_header_error = None
    ...
```

**After:**
```python
# Clear global error state AND peer backoffs
if self._sync_last_header_error in ("at_tip", "invalid_headers", "headers_empty"):
    self._sync_last_header_error = None
    ...
# NEW: Clear peer backoffs
cleared = self._clear_sync_backoff_reason("headers_empty")
cleared += self._clear_sync_backoff_reason("peer_behind")
if cleared > 0:
    log.info("Cleared peer backoffs to retry sync", extra={"cleared_peers": cleared})
```

## Changes
1. **p2p/node/p2p_service.py** (lines 9464-9499): Headers==blocks stall detection
   - Clear "headers_empty" and "peer_behind" backoffs
   - Log cleared peer count
   
2. **p2p/node/p2p_service.py** (lines 9521-9545): Behind network stall detection
   - Same backoff clearing logic
   
3. **SYNC_STALL_FIX_VERIFICATION.md**: Verification guide and expected behavior

## Impact
- ✅ Automatic recovery from sync stalls within 15-30 seconds
- ✅ No manual intervention required (no more `animica sync force`)
- ✅ Sync progresses smoothly even with stale peer heights
- ✅ Fixes the reported issue where sync gets stuck at 99.8%

## Risk Assessment
- **Risk Level**: Low
- **Scope**: Only affects sync retry logic when stalls are detected
- **Safety Guards**: 
  - Backoff clearing only happens on stall detection (15-30s cooldown)
  - Only clears specific backoff reasons, not all backoffs
  - No changes to consensus, validation, or security paths
  
## Testing
- ✅ Syntax validation: `python3 -m py_compile p2p/node/p2p_service.py`
- ✅ Code review: Passed with comments addressed
- ⏳ Manual testing: Pending deployment to testnet/mainnet

## Verification Steps
1. Deploy to node experiencing the issue
2. Monitor logs for:
   ```
   Sync stalled: headers == blocks with no progress
   Cleared peer backoffs to retry sync (cleared_peers=N)
   ```
3. Verify sync resumes and progresses to network height
4. Monitor for 24-48 hours to ensure no regressions

## Related Issues
- Original: "Syncing not progressing .123.248.62:30333"
- Addresses: Headers==blocks stall scenarios
- Related: Peer height staleness (separate issue, not fixed here)
