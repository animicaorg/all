# Fix: Peer Count Display Improvements

## Problem

Users were seeing confusing peer counts when running `animica node status`:
- Summary showed: "Peers: total=1 inbound=0 outbound=1"
- Peer list showed: "1. [connected] inbound" peer
- Mining failed with: "insufficient_peers (connected: 0, required: 1)"

This led to confusion where peers appeared to be connected but mining was still blocked.

## Root Cause

The peer counting system has multiple layers:

1. **`peers_total`** - Counts ALL active sessions including handshaking peers
   - Includes peers in DIALING, HANDSHAKING, and CONNECTED states
   - Necessary for sync to progress (fixed in previous PR)

2. **`peers_connected`** - Counts only CONNECTED and identity-validated peers
   - Requires both `state == "CONNECTED"` AND `identity_ok == True`
   - Used by mining to ensure node has real network connectivity

3. **Bootstrap bonus** - Adds phantom +1 to outbound count
   - Triggered when there's a recent successful bootstrap (within 600s)
   - Can make it appear there's a peer when there isn't

4. **Timing issues** - Status and peer list are separate RPC calls
   - Taken at different times
   - Peers can connect/disconnect between calls
   - Results in inconsistent displays

## Solution

### 1. Improved Node Status Display

**Before:**
```
Peers: total=1 inbound=0 outbound=1
```

**After:**
```
Peers: total=1 (connected=0, handshaking=1)
  Inbound: 0, Outbound: 1
```

This clearly shows:
- Total peer sessions
- How many are fully connected (identity-validated)
- How many are still handshaking
- Inbound/outbound breakdown

### 2. Enhanced Mining Error Message

**Before:**
```
insufficient_peers (connected: 0, required: 1)
```

**After (when handshaking peers exist):**
```
insufficient_peers (connected: 0, handshaking: 1, required: 1)
```

This helps users understand:
- Mining is blocked because no peers are fully connected yet
- There IS a peer, but it's still handshaking
- Once the handshake completes, mining will work

### 3. Debug Info for Handshaking Peers

For peers in handshaking state, the display now shows `identity_ok` status:
```
1. (handshaking) (192.168.1.1:30333) [handshaking] outbound identity_ok=False
```

This helps diagnose why a peer isn't transitioning to connected state.

## Technical Details

### Identity Validation

A peer requires these steps to be counted as "connected":
1. TCP connection established
2. Handshake protocol completed (HELLO/HELLO_ACK exchange)
3. Peer ID received
4. Identity validation passed:
   - Chain ID matches local node
   - Genesis hash matches local node
   - Network params hash matches
5. State transitions to CONNECTED

Only then is `identity_ok` set to `True` and the peer counted in `peers_connected`.

### Why Handshaking Peers Are Counted in Total

Previous fix (PR #XXX) ensured `peers_total` includes handshaking peers because:
- Sync needs to know peers are attempting to connect
- Without this, sync would report "no_peers_connected" and never progress
- Genesis blocks can't be fetched if sync thinks there are no peers

### Bootstrap Bonus

The `bootstrap_peer_bonus()` adds +1 to peer counts when:
- There was a successful bootstrap within last 600 seconds
- That peer is NOT currently in active sessions

This is intended to help sync progress, but can be confusing for users because:
- It makes it look like there's an outbound peer when there isn't
- The "phantom peer" isn't shown in the peer list
- Can cause "inbound=0 outbound=1" when the only real peer is inbound

**Future improvement**: Consider removing bootstrap_bonus from user-facing displays, or showing it separately as "bootstrap=1".

## Testing

Created test file: `python/animica/cli/tests/test_node_peer_count_display.py`

Tests verify:
1. Display shows connected vs handshaking breakdown
2. Simplified display when no handshaking peers
3. Correctly shows zero connected peers (the bug scenario)

Run with:
```bash
python python/animica/cli/tests/test_node_peer_count_display.py
```

## User Impact

### Before Fix

User sees this and is confused:
```
Peers: total=1 inbound=0 outbound=1
Peers (live):
  1. abc123... [connected] inbound

Mining: insufficient_peers (connected: 0, required: 1)
```

Questions:
- "Why does it say inbound=0 when there's an inbound peer?"
- "Why does it say the peer is [connected] but mining says connected: 0?"
- "What do I need to do to mine?"

### After Fix

User sees this and understands:
```
Peers: total=1 (connected=0, handshaking=1)
  Inbound: 0, Outbound: 1
Peers (live):
  1. abc123... [handshaking] inbound identity_ok=False

Mining: insufficient_peers (connected: 0, handshaking: 1, required: 1)
```

Understanding:
- "OK, there's 1 peer but it's still handshaking"
- "Mining needs a fully connected peer, not just handshaking"
- "I need to wait for the handshake to complete"
- "Or I can set ANIMICA_MINING_MIN_PEERS=0 for local development"

## Related Files

- `/home/runner/work/all/all/python/animica/cli/node.py` - Node status display
- `/home/runner/work/all/all/rpc/methods/miner.py` - Mining error messages
- `/home/runner/work/all/all/p2p/node/p2p_service_legacy.py` - Peer counting logic
- `/home/runner/work/all/all/p2p/node/peer_registry.py` - Peer state tracking

## Backward Compatibility

- No breaking changes
- Default behavior unchanged
- Additional information only added to displays
- Existing automation/scripts continue to work
