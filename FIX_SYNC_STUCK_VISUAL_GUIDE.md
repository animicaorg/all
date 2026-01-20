# Visual Guide: Sync Stuck Fix

## Problem Scenario

```
┌─────────────────────────────────────────────────────┐
│ Node at Genesis (height 0)                         │
│                                                     │
│ Status:                                             │
│   sync_status_reason: 'no_fresh_peer_tips' ❌      │
│   peer_tips_total: 0                               │
│   peer_tips_fresh: 0                               │
│   target_height: 1 ✓ (block known to exist)       │
│                                                     │
│ Peers:                                              │
│   - 3.133.122.91:30333 [pending/dialing]          │
│   - 82.66.161.84:30333 [pending/dialing]          │
│                                                     │
│ Problem: Cannot sync despite knowing blocks exist!  │
└─────────────────────────────────────────────────────┘
```

## Before Fix

```
┌─────────────────────────────────────────────────────┐
│ _compute_best_remote_info()                        │
├─────────────────────────────────────────────────────┤
│                                                     │
│ 1. Check all peers:                                │
│    ├─ Peer 1: hello_done=False ❌ SKIP            │
│    └─ Peer 2: hello_done=False ❌ SKIP            │
│                                                     │
│ 2. No peers with fresh tips                        │
│    └─ best_height = None                           │
│                                                     │
│ 3. Return (None, None, None, None)                 │
│                                                     │
├─────────────────────────────────────────────────────┤
│ RESULT:                                             │
│   best_remote_height = None ❌                     │
│   behind_by = None                                  │
│   sync_status_reason = "no_fresh_peer_tips"        │
│   → SYNC STUCK                                      │
└─────────────────────────────────────────────────────┘
```

## After Fix

```
┌─────────────────────────────────────────────────────┐
│ _compute_best_remote_info()                        │
├─────────────────────────────────────────────────────┤
│                                                     │
│ 1. Check all peers:                                │
│    ├─ Peer 1: hello_done=False ❌ SKIP            │
│    └─ Peer 2: hello_done=False ❌ SKIP            │
│                                                     │
│ 2. No peers with fresh tips                        │
│    └─ best_height = None                           │
│                                                     │
│ 3. 🆕 FALLBACK CHECK:                              │
│    ├─ target_height = 1 ✓                          │
│    ├─ target > 0 ✓                                 │
│    ├─ target ≤ 50M ✓                               │
│    └─ Use target as fallback! ✓                    │
│                                                     │
│ 4. Return (1, None, "target_fallback", 0.0)        │
│                                                     │
├─────────────────────────────────────────────────────┤
│ RESULT:                                             │
│   best_remote_height = 1 ✓                         │
│   behind_by = 1 - 0 = 1 ✓                          │
│   sync_status_reason = "behind_by_1_blocks"        │
│   → SYNC PROGRESSES ✓                              │
└─────────────────────────────────────────────────────┘
```

## Code Change

**File**: `p2p/node/p2p_service.py`
**Location**: Lines 12993-13018 (end of `_compute_best_remote_info()`)
**Size**: 26 lines added

```python
# FIX: Fallback to target_height when no fresh peer tips available
if best_height is None and self._sync_target_height is not None:
    target = int(self._sync_target_height)
    MAX_REASONABLE_HEIGHT = 50_000_000
    if target > 0 and target <= MAX_REASONABLE_HEIGHT:
        log.info("Using target_height as fallback...")
        return target, None, "target_fallback", 0.0
    elif target > MAX_REASONABLE_HEIGHT:
        log.warning("target_height exceeds reasonable bounds...")
```

## Impact Flow

```
┌──────────────────────┐
│ Peer Connection Fail │
│ (timeout, network)   │
└──────┬───────────────┘
       │
       ├─ Before: best_remote_height = None → STUCK ❌
       │
       └─ After:  best_remote_height = target_height ✓
                  │
                  ├─ Sync computes behind_by correctly
                  │
                  ├─ Sync requests headers for height 1
                  │
                  ├─ Headers accepted (if valid)
                  │
                  ├─ Blocks downloaded
                  │
                  └─ Sync progresses! ✓
```

## Safety Guarantees

```
┌─────────────────────────────────────────────────────┐
│ When Fallback Activates                            │
├─────────────────────────────────────────────────────┤
│ ✓ Only when NO peer tips available                 │
│ ✓ Only when target_height is set and > 0           │
│ ✓ Only when target_height ≤ MAX_REASONABLE_HEIGHT  │
│ ✓ Logged clearly for monitoring                    │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ When Real Peer Tips Available                      │
├─────────────────────────────────────────────────────┤
│ ✓ Fallback bypassed - peer tips used directly      │
│ ✓ No change to existing behavior                   │
│ ✓ Fresh peer data always preferred                 │
└─────────────────────────────────────────────────────┘
```

## Testing Coverage

```
┌─────────────────────────────────────────────────────┐
│ Unit Tests (test_sync_target_height_fallback.py)  │
├─────────────────────────────────────────────────────┤
│ ✓ No peers, no target → None                       │
│ ✓ No peers, target=1 → Use target                  │
│ ✓ Peers without handshakes → Use target            │
│ ✓ Fresh peer tips → Prefer peer over target        │
│ ✓ Target=0 → Not used (edge case)                  │
│ ✓ Target > 50M → Not used (bounds check)           │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ Verification (verify_target_height_fallback.py)    │
├─────────────────────────────────────────────────────┤
│ Simulates exact problem scenario:                  │
│ - Node at genesis                                   │
│ - target_height = 1                                 │
│ - Peers in dialing state                            │
│                                                     │
│ Result: ✓ best_remote_height=1, sync progresses    │
└─────────────────────────────────────────────────────┘
```

## Summary

**What Changed**: Single function in `p2p/node/p2p_service.py` (26 lines)
**How It Works**: Falls back to `target_height` when no peer tips available
**Why It's Safe**: Only activates in edge case, preserves existing behavior
**Result**: Sync resilient to temporary peer connection issues

This is a **surgical fix** that addresses a specific edge case without
changing core sync behavior or protocol semantics.
