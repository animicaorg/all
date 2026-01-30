# Sync Stall Fix - PR Summary

## Issue
Nodes getting permanently stuck at ~99% sync (e.g., 7468/7520 blocks) with:
- All peers returning "duplicate" headers
- Infinite peer rotation without progress
- Gap between local head and last matched ancestor (>1000 blocks)

## Root Cause
Infinite loop in duplicate header handling:
```
Peer returns headers → All known locally → Mark as duplicate → 
Increase locator depth → Penalize peer → Rotate to next peer → Repeat
```

When ALL peers have the same (correct) chain, this never terminates.

## Solution
Two recovery mechanisms triggered after stalled > 20 seconds:

### 1. All-Peers-Duplicate Recovery
When no peers remain after trying all:
- Reset locator depth hint to 0
- Clear duplicate_headers error state
- Clear peer backoffs for duplicate_headers
- Allows fresh retry with detailed locators

### 2. Extended-Stall Reset  
When receiving duplicates while stalled:
- Reset depth instead of increasing
- Clear error state for fresh retry
- Don't penalize peer (may be correct)
- Try with more detailed locator

## Files Changed
- `p2p/node/p2p_service.py` - Core fix (2 locations, ~70 lines)
- `test_sync_duplicate_recovery.py` - Unit tests (4 scenarios)
- `test_sync_duplicate_edge_cases.py` - Edge case tests (8 scenarios)
- `SYNC_DUPLICATE_RECOVERY_SUMMARY.md` - Technical documentation
- `SYNC_DUPLICATE_RECOVERY_TESTING.md` - Testing guide

## Test Results
✅ All 12 test scenarios pass:
- Recovery triggers correctly when stalled
- Normal operation preserved when not stalled
- Edge cases handled safely
- Backoff clearing is selective

## Deployment Impact
- **Backward Compatible:** No protocol changes
- **Configuration:** Uses existing `_sync_stall_timeout` (20s default)
- **Monitoring:** New log messages for recovery events
- **Rollback:** Safe to revert, no persistent state changes

## Success Criteria
Before: Stuck at 99.3% forever
After: Completes sync within 1-2 minutes of recovery trigger

## Next Steps
1. ✅ Code complete and tested
2. ✅ Documentation complete
3. ✅ Code review feedback addressed
4. ⏳ Manual testing on live network
5. ⏳ Monitor recovery logs in production
6. ⏳ Verify stall issues resolved

## Monitoring
Watch for these log messages:
```
"All peers returned duplicate headers with no progress; resetting sync state"
"Duplicate headers with extended stall; resetting locator depth"
```

Success indicators:
- Height increases after recovery log
- Sync completes to 100%
- No repeated stalls

## Additional Context
- Stall timeout: 20 seconds (default), configurable via `ANIMICA_SYNC_STALL_TIMEOUT_S`
- Duplicate threshold: 2 responses (default), configurable via `ANIMICA_P2P_DUPLICATE_HEADERS_THRESHOLD`
- Locator depth range: 0-64, increases by 8 per duplicate
- Recovery is automatic, no manual intervention required
