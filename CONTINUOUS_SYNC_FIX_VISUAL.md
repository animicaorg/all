# Visual Guide: Continuous Sync Fix

## The Problem: Race Condition in Sync Recovery

### Before Fix - Node Gets Stuck

```
┌─────────────────────────────────────────────────────────────────┐
│                      SYNC LOOP ITERATIONS                        │
└─────────────────────────────────────────────────────────────────┘

Iteration 1: Node reaches height 100
├─ local_height = 100
├─ target_height = 100
├─ _sync_phase = "TARGET_REACHED"
└─ ✅ Node successfully synced to target

                    [New block announced at height 101]

Iteration 2: Recovery logic triggers
├─ target_height updated to 101 (from block announcement)
├─ Recovery detects: 100 < 101 → at tip but behind
├─ Sets _sync_requested = True
├─ Changes _sync_phase to "SYNCING"
└─ Calls _sync_kick(aggressive=True)

Iteration 3: First sync attempt
├─ force_sync = _sync_requested = True ✅
├─ Calls _sync_once(force=True)
├─ Sync logic executes (bypasses early return)
├─ _sync_requested cleared to False
└─ ⚠️  Recovery condition not re-evaluated!

Iteration 4: THE BUG - Early return
├─ force_sync = False (no more _sync_requested) ❌
├─ Calls _sync_once(force=False)
├─ Check: local_height (100) >= target_height (101)? No
├─ Check: not force? Yes → EARLY RETURN
├─ _sync_phase = "TARGET_REACHED"
└─ ❌ SYNC STOPS - Node stuck at height 100!

Iteration 5+: Stuck forever
├─ force_sync = False
├─ Early return in _sync_once()
├─ Phase stays "TARGET_REACHED"
└─ 🔴 NODE NEVER SYNCS NEW BLOCKS
```

### After Fix - Continuous Syncing

```
┌─────────────────────────────────────────────────────────────────┐
│                      SYNC LOOP ITERATIONS                        │
└─────────────────────────────────────────────────────────────────┘

Iteration 1: Node reaches height 100
├─ local_height = 100
├─ target_height = 100
├─ _sync_phase = "TARGET_REACHED"
└─ ✅ Node successfully synced to target

                    [New block announced at height 101]

Iteration 2: Recovery logic triggers
├─ target_height updated to 101
├─ Recovery detects: 100 < 101 → at tip but behind
├─ Sets _sync_requested = True
├─ Changes _sync_phase to "SYNCING"
└─ Calls _sync_kick(aggressive=True)

Iteration 3: First sync attempt
├─ at_tip_but_behind = (phase="SYNCING" in targets) = False
├─ force_sync = _sync_requested = True ✅
├─ Calls _sync_once(force=True)
├─ Sync logic executes
├─ _sync_requested cleared to False
└─ ✅ Downloads block 101

Iteration 4: THE FIX - Continuous check
├─ Phase may revert to "TARGET_REACHED" (local=100, target=101)
├─ at_tip_but_behind = ✅
│   └─ phase in ("SYNCED", "TARGET_REACHED")? Yes
│   └─ 100 < 101? Yes
│   └─ no inflight? Yes
│   └─ = True
├─ force_sync = False or False or False or True = True ✅
├─ Calls _sync_once(force=True)
├─ Bypasses early return
└─ ✅ Continues syncing

Iteration 5: Block downloaded
├─ local_height = 101
├─ target_height = 101
├─ at_tip_but_behind = False (101 >= 101)
├─ force_sync = False
├─ _sync_phase = "TARGET_REACHED"
└─ ✅ At target again

                    [Another new block at height 102]

Iteration 6: Automatic resume
├─ target_height = 102
├─ at_tip_but_behind = (101 < 102) = True ✅
├─ force_sync = True ✅
└─ ✅ IMMEDIATELY RESUMES SYNC

Iteration 7+: Stays in sync forever
└─ 🟢 NODE CONTINUOUSLY SYNCS ALL NEW BLOCKS
```

## Key Difference

### Before Fix
```
force_sync = stalled or sync_force_always or _sync_requested
                                                 ↑
                              Cleared after first use - NOT PERSISTENT
```

### After Fix
```
at_tip_but_behind = (phase in ("SYNCED", "TARGET_REACHED") 
                     and height < target 
                     and no inflight)

force_sync = stalled or sync_force_always or _sync_requested or at_tip_but_behind
                                                                        ↑
                                                    CHECKED EVERY ITERATION - PERSISTENT
```

## Flow Diagram

```
                                    ┌─────────────────┐
                                    │  Sync Loop Tick │
                                    └────────┬────────┘
                                             │
                                             ▼
                              ┌──────────────────────────┐
                              │ Calculate at_tip_but_behind│
                              │   - Check phase           │
                              │   - Check heights         │
                              │   - Check inflight        │
                              └──────────┬───────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         │                               │
                     True│                           False│
                         ▼                               ▼
              ┌──────────────────┐         ┌────────────────────┐
              │ force_sync = TRUE│         │ Other conditions?  │
              │ (bypass check)   │         │ (stalled, etc.)    │
              └────────┬─────────┘         └─────────┬──────────┘
                       │                              │
                       └──────────────┬───────────────┘
                                      ▼
                          ┌───────────────────────┐
                          │ _sync_once(force=?)   │
                          └───────────┬───────────┘
                                      │
                      ┌───────────────┴────────────────┐
                      │                                │
              force=True│                      force=False│
                      ▼                                ▼
        ┌─────────────────────────┐    ┌──────────────────────────┐
        │ BYPASS early return     │    │ Check: height >= target? │
        │ Execute sync logic      │    │   Yes → early return      │
        │ Download blocks         │    │   No → continue sync      │
        └─────────────────────────┘    └──────────────────────────┘
                      │                                │
                      └────────────────┬───────────────┘
                                       ▼
                             ┌──────────────────┐
                             │ Blocks downloaded │
                             │ Continue loop     │
                             └──────────────────┘
```

## Summary

**Problem**: Recovery logic set `_sync_requested = True` but it got cleared, causing subsequent iterations to fail.

**Solution**: Added persistent `at_tip_but_behind` check that evaluates on every iteration, ensuring `force_sync = True` whenever node is at tip but behind target.

**Result**: Nodes now continuously sync new blocks without getting stuck! 🎉
