# Visual Before/After: Peer Count Display Fix

## Problem Scenario (from bug report)

User runs `animica node status` and sees confusing information:

```
Peers: total=1 inbound=0 outbound=1
Peers (live):
  1. 3802a2c1b2239040371133cac38a68adfa9459bc5f02bb9eee0d126332e00f70 (3.12.224.189:59086) [connected] inbound last_seen=2026-01-22 17:57:32Z
```

Then tries to mine:
```
$ animica miner mine-blocks --address anim1xxx --count 1
Warning: Block template unavailable (insufficient_peers (connected: 0, required: 1). Try: 'animica peer bootstrap' to connect to peers, or set ANIMICA_MINING_MIN_PEERS=0 for local development.)
Warning: No blocks were mined (may have failed)
```

**User questions:**
- Why does it say "inbound=0" when there's an inbound peer in the list?
- Why does it say the peer is "[connected]" but mining says "connected: 0"?
- What should I do to make mining work?

---

## After Fix

With this PR, the user now sees:

```
Peers: total=1 (connected=0, handshaking=1)
  Inbound: 1, Outbound: 0
Peers (live):
  1. 3802a2c1b2239040371133cac38a68adfa9459bc5f02bb9eee0d126332e00f70 (3.12.224.189:59086) [handshaking] inbound identity_ok=False last_seen=2026-01-22 17:57:32Z
```

Then tries to mine:
```
$ animica miner mine-blocks --address anim1xxx --count 1
Warning: Block template unavailable (insufficient_peers (connected: 0, handshaking: 1, required: 1). Try: 'animica peer bootstrap' to connect to peers, or set ANIMICA_MINING_MIN_PEERS=0 for local development.)
Warning: No blocks were mined (may have failed)
```

**User understanding:**
- ✅ "There's 1 peer total, but it's still handshaking (not fully connected yet)"
- ✅ "The inbound count is correctly shown as 1"
- ✅ "Mining needs a fully connected peer, which I don't have yet"
- ✅ "I need to wait for identity validation to complete, or use ANIMICA_MINING_MIN_PEERS=0"

---

## Key Improvements

### 1. Connected vs Handshaking Breakdown

**Before:** `Peers: total=1 inbound=0 outbound=1`  
**After:** `Peers: total=1 (connected=0, handshaking=1)`

This immediately shows:
- How many peers are in the handshake process
- How many are fully connected and usable for mining

### 2. Accurate Inbound/Outbound Counts

**Before:** Showed "inbound=0" but peer list showed an inbound peer  
**After:** Shows correct "Inbound: 1, Outbound: 0"

Note: The "outbound=1" in the old display was likely from bootstrap_bonus adding a phantom peer.

### 3. Peer Status Accuracy

**Before:** Peer shown as `[connected]` even though not identity-validated  
**After:** Peer shown as `[handshaking] identity_ok=False`

This accurately reflects the peer state.

### 4. Mining Error Context

**Before:** `insufficient_peers (connected: 0, required: 1)`  
**After:** `insufficient_peers (connected: 0, handshaking: 1, required: 1)`

Adding the handshaking count helps users understand:
- There ARE peers, they're just not ready yet
- This is a temporary state, not a fundamental connection problem

---

## Technical Details

### What is "handshaking"?

A peer goes through these states:
1. **DIALING** - TCP connection being established
2. **HANDSHAKING** - Protocol handshake in progress (HELLO/HELLO_ACK exchange)
3. **CONNECTED** - Handshake complete, identity validated

For mining to work, peers must be in **CONNECTED** state with `identity_ok=True`.

### Why does identity validation fail/delay?

Common reasons:
- Chain ID mismatch (peer on different network)
- Genesis hash mismatch (different chain)
- Network parameters mismatch (different configuration)
- Handshake timeout (slow network)
- Protocol version incompatibility

The `identity_ok=False` debug info helps diagnose which stage failed.

### Why count handshaking peers in total?

Previous fix ensured `peers_total` includes handshaking peers because:
- Sync needs to know connection attempts are in progress
- Without this, sync reports "no_peers_connected" and never progresses
- Shows the node is actively trying to connect

But mining requires FULLY CONNECTED peers for security:
- Need validated genesis hash to ensure mining on correct chain
- Need validated chain ID to prevent mining invalid blocks
- Need stable connection for block propagation

---

## Example Scenarios

### Scenario 1: All Peers Connected
```
Peers: total=3 (connected=3)
  Inbound: 1, Outbound: 2
```
Mining will work ✅

### Scenario 2: Mix of Connected and Handshaking
```
Peers: total=3 (connected=2, handshaking=1)
  Inbound: 2, Outbound: 1
```
Mining will work ✅ (has 2 connected peers)

### Scenario 3: All Peers Handshaking (Bug Report Scenario)
```
Peers: total=1 (connected=0, handshaking=1)
  Inbound: 1, Outbound: 0
```
Mining will fail ❌ (no connected peers yet)

User should:
- Wait for handshake to complete
- Check logs for handshake errors
- Or set `ANIMICA_MINING_MIN_PEERS=0` for local development

### Scenario 4: No Peers
```
Peers: total=0 (connected=0)
  Inbound: 0, Outbound: 0
```
Mining will fail ❌ (no peers at all)

User should:
- Run `animica peer bootstrap` to connect to bootstrap peers
- Check firewall/network settings
- Or set `ANIMICA_MINING_MIN_PEERS=0` for local development

---

## Testing

Run the test to verify display logic:
```bash
python python/animica/cli/tests/test_node_peer_count_display.py
```

Output:
```
✓ All tests passed!
Node status now clearly shows connected vs handshaking peer breakdown
```

---

## Related Issues

- Previous PR that added handshaking peers to `peers_total` (needed for sync)
- Bootstrap bonus confusion (phantom outbound peer)
- Timing issues between separate RPC calls for status and peer list

## Future Improvements

1. **Unified snapshot**: Get peer counts and peer list from same snapshot
2. **Bootstrap bonus clarification**: Show separately or remove from user display
3. **Real-time status**: Show peer state transitions as they happen
4. **Better handshake diagnostics**: Show why identity validation is failing
