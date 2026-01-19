# Genesis Sync Stall Fix - Verification Guide

## Changes Summary

This PR fixes the genesis sync stall issue where nodes couldn't progress from height 0 to height 1 due to inconsistent peer counting and tip tracking.

## Root Issues Fixed

### 1. Peer Count Inconsistency
**Problem:** Different CLI commands reported different peer counts:
- `animica sync force` showed "Connected peers: 1" 
- `animica node status` showed "Connected peers: 0"
- `animica peer list` showed peers in "handshaking" state forever

**Root Cause:** The `peer_count()` function counted all sessions with a `peer_id` (handshake complete), but didn't check if identity validation passed (chain_id match, genesis match, fork_id match, etc.). This meant:
- Peers that completed TCP handshake but failed identity checks were still counted
- Different code paths used different peer sets (some checked identity_ok, some didn't)
- Status field showed "connected" for peers that weren't fully validated

**Fix:**
- `peer_count()` now ONLY counts peers with `identity_ok=True`
- `peer_state_snapshot()` sets status based on identity validation:
  - `status: "connected"` = peer_id assigned AND identity_ok = True
  - `status: "handshaking"` = peer_id missing OR identity_ok = False

### 2. Peer Tips Not Appearing
**Problem:** Even when headers were fetched (`best_header_height: 1`), the status showed:
```
peer_tips_total: 0
peer_tips_fresh: 0
best_remote_height: null
```

**Root Cause:** Tips were only tracked if the periodic polling loop ran, but at genesis the node might not have had time to poll. The fallback to use hello message existed but might not have been working in all code paths.

**Fix:**
- Explicit initialization for genesis peers (height 0)
- Added `sync_wakeup.set()` immediately after handshake completion to trigger sync evaluation
- Added logging for tip initialization to aid debugging

## Files Changed

```
p2p/node/p2p_service.py              # Core P2P service
p2p/node/peer_registry.py            # Peer counting logic
p2p/tests/test_genesis_peer_count_consistency.py  # NEW tests
p2p/tests/test_peer_registry.py      # Updated tests
```

## Manual Verification Steps

### Test 1: Consistent Peer Counts

1. Reset and start node:
```bash
animica node reset
animica node up
```

2. Bootstrap peers:
```bash
animica sync force
# Note the "Connected peers after bootstrap: X" count
```

3. Check node status:
```bash
animica node status
# Verify "Connected peers: X" matches the sync force count
```

4. Check peer list:
```bash
animica peer list
# Count peers with status: "connected" (not "handshaking")
# This should match the peer count from steps 2 and 3
```

**Expected:** All three commands should report the SAME peer count.

### Test 2: Peer Tips Appear

1. Start node at genesis:
```bash
animica node reset
animica node up
```

2. Force sync:
```bash
animica sync force
```

3. Check sync status within 30 seconds:
```bash
animica sync status --verbose
```

**Expected output:**
```
peer_tips_total: >= 1
peer_tips_fresh: >= 1
best_remote_height: (some height >= 0)
best_remote_hash: (some hash)
sync_status_reason: null (or not "no_fresh_peer_tips")
```

### Test 3: Genesis to Height 1 Progression

1. Start fresh node:
```bash
animica node reset
animica node up
```

2. Wait up to 2 minutes and monitor:
```bash
watch -n 5 'animica node status | grep -E "(height|peer|sync)"'
```

**Expected:** 
- Within 2 minutes, height should progress from 0 to 1
- Sync should not get stuck in IDLE phase
- Should not show "no_fresh_peer_tips" if peers are connected

### Test 4: Peer State Transitions

1. Watch peer list during connection:
```bash
watch -n 2 'animica peer list'
```

2. In another terminal, connect to a peer:
```bash
animica peer add <address>
```

**Expected transition:**
```
1. status: "dialing"       (initial connection)
2. status: "handshaking"   (TCP connected, identity validation in progress)
3. status: "connected"     (handshake + identity validation complete)
```

Peers should NOT stay in "handshaking" for more than 3-5 seconds.

## Debugging Commands

If issues persist, use these commands to diagnose:

### Check peer details:
```bash
animica p2p doctor --json | jq '.peers[] | {addr, status, identity_ok}'
```

### Check sync diagnostics:
```bash
animica sync status --verbose
```

### Check logs:
```bash
docker logs animica-node 2>&1 | grep -E "(peer_tips|identity_ok|handshake|sync)"
```

## Success Criteria

All of the following must be true:

1. ✅ `peer_count` is consistent across sync force, node status, and peer list
2. ✅ Peers show `status: "connected"` only when `identity_ok: true`
3. ✅ `peer_tips_fresh >= 1` appears within 30 seconds of peer connection
4. ✅ Node progresses from genesis (height 0) to height 1 within 2 minutes
5. ✅ No peers stuck in "handshaking" state for more than 5 seconds
6. ✅ `sync_status_reason` does not show "no_fresh_peer_tips" when peers are connected

## Rollback Plan

If issues are found during manual testing:

```bash
git revert f3713e60..HEAD
git push origin copilot/fix-genesis-sync-issues --force
```

## Additional Notes

- The changes are backward compatible - existing peers will continue to work
- The changes only affect peer counting/status reporting, not the underlying P2P protocol
- All existing unit tests pass
- The logic for genesis sync (allowing height 0 peers, queueing blocks) was already correct and unchanged
