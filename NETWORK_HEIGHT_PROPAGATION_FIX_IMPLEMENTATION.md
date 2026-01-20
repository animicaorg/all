# Network Height Propagation Fix - Implementation Guide

## Problem Statement
Nodes were not properly updating their neighbors with new chain heights, causing:
- Premature sync stopping (nodes thinking they're at the highest height when they're not)
- Network-wide forks (different parts of the network having different views)
- Sync stalling at "weird heights" depending on peer topology

## Root Cause Analysis

### The Bug
The `_propagate_network_height_update()` function was using **HELLO messages** to propagate height updates:

```python
# OLD CODE (BROKEN)
async def _propagate_network_height_update(self, network_best_height: int):
    for peer in peers:
        if peer_network_best < network_best_height:
            await self._send_hello(peer)  # ❌ WRONG MESSAGE TYPE
```

### Why This Was Wrong
1. **HELLO messages are for handshake only**: They're sent once when a peer first connects
2. **Not designed for ongoing updates**: HELLO contains genesis hash, chain ID, protocol version, etc.
3. **Peers may ignore post-handshake HELLOs**: The protocol expects HELLO only during initial connection
4. **No guarantee of delivery**: Not designed for periodic/on-demand updates

## The Solution

### Use HEAD_STATUS Messages
HEAD_STATUS (message ID 0x0105) is the **correct message type** for ongoing height updates:

```python
# NEW CODE (FIXED)
async def _propagate_network_height_update(self, network_best_height: int):
    local_height, local_head_hash = self._local_head()
    
    head_status = HeadStatus(
        chain_id=self.chain_id,
        head_height=int(local_height or 0),
        head_hash=bytes(local_head_hash),
        timestamp_ms=int(time.time() * 1000),
        network_best_height=network_best_height,  # ✅ Multi-hop propagation
    )
    
    for peer in peers:
        if peer_chain_matches(peer):
            await self._send(peer, MsgID.HEAD_STATUS, head_status)  # ✅ CORRECT
```

### Key Improvements

#### 1. Correct Message Type
- ✅ HEAD_STATUS is lightweight (height, hash, timestamp, network_best)
- ✅ Designed for periodic/on-demand updates
- ✅ All peers expect and handle HEAD_STATUS after handshake

#### 2. Universal Broadcasting
- ✅ Sent to **ALL peers** (both inbound and outbound connections)
- ✅ No filtering based on peer's current network_best
- ✅ Ensures consistent view across entire network

#### 3. Multi-hop Propagation
- ✅ Includes `network_best_height` field
- ✅ Nodes learn about heights from peers-of-peers
- ✅ Prevents topology-dependent sync issues

## How It Works

### Message Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Node A (height=50)                                          │
│   1. Receives HEAD_STATUS from Node B:                     │
│      - head_height: 100                                     │
│      - network_best_height: 200                             │
│                                                             │
│   2. Computes network_best_height:                         │
│      heights = [50, 100, 200]                              │
│      network_best = max(heights) = 200                     │
│                                                             │
│   3. Detects significant change (50 -> 200, delta > 10)   │
│                                                             │
│   4. Calls _propagate_network_height_update(200)          │
│                                                             │
│   5. Broadcasts HEAD_STATUS to all peers:                  │
│      - head_height: 50 (my current height)                 │
│      - network_best_height: 200 (highest I know about)     │
│                                                             │
│   6. All peers learn network_best = 200                    │
└─────────────────────────────────────────────────────────────┘
```

### Trigger Conditions

The `_propagate_network_height_update()` function is called when:
1. Network best height increases by more than 10 blocks
2. Called from `_head_watch_loop()` which runs every second
3. Ensures rapid propagation of height updates across the network

### Integration with Existing Heartbeat

HEAD_STATUS is also sent via periodic heartbeat:
- **Heartbeat interval**: 10-15 seconds (configurable via `ANIMICA_P2P_TX_REANNOUNCE_SEC`)
- **Freshness window**: 600 seconds (10 minutes)
- **Tolerance**: Up to 60 missed heartbeats before peer tips go stale

The fix adds **immediate propagation** on significant height changes, complementing the periodic heartbeat.

## Testing

### Unit Tests
**test_head_status_propagation.py:**
- ✅ HeadStatus message structure validation
- ✅ network_best_height field presence and optional nature
- ✅ Correct message ID (0x0105)
- ✅ Multi-hop propagation logic

### Integration Tests
**test_network_height_propagation_integration.py:**
- ✅ Three-node scenario (A → B → C)
- ✅ Node A learns about Node C via Node B
- ✅ HEAD_STATUS broadcast behavior
- ✅ Before/after comparison

### Existing Tests
**p2p/tests/test_head_status.py:**
- ✅ All existing tests continue to pass
- ✅ No breaking changes to HEAD_STATUS protocol

## Deployment Notes

### Backward Compatibility
- ✅ **No breaking changes**: Wire protocol unchanged
- ✅ **Gradual rollout**: Mix of old/new nodes will work
- ✅ **Optional field**: network_best_height is optional in HEAD_STATUS
- ✅ **No migration**: Nodes discover heights organically

### Performance Impact
- **Minimal**: HEAD_STATUS messages are lightweight (~100 bytes)
- **Network traffic**: Negligible increase (broadcasts on height changes > 10 blocks)
- **CPU**: No measurable impact
- **Memory**: Negligible (one int per peer)

### Monitoring

Look for these log messages to verify the fix is working:

```
# Successful propagation
DEBUG Propagated network best height via HEAD_STATUS 
  network_best_height=2000 local_height=50 peers=5

# Peer receiving update
INFO Received HEAD_STATUS update 
  remote=peer:1 height=100 network_best=200

# Sync continuing due to network best
INFO Local head behind network; continuing header sync (multi-hop propagation)
  local_height=50 remote_height=100 network_best_height=200 height_gap=150
```

## Verification Steps

### 1. Check Message Types
```bash
# In logs, verify HEAD_STATUS (not HELLO) is used for propagation
grep "Propagated network best height" /path/to/logs | grep HEAD_STATUS
```

### 2. Monitor Network Best Height
```bash
# Check that network_best_height is being tracked
grep "Network best height updated" /path/to/logs
```

### 3. Verify Multi-hop Propagation
```bash
# Should see nodes learning about heights from peers-of-peers
grep "continuing header sync (multi-hop propagation)" /path/to/logs
```

## Troubleshooting

### Symptom: Nodes still not syncing correctly
**Cause**: Heartbeat loop may not be running  
**Solution**: Check that `ANIMICA_P2P_TX_REANNOUNCE_SEC` is set > 0 (default is 15)

### Symptom: Network best height not updating
**Cause**: Peers may not have matching chain identity  
**Solution**: Verify `_peer_chain_matches()` returns true for peers

### Symptom: Old HELLO messages in logs
**Cause**: May be using old code version  
**Solution**: Verify latest code is deployed and service restarted

## Related Code Paths

### Height Propagation
- `_propagate_network_height_update()` - Immediate broadcast on height changes
- `_head_status_heartbeat()` - Periodic broadcast every 10-15s
- `_head_watch_loop()` - Monitors head changes, triggers propagation

### Height Reception
- `_handle_head_status()` - Processes incoming HEAD_STATUS messages
- `_update_peer_head()` - Updates peer tip tracker
- `_network_best_height()` - Computes highest known height across all peers

### Sync Decisions
- `_sync_once()` - Uses network_best_height to decide when to continue syncing
- `_compute_best_remote_info()` - Computes best remote height from fresh peer tips
- `sync_status_snapshot()` - Exposes sync status to RPC

## Summary

### Before Fix
❌ Used HELLO messages for ongoing updates  
❌ Inappropriate for post-handshake communication  
❌ Limited to direct peer connections  
❌ Caused premature sync stopping and forks  

### After Fix
✅ Uses HEAD_STATUS messages for ongoing updates  
✅ Correct protocol for periodic/on-demand broadcasts  
✅ Multi-hop propagation across entire network  
✅ Prevents sync stopping and forking issues  

### Impact
🎯 Nodes stay synchronized even with complex topologies  
🎯 Network-wide awareness of highest chain height  
🎯 No more weird forks at arbitrary heights  
🎯 Backward compatible with existing nodes  
