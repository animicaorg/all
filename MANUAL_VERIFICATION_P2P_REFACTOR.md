# Manual Verification Guide - P2P Refactor

This guide provides step-by-step instructions for manually verifying the P2P refactor on two VPS nodes running mainnet.

## Overview

The P2P refactor introduces:
- Deterministic handshake with 15s timeout
- Automatic peer tip exchange
- Consistent peer counting
- Fixed status schema (head_hash never None)

## Prerequisites

- Two VPS nodes with animica installed
- Network connectivity between nodes (port 30333 open)
- Fresh datadirs (or existing mainnet sync)

## Setup

### Node A (Seed Node)
```bash
# SSH to Node A
ssh user@nodeA

# Start the animica node
cd /path/to/animica
animica node up

# Note the IP address
ip addr show | grep inet
# Example: 203.0.113.10
```

### Node B (Connecting Node)
```bash
# SSH to Node B
ssh user@nodeB

# Start the animica node
cd /path/to/animica
animica node up
```

## Test 1: Initial State (Before Connection)

### On Both Nodes
```bash
# Check peer list
animica peer list
# Expected: Empty list (0 peers)

# Check sync status
animica sync status
# Expected:
# - peer_tips_total: 0
# - peer_tips_fresh: 0
# - peer_tips_stale: 0
# - connected_peers: 0

# Check node status
animica node status
# Expected:
# - head_hash: NOT None (should be genesis hash or current tip)
# - status_version: "2.0"
```

**Success Criteria**:
- ✅ head_hash is NOT None (even at genesis)
- ✅ status_version is "2.0"
- ✅ No peers listed

## Test 2: Connect Nodes

### On Node B (Connect to Node A)
```bash
# Add Node A as peer (replace IP with actual Node A IP)
animica peer add 203.0.113.10:30333

# Wait 5 seconds
sleep 5

# Check peer list
animica peer list
```

**Expected Output**:
```
Peer: 203.0.113.10:30333
  Direction: outbound
  State: CONNECTED
  Peer ID: abc123...
  Identity OK: True
  Tip Height: 1000 (or current height)
```

**Success Criteria**:
- ✅ Peer appears in list within 5 seconds
- ✅ State transitions: DIALING → HANDSHAKING → CONNECTED
- ✅ Identity OK: True
- ✅ Tip Height: populated (not None)

### On Node A (Check Inbound)
```bash
# Check peer list
animica peer list
```

**Expected Output**:
```
Peer: [Node B IP]:random_port
  Direction: inbound
  State: CONNECTED
  Peer ID: def456...
  Identity OK: True
  Tip Height: 1000 (or current height)
```

**Success Criteria**:
- ✅ Inbound peer appears
- ✅ State: CONNECTED
- ✅ Both nodes see each other

## Test 3: Verify Peer Tips Exchange

### On Both Nodes
```bash
# Check sync status
animica sync status
```

**Expected Output**:
```json
{
  "status_version": "2.0",
  "connected_peers": 1,
  "peer_tips_total": 1,
  "peer_tips_fresh": 1,
  "peer_tips_stale": 0,
  "best_remote_peer": "203.0.113.10:30333",
  "best_remote_height": 1000,
  "best_remote_hash": "0xabc123...",
  "head_hash": "0xdef456..."
}
```

**Success Criteria**:
- ✅ connected_peers: 1
- ✅ peer_tips_fresh: 1 (within 20s of connection)
- ✅ best_remote_peer: NOT "target_fallback" (real peer address)
- ✅ best_remote_height: populated
- ✅ head_hash: NOT None

## Test 4: Verify Handshake Timeout (Negative Test)

This tests that stuck handshakes fail after 15s.

### On Node B
```bash
# Try to connect to a non-existent peer (intentional failure)
animica peer add 198.51.100.99:30333

# Wait 20 seconds
sleep 20

# Check peer list
animica peer list
```

**Expected Output**:
- Peer should NOT appear (or appear as FAILED)
- No permanent stuck peers in HANDSHAKING state

**Success Criteria**:
- ✅ Handshake times out within 15-20s
- ✅ No peers stuck in HANDSHAKING forever

## Test 5: Verify Tip Polling

This tests that tips are refreshed periodically.

### Setup
1. Let both nodes stay connected for 60 seconds
2. Monitor sync status every 15 seconds

### On Node A
```bash
# Watch sync status (check every 15s)
watch -n 15 "animica sync status | grep peer_tips"
```

**Expected Behavior**:
- Tips should stay fresh (peer_tips_fresh: 1)
- Every 30s, TipManager polls if tip is >30s old
- peer_tips_stale should remain 0 (tips refreshed)

**Success Criteria**:
- ✅ peer_tips_fresh stays >= 1
- ✅ peer_tips_stale stays 0 or low
- ✅ No permanent stale tips

## Test 6: Verify Status Schema Consistency

### On Both Nodes
```bash
# Check all status commands return consistent peer counts
animica peer list | grep -c "Peer:"
animica sync status | grep connected_peers
animica node status | grep peer_count
```

**Expected Output**:
- All three commands should agree on peer count
- Example: 1 peer connected → all commands show 1

**Success Criteria**:
- ✅ Peer counts consistent across commands
- ✅ No "connected: 0" while showing handshaking peers
- ✅ No "total: 1" while "connected: 0"

## Test 7: Verify Mining & Sync (Partial)

**Note**: Full block propagation requires Phase 6 (gossip), which is optional. This tests infrastructure readiness.

### On Node A
```bash
# Mine a block
animica miner mine-blocks --count 1 --address <your_address>

# Check new height
animica node status | grep head_height
# Example: head_height: 1001 (increased by 1)
```

### On Node B (After 60 seconds)
```bash
# Check if Node B sees the new tip
animica sync status | grep best_remote_height
# Expected: best_remote_height should eventually update to 1001

# Note: This may take 30-60s due to polling interval
# Full propagation requires Phase 6 (gossip)
```

**Success Criteria**:
- ✅ Node A shows new height immediately
- ⚠️ Node B sees new tip within 60s (via polling, not instant gossip)
- 📝 Instant propagation requires Phase 6 (future work)

## Test 8: Verify Error Handling

### Test Chain ID Mismatch (Requires Test Network)
If you have two nodes on different networks:
```bash
# Node A on mainnet (chain_id=1)
# Node B on testnet (chain_id=2)

# Try to connect
animica peer add <other_node>

# Expected: Connection fails with identity error
```

**Expected Log Output**:
```
WARNING: Handshake identity failed (session_id=..., reason=chain_id_mismatch, expected=1, got=2)
```

**Success Criteria**:
- ✅ Connection refused with clear error
- ✅ Peer state: FAILED
- ✅ Identity OK: False

## Monitoring Logs

### Successful Handshake Logs
```bash
# View recent logs
tail -f /path/to/animica/logs/node.log | grep -E "(handshake|peer tip)"
```

**Expected Patterns**:
```
INFO: Starting handshake (session_id=abc123, remote=203.0.113.10:30333, direction=outbound)
INFO: Handshake hello received (session_id=abc123, peer_id=def456, version=1.0)
INFO: Handshake identity validated (session_id=abc123, chain_id=1, genesis_match=True)
INFO: Peer tip updated (session_id=abc123, height=1000, age_s=0.1)
```

### Tip Polling Logs
```bash
# View tip polling activity
tail -f /path/to/animica/logs/node.log | grep "Polling peer tips"
```

**Expected Patterns** (every 30s):
```
INFO: Polling peer tips (session_count=1, stale_count=0)
```

### Timeout Logs
```bash
# View timeout activity
tail -f /path/to/animica/logs/node.log | grep "timeout"
```

**Expected Patterns** (if handshake stuck):
```
WARNING: Handshake timeout (session_id=abc123, duration_s=16.2, timeout_s=15.0)
```

## Troubleshooting

### Problem: Peer Stuck in HANDSHAKING
**Symptoms**: Peer list shows state=HANDSHAKING for >15s

**Check**:
```bash
# View recent handshake logs
grep "handshake" /path/to/animica/logs/node.log | tail -20
```

**Expected**: Should see timeout warning after 15s
**Solution**: Wait for timeout to trigger (automatic), or restart node

### Problem: peer_tips_total=0 After 60s
**Symptoms**: No peer tips recorded despite connected peers

**Check**:
```bash
# View tip update logs
grep "tip updated" /path/to/animica/logs/node.log | tail -20
```

**Expected**: Should see tip updates within 20s of connection
**Solution**: Check if HeadStatus messages are being sent/received

### Problem: best_remote_peer=None with Connected Peers
**Symptoms**: sync status shows no best peer despite connections

**Check**:
```bash
# Check tip freshness
animica sync status | grep -E "(peer_tips_|best_remote)"
```

**Expected**: peer_tips_fresh should be >0
**Possible Cause**: Tips are stale (>600s old)
**Solution**: Tips will refresh automatically via polling (30s interval)

### Problem: Peer Counts Don't Match
**Symptoms**: `animica peer list` shows 1 peer, but `sync status` shows 0

**Check**:
```bash
# Check peer states
animica peer list
```

**Expected**: Peer should be state=CONNECTED, identity_ok=True
**Possible Cause**: Peer is HANDSHAKING or identity_ok=False
**Solution**: Wait for handshake to complete or check chain_id match

## Success Checklist

After completing all tests, verify:

- [ ] **Test 1**: Initial state shows head_hash NOT None
- [ ] **Test 2**: Nodes connect within 15s (DIALING → CONNECTED)
- [ ] **Test 3**: Tips exchanged (peer_tips_fresh=1)
- [ ] **Test 4**: Stuck handshakes timeout (no forever HANDSHAKING)
- [ ] **Test 5**: Tips refresh every 30s (peer_tips_fresh stays >0)
- [ ] **Test 6**: Peer counts consistent across commands
- [ ] **Test 7**: Mining updates head (infrastructure ready)
- [ ] **Test 8**: Chain ID mismatch rejected (if tested)

## Expected Behavior Summary

| Metric | Before Refactor | After Refactor |
|--------|----------------|----------------|
| Handshake timeout | Never | 15s |
| Peer tips | 0 (never requested) | >0 within 20s |
| Tip freshness | Stale forever | Refreshed every 30s |
| Peer count consistency | Inconsistent | Consistent |
| head_hash at genesis | None | Genesis hash |
| best_remote_peer | "target_fallback" | Real peer address |
| Status schema | Truncated | Always complete |

## Reporting Issues

If any test fails, report:
1. Which test failed
2. Expected vs actual output
3. Relevant log snippets
4. Node versions and chain IDs
5. Network configuration (inbound/outbound ports)

## Rollback

If issues arise, rollback by:
```bash
# Stop node
animica node down

# Checkout previous version
git checkout <previous_commit>

# Restart node
animica node up
```

No data migration needed (state is in memory).

## Next Steps

After successful verification:
1. Monitor for 24 hours
2. Check handshake success rate
3. Verify no peer count drops
4. Consider Phase 6 (block gossip) for instant propagation

---

*Manual verification complete. Report results to the team.*
