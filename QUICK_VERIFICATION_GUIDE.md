# Quick Verification Guide: not_anchored Sync Stall Fix

## Problem Solved
Permanent sync stalls when all peers fail checkpoint anchor validation (error: `not_anchored`).

## What Changed
Added fallback logic to `_select_block_peer()` to ignore `not_anchored` backoff when no eligible peers exist.

## How to Verify

### 1. Check Current Stall (Before Fix)
```bash
animica debug sync-dump
```

**Expected output (stuck node):**
```
Sync phase:       STALLED
In-flight:        headers=1 blocks=0
Stall reason:     not_anchored
Last block error:  not_anchored
Last recovery:    watchdog_requeue (attempt 1)
```

### 2. Deploy Fix
Deploy the changes from this PR to the stuck node.

### 3. Wait for Automatic Recovery
Wait 10-30 seconds. The node should automatically:
1. Retry peer selection with `ignore_backoff_reason="not_anchored"`
2. Select a peer despite the backoff
3. Clear `_sync_block_stalled_reason`
4. Resume block requests
5. Start syncing

### 4. Verify Recovery (After Fix)
```bash
animica debug sync-dump
```

**Expected output (recovered node):**
```
Sync phase:       BLOCKS (or SYNCED)
In-flight:        headers=0 blocks=5
Stall reason:     (none)
```

**Additional verification:**
- Local head height should be increasing
- No manual `animica sync force` needed
- Node should reach network height automatically

## Key Changes

**File:** `p2p/node/p2p_service.py`

**Function:** `_select_block_peer()` (lines 9438-9449)

**Code added:**
```python
eligible, _ = self._eligible_block_peers()
# Fallback: if no peers are eligible due to not_anchored backoff, retry ignoring it
# This prevents permanent stalls when all peers temporarily fail checkpoint anchor validation
if not eligible:
    eligible, _ = self._eligible_block_peers(ignore_backoff_reason="not_anchored")
```

This matches the existing pattern from `_select_sync_peer()` (line 9378).

## What to Monitor

### Success Indicators
✅ Node automatically recovers from `not_anchored` stalls
✅ No manual `animica sync force` needed
✅ Sync completes to network height
✅ No increase in peer penalties or bans

### Warning Signs (if any)
⚠️ Increased retries to peers with `not_anchored` status
⚠️ Excessive log messages about peer selection

If warning signs appear, check:
- Checkpoint validation logic
- Network connectivity to peers
- Peer diversity (need multiple peers from different providers)

## Rollback Plan

If issues arise, revert with:
```bash
git revert 52b3bfe3 b5fe2ddc f95f607c
```

This restores the original behavior (permanent stalls requiring manual intervention).

## Expected Outcome

**Before Fix:**
- Node stalls permanently
- User must manually run `animica sync force`
- Poor user experience

**After Fix:**
- Node automatically recovers
- No manual intervention needed
- Improved reliability and uptime

## Technical Details

See `SYNC_STALL_NOT_ANCHORED_FIX.md` for complete analysis, including:
- Detailed root cause explanation
- Code flow diagrams
- Testing procedures
- Monitoring recommendations
