# Sync Stall Fix - Visual Explanation

## Problem: Nodes Getting Stuck During Sync

### Before the Fix

```
┌─────────────────────────────────────────────────────────────────┐
│                      SYNC LOOP TIMELINE                         │
└─────────────────────────────────────────────────────────────────┘

Time: 0s         5s         10s        15s        20s        25s
      │          │          │          │          │          │
      ▼          ▼          ▼          ▼          ▼          ▼

Headers: ████████████████████ (Complete - Height 15000)

Blocks:  ████░░░░░░░░ (Stuck at 5000)
            ▲
            │
            └─ Some blocks timeout/fail here
            └─ Inflight blocks exist
            └─ ❌ No more requests due to gating condition
            └─ ❌ Sync marked as "SYNCED" prematurely

Node Status: "SYNCED" ❌ (Actually at 5000, should be 15000)
```

#### What Went Wrong?

1. **Issue 1 - Inflight Gating:**
```python
# Line 7316 - The problematic condition
if network_best_height > local_height and not self._sync_inflight_blocks:
    await self._schedule_block_requests()
    #                                    ▲
    #                                    └─ This prevented requests!
```

**Timeline:**
```
T=0s:  Request blocks 1-100    → _sync_inflight_blocks = {1,2,3,...,100}
T=2s:  50 blocks downloaded     → _sync_inflight_blocks = {51,52,...,100}
T=5s:  Some blocks timeout      → _sync_inflight_blocks = {75,76,...,100} (stuck)
T=6s:  Try to request more?     → NO! (inflight check fails)
T=10s: Still waiting...         → _sync_inflight_blocks = {75,76,...,100}
T=∞:   Forever stuck!           → Node stuck at height ~74
```

2. **Issue 2 - Premature Completion:**
```python
# Line 7003 - Another problematic check
if self._sync_best_header.height <= local_height:
    self._sync_phase = "SYNCED"
    return  # ❌ Exits without checking block queue!
```

**Scenario:**
```
_sync_best_header.height = 15000  ← Headers synced
local_height = 5000               ← Only 5000 blocks downloaded
_sync_block_queue = [5001, 5002, ..., 15000]  ← 10k blocks queued!

Check: 15000 <= 5000? → False, so continue...
       *later in code*
Check: 15000 <= 15000? → True!
       self._sync_phase = "SYNCED"  ❌ Wrong! Blocks still pending
       return  ← Exits early
```

### After the Fix

```
┌─────────────────────────────────────────────────────────────────┐
│                      SYNC LOOP TIMELINE                         │
└─────────────────────────────────────────────────────────────────┘

Time: 0s         5s         10s        15s        20s        25s
      │          │          │          │          │          │
      ▼          ▼          ▼          ▼          ▼          ▼

Headers: ████████████████████ (Complete - Height 15000)

Blocks:  ████████████████████ (Complete - Height 15000) ✓
            ▲          ▲
            │          │
            │          └─ ✓ Continues requesting despite inflight
            └─ Some blocks timeout/fail here
            └─ ✓ More requests scheduled automatically
            └─ ✓ Queue drained completely

Node Status: "SYNCED" ✓ (Actually at 15000, correct!)
```

#### How It's Fixed

1. **Fix 1 - Remove Inflight Gating:**
```python
# Line 7316 - Fixed condition
if network_best_height > local_height:  # ✓ Removed inflight check
    await self._schedule_block_requests()
```

**New Timeline:**
```
T=0s:  Request blocks 1-100    → _sync_inflight_blocks = {1,2,3,...,100}
T=2s:  50 blocks downloaded     → _sync_inflight_blocks = {51,52,...,100}
T=3s:  Request blocks 101-200   → ✓ Can request more! (no gating)
T=5s:  Some blocks timeout      → _sync_inflight_blocks = {75,76,...,200}
T=6s:  Request blocks 201-300   → ✓ Continues requesting!
T=10s: Download continues        → _sync_inflight_blocks shrinking
T=25s: All blocks downloaded!   → _sync_inflight_blocks = {} ✓
```

2. **Fix 2 - Check Pending Blocks:**
```python
# Line 7003 - Fixed completion check
if (self._sync_best_header.height <= local_height 
    and not self._sync_block_queue          # ✓ Check queue
    and not self._sync_inflight_blocks):    # ✓ Check inflight
    self._sync_phase = "SYNCED"
elif self._sync_block_queue or self._sync_inflight_blocks:
    log.debug("Continuing sync for pending blocks")  # ✓ Continue!
    return
```

**New Scenario:**
```
_sync_best_header.height = 15000
local_height = 5000
_sync_block_queue = [5001, 5002, ..., 15000]
_sync_inflight_blocks = {5001, 5002, ..., 5100}

Check: height <= local AND no_queue AND no_inflight?
       15000 <= 5000 AND False AND False → False ✗
       
Check: has_queue OR has_inflight?
       True OR True → True ✓
       log.debug("Continuing sync for pending blocks")  ✓
       return  → Keeps sync loop running ✓

Later when blocks complete:
_sync_block_queue = []
_sync_inflight_blocks = {}
local_height = 15000

Check: height <= local AND no_queue AND no_inflight?
       15000 <= 15000 AND True AND True → True ✓
       self._sync_phase = "SYNCED"  ✓ Correct now!
```

## Side-by-Side Comparison

### Sync State Flow

#### Before Fix:
```
START
  │
  ├─ Fetch Headers ────────► Headers @ 15000 ✓
  │
  ├─ Queue Blocks ─────────► Queue: [1..15000]
  │
  ├─ Request 1-100 ────────► Inflight: [1..100]
  │
  ├─ Download 1-50 ────────► Inflight: [51..100]
  │
  ├─ Timeout on 75-100 ────► Inflight: [75..100] (stuck)
  │
  ├─ Try Request More ─────► ❌ BLOCKED (inflight exists)
  │
  ├─ Check Completion ─────► ❌ "SYNCED" (wrong!)
  │
  └─ STUCK @ Height 50 ────► ❌ Node stuck forever
```

#### After Fix:
```
START
  │
  ├─ Fetch Headers ────────► Headers @ 15000 ✓
  │
  ├─ Queue Blocks ─────────► Queue: [1..15000]
  │
  ├─ Request 1-100 ────────► Inflight: [1..100]
  │
  ├─ Download 1-50 ────────► Inflight: [51..100]
  │
  ├─ Request 101-200 ──────► ✓ CONTINUES (no gating)
  │                           Inflight: [51..200]
  │
  ├─ Timeout on 75-100 ────► Inflight: [51..74,101..200]
  │
  ├─ Request 75-100 ───────► ✓ Retry (queued again)
  │                           Inflight: [51..74,75..200]
  │
  ├─ Check Completion ─────► ✓ Has pending blocks
  │                           Continue sync!
  │
  ├─ Download continues ───► Queue drains...
  │
  ├─ All blocks done ──────► Queue: [], Inflight: []
  │
  ├─ Check Completion ─────► ✓ "SYNCED" (correct!)
  │
  └─ SUCCESS @ Height 15000 ► ✓ Fully synced!
```

## Code Changes Visualization

### Change 1: Remove Gating

```diff
  self._log_sync_cycle()
  if self._sync_block_stalled_reason is None:
      await self._schedule_block_requests()
+     # Continue requesting blocks if we're behind, regardless of inflight status
+     # This ensures sync continues even if some blocks are already being downloaded
      if (
          network_best_height is not None
          and best_block_height < int(network_best_height)
-         and not self._sync_inflight_blocks  # ❌ REMOVED
      ):
          await self._schedule_block_requests()
```

**Effect:**
- **Before:** Only requests if inflight is empty → Can get stuck
- **After:** Always requests if behind → Continuous progress ✓

### Change 2: Check Pending Blocks

```diff
                  )
              else:
+                 # Check if we still have pending block downloads before marking as synced
                  if (
                      self._sync_best_header is None
                      or self._sync_best_header.height <= local_height
-                 ):
+                 ) and not self._sync_block_queue and not self._sync_inflight_blocks:
                      self._sync_phase = "SYNCED" if local_height > 0 else "IDLE"
                      log.debug(
                          "Skipped header request: already at tip",
                          extra={
                              "remote": peer.remote,
                              "local_height": local_height,
                              "remote_height": remote_height,
                          },
                      )
                      return result
+                 elif self._sync_block_queue or self._sync_inflight_blocks:
+                     # We have pending blocks, continue to download them
+                     log.debug(
+                         "Continuing sync for pending blocks",
+                         extra={
+                             "remote": peer.remote,
+                             "local_height": local_height,
+                             "block_queue": len(self._sync_block_queue),
+                             "inflight_blocks": len(self._sync_inflight_blocks),
+                         },
+                     )
+                     # Skip header sync but continue with block downloads
+                     return result
```

**Effect:**
- **Before:** Exits early if headers at tip → Misses pending blocks
- **After:** Checks queue/inflight before exit → Completes sync ✓

## Summary

The fix ensures that:

1. ✅ Sync loop **continuously requests blocks** even when some are in-flight
2. ✅ Nodes **check for pending work** before marking sync as complete
3. ✅ Better **logging** for debugging sync state
4. ✅ Natural **recovery** from timeouts and transient failures

Result: **All nodes reach network height reliably** 🎉
