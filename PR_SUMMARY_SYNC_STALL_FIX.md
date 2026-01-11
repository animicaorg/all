# PR Summary: Fix Sync Stall Caused by Inflight Header Expiry Guard Condition

## Overview
This PR fixes a critical bug where nodes get stuck in HEADERS sync phase indefinitely with 1 inflight header request, blocking all sync operations.

## Problem
Nodes at tip (synced with peers) could have stuck header requests that never expire because:
1. A guard condition prevented expiry checks unless no progress was made for `_sync_request_timeout` seconds
2. At tip, blocks/other progress count as "progress", keeping the condition false
3. Stuck header requests never expire, blocking all future header requests
4. Watchdog recovery can't help (only tries snapshot recovery at attempt 4+, which has rate limits)

## Solution
Removed the unnecessary guard condition. The `_expire_inflight_headers()` function already:
- Returns early if no inflight requests exist (efficient)
- Checks request deadlines internally using `time.monotonic()` (accurate)
- Only expires truly timed-out requests (safe)

## Changes
1. **p2p/node/p2p_service.py** (5 lines)
   - Removed guard condition on expiry check
   - Added clear comments explaining the fix
   
2. **p2p/tests/test_sync_loop_behavior.py** (53 lines)
   - Added `test_inflight_header_expiry_at_tip` test
   - Verifies expiry works correctly when at tip
   
3. **Documentation** (243 lines)
   - `SYNC_STALL_INFLIGHT_HEADER_FIX.md` - Problem/solution details
   - `SYNC_STALL_FIX_VERIFICATION.md` - Testing and verification guide

## Testing
- ✅ New test added covering the at-tip scenario
- ✅ Existing test `test_inflight_header_expiry_requeues` still passes
- ✅ Code syntax validated
- ✅ Code review passed
- ⏳ Manual verification pending (requires production environment)

## Risk Assessment
**Low Risk**
- Minimal code change (5 lines in core, surgical modification)
- Removes constraint, doesn't add new behavior
- Internal function already handles edge cases
- Pattern matches existing code for `_expire_inflight_blocks()`
- Comprehensive test coverage added

## Impact
- Fixes nodes stuck indefinitely at tip with inflight headers
- Eliminates need for manual `animica sync force` intervention
- Improves sync reliability and uptime
- No performance impact (efficient internal checks)

## Verification Steps (Post-Deployment)
```bash
# On stuck node, check current state
animica debug sync-dump

# Expected: headers=1 persists indefinitely before fix
# Expected: headers=0 within 10-30 seconds after fix
```

## Rollback Plan
If needed, revert with:
```bash
git revert dddab31c 54c38625 503deddd 5a593353 8b143ced
```

## Related Issues
Fixes sync stall issue reported in production where nodes show:
- `Sync phase: HEADERS`
- `In-flight: headers=1 blocks=0`
- `Last recovery: watchdog_snapshot_recovery (attempt 0)`
- Node stuck indefinitely at same height as peers

## Code Review
All review comments addressed:
- ✅ Enhanced comments to explain 'at tip' scenario
- ✅ Improved test deadline calculation for clarity
- ✅ Removed extra blank lines for code density
