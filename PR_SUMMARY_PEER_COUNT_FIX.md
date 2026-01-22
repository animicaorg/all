# PR Summary: Fix Peer Count Display Discrepancy

## Problem Statement

User reported that `animica node status` shows peers as connected, but mining fails with:
```
insufficient_peers (connected: 0, required: 1)
```

The node status showed:
```
Peers: total=1 inbound=0 outbound=1
```

But the peer list showed:
```
1. 3802a2c1... [connected] inbound
```

This created confusion about why mining was blocked when peers appeared to be present.

## Solution Overview

This PR improves the peer count display to clearly distinguish between:
- **Connected peers** (identity-validated, usable for mining)
- **Handshaking peers** (still in connection/validation process)

## Changes Made

### 1. Enhanced Node Status Display (`python/animica/cli/node.py`)

**Before:**
```
Peers: total=1 inbound=0 outbound=1
```

**After:**
```
Peers: total=1 (connected=0, handshaking=1)
  Inbound: 1, Outbound: 0
```

Changes:
- Added connected/handshaking breakdown in parentheses
- Moved inbound/outbound to secondary line
- Extract counts from `peers_connected` and `peers_handshaking` fields

### 2. Improved Mining Error Messages (`rpc/methods/miner.py`)

**Before:**
```
insufficient_peers (connected: 0, required: 1)
```

**After:**
```
insufficient_peers (connected: 0, handshaking: 1, required: 1)
```

Changes:
- Include handshaking peer count when present
- Refactored to reduce code duplication
- Helps users understand the actual state

### 3. Debug Info for Peers (`python/animica/cli/node.py`)

**Before:**
```
1. abc123... (addr) [connected] inbound
```

**After:**
```
1. abc123... (addr) [handshaking] inbound identity_ok=False
```

Changes:
- Show `identity_ok` status for handshaking peers
- Helps diagnose why peers aren't becoming connected
- Accurate status labels ("handshaking" vs "connected")

### 4. Tests (`python/animica/cli/tests/test_node_peer_count_display.py`)

Added comprehensive tests for:
- Peer count display with handshaking peers
- Peer count display without handshaking peers
- Zero connected peers scenario (the bug case)
- Mining error message formatting

All tests pass successfully.

### 5. Documentation

- **PEER_COUNT_DISPLAY_FIX.md** - Technical details and background
- **PEER_COUNT_VISUAL_BEFORE_AFTER.md** - Visual examples and scenarios

## Technical Background

### Peer States

Peers go through these states:
1. **DIALING** - TCP connection being established
2. **HANDSHAKING** - Protocol handshake in progress
3. **CONNECTED** - Handshake complete, identity validated

### Peer Counts

The system tracks two different counts:

- **`peers_total`** - All active sessions (includes handshaking)
  - Used by sync to know connection attempts are in progress
  - Prevents "no_peers_connected" deadlock at genesis

- **`peers_connected`** - Only CONNECTED peers with `identity_ok=True`
  - Used by mining to ensure network connectivity
  - Requires validated chain_id and genesis_hash

### Why This Matters

Mining requires connected peers because:
- Need validated genesis hash (ensure mining on correct chain)
- Need validated chain_id (prevent invalid blocks)
- Need stable connection (for block propagation)

But sync needs to see handshaking peers because:
- Shows connection attempts are in progress
- Prevents getting stuck at genesis with "no peers"
- Allows sync to start once handshake completes

### The Bug

The old display showed `peers_total` as just "total", making it unclear that some peers might not be ready for mining. Combined with:
- Bootstrap bonus adding phantom +1 to outbound
- Separate RPC calls for status and peer list (timing differences)
- Peer status showing "connected" for any peer with peer_id

This created the appearance of working peers when actually they were still handshaking.

## Testing

### Unit Tests
```bash
python python/animica/cli/tests/test_node_peer_count_display.py
✓ All tests passed!
```

### Code Review
```
No review comments found.
```

### Security Scan
```
No code changes detected for languages that CodeQL can analyze
```

## Impact

### Before Fix
Users saw confusing information:
- "Peers: total=1 inbound=0 outbound=1" (inconsistent)
- "1. [connected] inbound" (misleading status)
- "connected: 0, required: 1" (no context)

Leading to questions like:
- "Why does it say inbound=0 when there's an inbound peer?"
- "Why is the peer [connected] but mining says connected: 0?"
- "What do I do to fix this?"

### After Fix
Users see clear information:
- "Peers: total=1 (connected=0, handshaking=1)" (clear breakdown)
- "1. [handshaking] inbound identity_ok=False" (accurate status)
- "connected: 0, handshaking: 1, required: 1" (full context)

Leading to understanding:
- ✅ There's 1 peer but it's still handshaking
- ✅ Mining needs a fully connected peer
- ✅ Need to wait for validation to complete
- ✅ Or use ANIMICA_MINING_MIN_PEERS=0 for local dev

## Files Changed

```
PEER_COUNT_DISPLAY_FIX.md                                | 178 +++++++
PEER_COUNT_VISUAL_BEFORE_AFTER.md                        | 193 +++++++
python/animica/cli/node.py                               |  30 +++-
python/animica/cli/tests/test_node_peer_count_display.py | 126 +++++
rpc/methods/miner.py                                     |   7 +-
5 files changed, 526 insertions(+), 8 deletions(-)
```

## Backward Compatibility

- ✅ No breaking changes
- ✅ Default behavior unchanged
- ✅ Additional information only
- ✅ Existing scripts continue to work

## Future Improvements

1. **Unified snapshot** - Get counts and list from same snapshot (reduce timing issues)
2. **Bootstrap bonus** - Show separately or remove from user display (reduce confusion)
3. **Real-time status** - Show peer state transitions as they happen
4. **Better diagnostics** - Show why identity validation is failing

## Conclusion

This PR significantly improves the user experience by making peer status information clear and actionable. Users can now immediately see why mining is blocked and what state their peers are in, reducing confusion and support burden.
