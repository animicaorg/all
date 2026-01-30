# Sync Duplicate Recovery - Visual Flow

## Before Fix: Infinite Loop

```
┌─────────────────────────────────────────────────────────────┐
│ Node Stuck at Height 7468, Network at 7520                  │
│ Last Matched Ancestor: 6436                                 │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Build Locator (depth=0)                                     │
│ [7468, 7467, ..., 7456, 7440, 7408, ..., 6436, ..., 0]     │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Peer 1: Request headers with locator                        │
│ Returns: [6437, 6438, ..., 7468] (100+ headers)            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Check: all_known? YES (all heights exist locally)          │
│ Action: Mark as DUPLICATE                                   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Duplicate count >= threshold (2)                            │
│ Action: depth=0→8, penalize peer, rotate                   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Peer 2: Request with deeper locator (depth=8)              │
│ [7468, ..., 7408, 7280, 7024, ..., 6436, 0]               │
│ Returns: Same headers (duplicate)                           │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ depth=8→16, rotate to Peer 3                               │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Peer 3: depth=16→24, rotate to Peer 4                      │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Continue rotating... depth=24→32→40→48→56→64 (capped)      │
│ All 20 peers tried, all return duplicates                   │
│ Back to Peer 1: Still duplicates!                          │
│ ⚠️  INFINITE LOOP - NEVER MAKES PROGRESS ⚠️                │
└─────────────────────────────────────────────────────────────┘
```

## After Fix: Recovery Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Node Stuck at Height 7468, Network at 7520                  │
│ Last Matched Ancestor: 6436                                 │
│ Tried all 20 peers, all return duplicates                   │
│ Stalled for >20 seconds                                     │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 🔧 RECOVERY TRIGGER #1: All Peers Tried                    │
│                                                              │
│ Condition: len(tried_peers) >= eligible_count               │
│           AND error == "duplicate_headers"                  │
│           AND stalled > 20s                                  │
│                                                              │
│ Action:                                                      │
│ • Reset locator depth: 64→0 ✓                              │
│ • Clear error state ✓                                       │
│ • Clear peer backoffs ✓                                     │
│ • Clear duplicate tracking ✓                                │
│                                                              │
│ Log: "All peers returned duplicates; resetting state"       │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Next Sync Cycle: Try Again With Fresh State                │
│ Build Locator (depth=0 - most detailed)                     │
│ [7468, 7467, 7466, ..., 7458, 7456, 7454, ...]            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Peer 1: Request headers (no backoff, no penalty)           │
│ Returns: Headers starting from 6437                         │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Check: all_known? YES (still duplicate)                     │
│ But: Stalled >20s AND depth>0                              │
│                                                              │
│ 🔧 RECOVERY TRIGGER #2: Extended Stall Reset               │
│                                                              │
│ Action:                                                      │
│ • Reset depth: 0→0 (already reset) ✓                       │
│ • Clear error state ✓                                       │
│ • DON'T penalize peer (may be correct) ✓                   │
│ • Rotate to try different approach ✓                        │
│                                                              │
│ Log: "Duplicate with extended stall; resetting depth"       │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Alternative path: Block import detects gap                  │
│ Fetches missing blocks despite "duplicate" headers          │
│ OR: Reorg detection triggers fork resolution                │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ ✅ HEIGHT INCREASES: 7468→7469→...→7520                    │
│ ✅ SYNC COMPLETES TO 100%                                   │
│ Total recovery time: ~20-60 seconds                         │
└─────────────────────────────────────────────────────────────┘
```

## Key Differences

| Aspect | Before Fix | After Fix |
|--------|-----------|-----------|
| **Locator Depth** | Increases indefinitely (0→8→16→...→64) | Resets to 0 after stall |
| **Error State** | Persists as "duplicate_headers" | Cleared after recovery |
| **Peer Backoffs** | Accumulate, block retry | Cleared for fresh attempt |
| **Peer Penalties** | All peers penalized | Not penalized during recovery |
| **Outcome** | Stuck forever | Recovers in 20-60s |

## Recovery Detection

### Normal Operation (No Recovery)
```
Time: 0s    - Try Peer 1 → Duplicate → depth=0→8
Time: 5s    - Try Peer 2 → Duplicate → depth=8→16
Time: 10s   - Try Peer 3 → Headers accepted! ✓
           - Normal sync continues
```

### Stalled Operation (Recovery Triggers)
```
Time: 0s    - Try Peer 1 → Duplicate → depth=0→8
Time: 5s    - Try Peer 2 → Duplicate → depth=8→16
Time: 10s   - Try Peer 3 → Duplicate → depth=16→24
Time: 15s   - Try Peer 4 → Duplicate → depth=24→32
Time: 20s   - Try Peer 5 → Duplicate → depth=32→40
Time: 25s   - 🔧 STALL >20s DETECTED
           - All peers tried, all duplicates
           - 🔧 RECOVERY: Reset depth to 0
           - Clear all error state
           - Retry with fresh locators
Time: 30s   - Try Peer 1 again → Progress! ✓
```

## Monitoring Tips

### Good Recovery (Working)
```
[INFO] All peers returned duplicate headers with no progress; resetting sync state
[DEBUG] Cleared duplicate_headers error state
[DEBUG] Reset locator depth hint: 48→0
[INFO] Sync cycle: head_height=7469 (progress!)
[INFO] Sync cycle: head_height=7470
... (continues to 7520)
```

### Still Stuck (Not Working)
```
[WARN] All peers returned duplicate headers with no progress; resetting sync state
[INFO] Sync cycle: head_height=7468 (no change)
[WARN] All peers returned duplicate headers with no progress; resetting sync state
[INFO] Sync cycle: head_height=7468 (still no change)
... (repeating pattern)
```

## Testing Checklist

- [ ] Normal sync works (no unnecessary resets)
- [ ] Recovery triggers after stall >20s
- [ ] Locator depth resets to 0
- [ ] Error state cleared
- [ ] Peer backoffs cleared
- [ ] Height increases after recovery
- [ ] Sync completes to 100%
- [ ] No infinite recovery loops
- [ ] Logs show recovery actions
- [ ] Manual `animica sync force` still works

## Rollback Plan

If issues occur:
```bash
git revert <commit-hash>
animica node restart
# Monitor: Sync should continue with old behavior
```

Old behavior will return:
- Infinite loop on all-peers-duplicate
- Manual intervention required
- But no data corruption or protocol issues
