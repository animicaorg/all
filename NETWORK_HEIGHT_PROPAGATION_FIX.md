# Network Height Propagation Fix - Technical Summary

## Problem Statement

Nodes were not syncing correctly and forking at weird heights because each node could only see the heights of its directly connected peers, not the heights of peers-of-peers. This caused:

1. **Premature sync stopping**: Nodes thought they were at the highest height when they weren't
2. **Network-wide forks**: Different parts of the network had different views of "highest height"
3. **Inconsistent behavior**: Sync would stop at "weird heights" depending on peer topology

## Root Cause

The `_network_best_height()` function in `p2p/node/p2p_service.py` only examined directly connected peers:

```python
# OLD CODE (Broken)
def _network_best_height(self) -> Optional[int]:
    heights: list[int] = []
    for peer in self._peers.values():
        # Only looks at peer.head_height
        heights.append(int((peer.hello or {}).get("head_height") or 0))
    return max(heights) if heights else None
```

### Example Failure Scenario

```
Network Topology:
  Node A (height=50) ← connected to → Node B (height=100)
  Node B (height=100) ← connected to → Node C (height=200)

Problem:
  - Node A can only see Node B's height (100)
  - Node A thinks network_best_height = 100
  - Node A stops syncing at 100
  - Node A never learns about Node C at height 200
  - Node A forks from the rest of the network
```

## Solution

### 1. Add Network Best Height to Wire Protocol

Modified `p2p/wire/messages.py` to include multi-hop height in Hello messages:

```python
@dataclass(frozen=True)
class Hello:
    # ... existing fields ...
    head_height: Height = 0  # Peer's own height
    head_hash: Hash32 = b""
    # NEW FIELD: Peer's view of network (includes peers-of-peers)
    network_best_height: Optional[Height] = None
```

### 2. Update Hello Message Construction

Modified `_send_hello()` in `p2p/node/p2p_service.py`:

```python
async def _send_hello(self, peer: _PeerState) -> None:
    # ... get our own height ...
    
    # Compute network best height: max of our height and what we've seen from peers
    network_best = self._network_best_height()
    if network_best is None or network_best < int(height or 0):
        network_best = int(height or 0)
    
    hello = Hello(
        # ... other fields ...
        head_height=height,
        network_best_height=network_best,  # NEW: Tell peer about wider network
    )
    await self._send(peer, MsgID.HELLO, hello)
```

### 3. Update Network Height Calculation

Modified `_network_best_height()` to consider both peer heights AND their network views:

```python
def _network_best_height(self) -> Optional[int]:
    """
    Compute the highest height we know about in the network.
    
    This considers:
    1. Direct peer heights (head_height)
    2. Peer's network views (network_best_height) - enabling multi-hop propagation
    """
    heights: list[int] = []
    for peer in self._peers.values():
        # Add peer's own head height
        peer_height = int((peer.hello or {}).get("head_height") or 0)
        if peer_height > 0:
            heights.append(peer_height)
        
        # NEW: Add peer's view of network best height (peers-of-peers)
        network_height = (peer.hello or {}).get("network_best_height")
        if network_height is not None:
            network_height = int(network_height)
            if network_height > 0:
                heights.append(network_height)
    
    return max(heights) if heights else None
```

### 4. Add Height Propagation Loop

Modified `_head_watch_loop()` to actively propagate height updates:

```python
async def _head_watch_loop(self) -> None:
    last_network_best = 0
    while self._running:
        # ... existing head monitoring ...
        
        # NEW: Propagate network best height updates
        current_network_best = self._network_best_height() or 0
        if current_network_best > last_network_best + 10:  # Significant change
            last_network_best = current_network_best
            log.debug("Network best height updated", 
                     extra={"network_best_height": current_network_best})
            await self._propagate_network_height_update(current_network_best)
```

### 5. More Aggressive Sync Behavior

Modified `_sync_once()` to continue syncing when network height is higher:

```python
# In sync loop
if remote_height <= local_height:
    network_best_height = self._network_best_height()
    if network_best_height is not None and int(network_best_height) > int(local_height):
        log.info(
            "Local head behind network; continuing header sync (multi-hop height propagation)",
            extra={
                "local_height": local_height,
                "remote_height": remote_height,
                "network_best_height": network_best_height,
                "height_gap": int(network_best_height) - int(local_height),
            },
        )
        # Continue syncing - don't stop even if peer's own height is lower
```

## How It Works Now

### Information Flow

```
Round 1: Initial state
  Node A (h=50, nb=50)
  Node B (h=100, nb=100)  
  Node C (h=200, nb=200)

Round 2: B learns from C
  Node A (h=50, nb=50)
  Node B (h=100, nb=200)  ← B now knows about C's 200
  Node C (h=200, nb=200)

Round 3: A learns from B
  Node A (h=50, nb=200)   ← A now knows about C's 200 via B!
  Node B (h=100, nb=200)
  Node C (h=200, nb=200)

Result: All nodes know network_best_height = 200
```

### Multi-Hop Propagation

```
Before Fix:
  A→B→C→D→E
  Each node only sees immediate neighbors
  Node A thinks network height = B's height
  
After Fix:
  A→B→C→D→E
  Heights propagate backwards through network
  Node A eventually learns about Node E's height
  All nodes converge on true network height
```

## Benefits

1. **No More Premature Sync Stopping**: Nodes continue syncing until true network height
2. **No More Weird Forks**: All nodes discover and sync to same height
3. **Better Network Topology Handling**: Works with any peer connection pattern
4. **Backward Compatible**: `network_best_height` is optional in Hello messages
5. **Observable**: Clear logging shows multi-hop propagation in action

## Testing

Created `test_network_height_propagation.py` with tests for:
- Hello message includes new field ✓
- Field is optional (backward compatibility) ✓
- Multi-hop height calculation works ✓
- Three-hop propagation scenario works ✓

All tests pass!

## Log Messages to Watch For

When the fix is working, you'll see:

```
Network best height updated (network_best_height=2000, local_height=50)
Local head behind network; continuing header sync (multi-hop height propagation)
  local_height=50, remote_height=100, network_best_height=2000, height_gap=1950
```

## Files Modified

1. `p2p/wire/messages.py` - Add network_best_height field to Hello
2. `p2p/node/p2p_service.py` - Update handshake, height calculation, sync logic
3. `test_network_height_propagation.py` - Unit tests (new file)

## Deployment Notes

- **Backward Compatible**: Old nodes ignore network_best_height field
- **Gradual Rollout**: Mix of old/new nodes will work (new nodes get benefit)
- **No Breaking Changes**: Wire protocol version unchanged
- **No Migration Required**: Nodes discover heights organically as they connect

## Performance Impact

- **Minimal**: One extra integer field in Hello messages
- **Network Traffic**: Negligible increase (4-8 bytes per Hello)
- **CPU**: No measurable impact
- **Memory**: Negligible (one int per peer)

## Future Enhancements

Potential improvements:
1. Exponential backoff for height propagation updates
2. Height gossip messages (dedicated message type)
3. Bloom filters to avoid redundant propagation
4. Network topology awareness for smarter propagation

## References

- Issue: "Nodes not syncing correctly and forking at weird heights"
- Related: P2P sync stall fixes, checkpoint integration
- Spec: Multi-hop height propagation (this document)
