# Visual Guide: Genesis Sync Handshake Fix

## The Problem (Before Fix)

```
┌─────────────────────────────────────────────────────────────────┐
│                         Your Node                               │
│                                                                 │
│  State: Stuck at Genesis (Height 0)                           │
│  Sync Status: "no_fresh_peer_tips"                            │
│  Headers Received: 1 at height 1                              │
│  Headers Accepted: 0                                           │
└─────────────────────────────────────────────────────────────────┘
                             ▲
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
┌─────────▼───────┐  ┌──────▼──────┐  ┌───────▼────────┐
│ Peer 1          │  │ Peer 2      │  │ Peer 3         │
│ 144.126.x.x:30k │  │ 144.126.x.x │  │ 144.126.x.x    │
│                 │  │             │  │                │
│ State:          │  │ State:      │  │ State:         │
│ "handshaking"   │  │"handshaking"│  │"handshaking"   │
│                 │  │             │  │                │
│ ❌ Wrong        │  │ ❌ Wrong    │  │ ❌ Wrong       │
│ genesis_identity│  │ genesis_id  │  │ genesis_id     │
│                 │  │             │  │                │
│ ❌ Never        │  │ ❌ Never    │  │ ❌ Never       │
│ completes       │  │ completes   │  │ completes      │
│ handshake       │  │ handshake   │  │ handshake      │
└─────────────────┘  └─────────────┘  └────────────────┘

BUG: Peers with wrong genesis_identity only get WARNING logged
     They are NOT rejected → Stay connected in limbo
     hello_done never set → No fresh peer tips available
     Sync stuck forever 🔴
```

## What Was Happening (Code Flow - BEFORE)

```python
# In _handle_hello():

if bytes(hello.genesis_identity) != self._genesis_identity():
    # ❌ BUG: Only logs warning!
    self._log_handshake_mismatch(
        peer, 
        reason="genesis_identity_mismatch"
    )
    # ❌ Missing: No rejection!
    # ❌ Missing: No exception raised!
    # Execution continues...

# Later...
peer.identity_ok = True      # ❌ Set even for wrong identity!
peer.hello_done.set()        # ❌ But never actually completes

# Result:
# - Peer stays connected but in broken state
# - hello_done.is_set() returns False
# - _network_best_height() skips this peer
# - Sync sees "no_fresh_peer_tips"
```

## The Fix (After Fix)

```
┌─────────────────────────────────────────────────────────────────┐
│                         Your Node                               │
│                                                                 │
│  State: Syncing! ✅                                            │
│  Sync Status: "SYNCING" or "SYNCED"                           │
│  Headers Received: Multiple                                    │
│  Headers Accepted: Multiple ✅                                 │
└─────────────────────────────────────────────────────────────────┘
                             ▲
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
┌─────────▼───────┐  ┌──────▼──────┐  ┌───────▼────────┐
│ Peer 1          │  │ Peer 4      │  │ Peer 5         │
│ 144.126.x.x:30k │  │ 82.66.x.x   │  │ 203.45.x.x     │
│                 │  │             │  │                │
│ ❌ REJECTED     │  │ ✅ State:   │  │ ✅ State:      │
│ Immediately!    │  │ "connected" │  │ "connected"    │
│                 │  │             │  │                │
│ (wrong genesis) │  │ ✅ Correct  │  │ ✅ Correct     │
│                 │  │ genesis_id  │  │ genesis_id     │
│ HelloAck:       │  │             │  │                │
│ accepted=False  │  │ ✅ hello_   │  │ ✅ hello_      │
│                 │  │ done=True   │  │ done=True      │
│ Disconnected    │  │             │  │                │
└─────────────────┘  │ Fresh tips  │  │ Fresh tips     │
                     │ available!  │  │ available!     │
                     └─────────────┘  └────────────────┘

✅ Incompatible peers rejected immediately
✅ Node finds compatible peers
✅ Handshakes complete properly
✅ Sync proceeds! 🟢
```

## What Happens Now (Code Flow - AFTER)

```python
# In _handle_hello():

if bytes(hello.genesis_identity) != self._genesis_identity():
    # ✅ Logs the mismatch
    self._log_handshake_mismatch(
        peer, 
        reason="genesis_identity_mismatch"
    )
    
    # ✅ NEW: Properly rejects the peer!
    await self._reject_handshake_mismatch(
        peer,
        reason="genesis_identity_mismatch",
        points=self._score_points["wrong_chain"]
    )
    # This sends HelloAck(accepted=False) and raises exception
    # Peer is immediately disconnected
    # Node tries other peers

# Result:
# ✅ Incompatible peer rejected immediately
# ✅ Node connects to compatible peers
# ✅ Those peers complete handshake properly
# ✅ hello_done.is_set() returns True for compatible peers
# ✅ _network_best_height() returns valid heights
# ✅ Sync proceeds from genesis to height 1+
```

## Timeline Comparison

### BEFORE FIX ❌
```
T+0s:   Node starts, tries to connect to peers
T+1s:   Connects to Peer 1 (wrong genesis_identity)
        └─> Handshake: ⚠️  WARNING logged, but peer accepted
T+2s:   Peer 1 state: "handshaking" (stuck)
T+5s:   Connects to Peer 2 (wrong genesis_identity)
        └─> Handshake: ⚠️  WARNING logged, but peer accepted
T+6s:   Peer 2 state: "handshaking" (stuck)
T+10s:  Connects to Peer 3 (wrong genesis_identity)
        └─> Handshake: ⚠️  WARNING logged, but peer accepted
T+11s:  Peer 3 state: "handshaking" (stuck)
T+30s:  Sync status: "no_fresh_peer_tips"
        └─> All peers stuck, no valid tips available
T+60s:  Still at genesis (height 0)
T+5m:   Still at genesis (height 0)
T+1h:   Still at genesis (height 0) 🔴 STUCK FOREVER
```

### AFTER FIX ✅
```
T+0s:   Node starts, tries to connect to peers
T+1s:   Connects to Peer 1 (wrong genesis_identity)
        └─> Handshake: ❌ REJECTED immediately
        └─> Peer disconnected
T+2s:   Connects to Peer 2 (wrong genesis_identity)
        └─> Handshake: ❌ REJECTED immediately
        └─> Peer disconnected
T+3s:   Connects to Peer 4 (correct genesis_identity)
        └─> Handshake: ✅ ACCEPTED
        └─> Peer state: "connected"
T+4s:   Fresh peer tips available!
T+5s:   Headers requested from Peer 4
T+6s:   Headers received and ACCEPTED ✅
T+7s:   Sync progressing: height 0 → 1 → 2 → ...
T+30s:  Fully synced! 🟢
```

## Key Metrics

### BEFORE FIX
- **last_headers_accepted_count**: 0 ❌
- **headers_accepted_total**: 0 ❌
- **peer_tips_fresh**: 0 ❌
- **sync_status_reason**: "no_fresh_peer_tips" ❌
- **Peers in "handshaking"**: 3 ❌
- **Peers "connected"**: 0 ❌

### AFTER FIX
- **last_headers_accepted_count**: >0 ✅
- **headers_accepted_total**: >0 ✅
- **peer_tips_fresh**: >0 ✅
- **sync_status_reason**: null (or "syncing") ✅
- **Peers in "handshaking"**: 0 ✅
- **Peers "connected"**: 1+ ✅

## Validation Logic Comparison

### BEFORE (Inconsistent)
```
chain_id mismatch        → ✅ Rejects peer
genesis_hash mismatch    → ✅ Rejects peer
protocol_version wrong   → ✅ Rejects peer
clock_skew too large     → ✅ Rejects peer
genesis_identity wrong   → ❌ Only warns (BUG!)
network_params_hash wrong→ ❌ Only warns (BUG!)
```

### AFTER (Consistent)
```
chain_id mismatch        → ✅ Rejects peer
genesis_hash mismatch    → ✅ Rejects peer
protocol_version wrong   → ✅ Rejects peer
clock_skew too large     → ✅ Rejects peer
genesis_identity wrong   → ✅ Rejects peer (FIXED!)
network_params_hash wrong→ ✅ Rejects peer (FIXED!)
```

## What To Do If You're Affected

### Symptoms You Might See:
```bash
$ animica node status
...
sync_status_reason: 'no_fresh_peer_tips'
Peers: 
  1. (handshaking) ...
  2. (handshaking) ...
  3. (handshaking) ...
last_headers_accepted_count: 0
```

### Solution:
1. **Update to this fix**
2. **Restart your node**
3. **Verify your configuration**:
   - Check `chain_id` matches target network
   - Check `genesis.json` matches target network
   - Check seed nodes point to correct network
4. **Monitor logs for**:
   - Peer rejection messages (now working correctly)
   - Successful peer connections
   - Headers being accepted
   - Sync progressing

### Expected After Fix:
```bash
$ animica node status
...
Sync status: SYNCING (or SYNCED)
Peers:
  1. connected (82.66.x.x) ✅
  2. connected (203.45.x.x) ✅
...
Highest head: 1234 (sync target)
Local head: 1234
last_headers_accepted_count: 42
```

## Summary

**🔴 Problem**: Incompatible peers accepted → stuck in handshaking → no sync

**🟢 Solution**: Incompatible peers rejected → node finds compatible peers → sync works

**✅ Result**: Your node can now properly reject incompatible peers and sync successfully!
