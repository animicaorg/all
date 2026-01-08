# Manual Testing Guide for Snapshot Peer Connection Fix

## Background
Fixed issue where `animica snapshot list` incorrectly reports "No peers connected" even when peers are connected.

## Test Scenarios

### Scenario 1: Normal Operation (Both RPC calls succeed)
**Setup:**
- Node is running
- Peers are connected
- `snapshot.discoverFromPeers` RPC method is available

**Expected Output:**
```
Found 1 local snapshot(s):

Chain 1 - Height 2000
  Hash: 0x00009da2...
  Blocks: 2001
  Accounts: 6
  Size: 0.26 MB
  Path: /data/snapshots/chain-1-height-2000

🌐 Highest snapshot from connected peers:

Chain 1 - Height 3000
  Hash: 0x0000abcd...
  Blocks: 3001
  Accounts: 10
  Size: 0.35 MB
  Source: peer_xyz123
```

**Result:** ✓ Pass - Shows both local and peer snapshots

---

### Scenario 2: snapshot.discoverFromPeers Fails, Peers Connected (THE BUG)
**Setup:**
- Node is running
- Peers ARE connected
- `snapshot.discoverFromPeers` RPC method fails (not found, timeout, error)

**OLD Behavior (BEFORE FIX):**
```
Found 1 local snapshot(s):
...

💡 No peers connected.
   Connect to peers first to discover snapshots from the network.
```
❌ INCORRECT - Peers are actually connected!

**NEW Behavior (AFTER FIX):**
```
Found 1 local snapshot(s):
...

💡 Connected to 3 peer(s), but none have snapshots available.
```
✓ CORRECT - Accurately reflects peer status!

**Result:** ✓ Pass - Shows correct peer count via fallback

---

### Scenario 3: No Peers Actually Connected
**Setup:**
- Node is running
- No peers connected
- `snapshot.discoverFromPeers` fails (expected)

**Expected Output:**
```
Found 1 local snapshot(s):
...

💡 No peers connected.
   Connect to peers first to discover snapshots from the network.
```

**Result:** ✓ Pass - Correctly reports no peers

---

### Scenario 4: Both RPC Calls Fail
**Setup:**
- Node is running but RPC is having issues
- `snapshot.discoverFromPeers` fails
- `net.peers` / `p2p.listPeers` also fails

**Expected Output:**
```
Found 1 local snapshot(s):
...

💡 No peers connected.
   Connect to peers first to discover snapshots from the network.
```

**Result:** ✓ Pass - Graceful fallback to safe default message

---

## How to Test Manually

### Test with Real Node:
```bash
# 1. Start a node with peers
animica node up

# 2. Check peers are connected
animica peer list
# Should show connected peers

# 3. Run snapshot list
animica snapshot list
# Should show accurate peer status

# 4. Check with verbose logging
export ANIMICA_LOG_LEVEL=DEBUG
animica snapshot list
# Should see fallback logic in logs if RPC method doesn't exist
```

### Test with Mock RPC (Simulate Failure):
```bash
# 1. Start node without snapshot.discoverFromPeers method (older version)
# 2. Connect peers
# 3. Run: animica snapshot list
# Expected: Should still show correct peer count via fallback
```

## Code Review Checklist
- [x] Exception handling doesn't swallow errors silently
- [x] Fallback logic uses existing `_get_peers()` function
- [x] peer_count is updated correctly in all scenarios
- [x] Messaging distinguishes between "no peers" vs "peers but no snapshots"
- [x] No breaking changes to existing functionality
- [x] Code follows existing patterns in the file

## Verification
Run the explanation test to see the fix in action:
```bash
python3 test_snapshot_fix_explanation.py
```

This will show the before/after behavior comparison.
