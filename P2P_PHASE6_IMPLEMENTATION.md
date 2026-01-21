# Phase 6: Block Gossip/Propagation Implementation

## Overview

Phase 6 implements block announcement propagation across the P2P network. When a node accepts a new block, it broadcasts a `HEAD_STATUS` message to all connected peers, allowing the network to stay synchronized in real-time.

## Architecture

```
Block Accepted → EventBus.emit("newHead") → BlockAnnounceHandler
                                                    ↓
                                          Broadcast HEAD_STATUS
                                                    ↓
                              ┌──────────────────────────────────┐
                              ↓                                  ↓
                        Gossip Engine                    Direct Peer Send
                       (mesh broadcast)                  (all connected)
                              ↓                                  ↓
                        Connected Peers ← Receive HEAD_STATUS ──┘
                              ↓
                    TipManager.on_tip_received()
                              ↓
                    Check if behind (gap > 2 blocks)
                              ↓
                    EventBus.emit("syncCheck") → Trigger Sync
```

## Components

### 1. BlockAnnounceHandler

**Location:** `p2p/protocol/block_announce_handler.py`

**Responsibilities:**
- Handle incoming `HEAD_STATUS` messages from peers
- Update peer tip information via `TipManager`
- Trigger sync when detecting the node is behind
- Broadcast `HEAD_STATUS` when new blocks are accepted
- Subscribe to `newHead` events from the `EventBus`

**Key Methods:**
- `handle(conn, frame)`: Process incoming HEAD_STATUS messages
- `_broadcast_head_status()`: Send announcements to all peers
- `start()`: Subscribe to newHead events
- `stop()`: Clean up subscriptions and tasks

### 2. TipManager

**Location:** `p2p/node/tip_manager.py`

**Responsibilities:**
- Track peer tip (head status) information
- Compute best tip across the network
- Manage tip freshness (600s default window)
- Poll peers periodically for tip updates (30s interval)

**Key Methods:**
- `on_tip_received()`: Update peer tip when HEAD_STATUS received
- `get_best_tip()`: Get highest peer tip from fresh tips
- `poll_peer_tips()`: Identify peers needing tip refresh

### 3. PeerRegistry

**Location:** `p2p/node/peer_registry.py`

**Responsibilities:**
- Maintain peer session state
- Track peer identity and validation
- Store peer tip information
- Enforce connection limits

**Key Methods:**
- `register()`: Register new peer connection
- `mark_identity_validated()`: Mark peer as validated
- `update_peer_tip()`: Update stored tip for peer
- `get_best_peer_tip()`: Query highest tip across peers

## Message Flow

### Outbound: Broadcasting New Block

1. **Block Acceptance** (Core)
   ```python
   # In core blockchain code (when block is accepted)
   await events.emit("newHead", {
       "height": block.height,
       "hash": block.hash.hex(),
   })
   ```

2. **Handler Receives Event**
   ```python
   # BlockAnnounceHandler._broadcast_loop
   async for event in subscription:
       height = event.get("height", 0)
       if height > self._last_broadcast_height:
           await self._broadcast_head_status()
   ```

3. **HEAD_STATUS Creation**
   ```python
   head_status = wire_msg.HeadStatus(
       chain_id=chain_id,
       head_height=local_height,
       head_hash=local_hash,
       timestamp_ms=int(time.time() * 1000),
       network_best_height=None,
   )
   ```

4. **Broadcast via Gossip + Direct Send**
   - Gossip engine fans out to mesh peers (efficient, deduplicated)
   - Direct send to all CONNECTED peers with validated identity

### Inbound: Receiving Peer Announcement

1. **Router Dispatches HEAD_STATUS**
   ```python
   # Router receives frame with msg_id=MsgID.HEAD_STATUS
   handler = router._handlers.get(frame.msg_id)
   await handler.handle(conn, frame)
   ```

2. **Handler Processes Message**
   ```python
   # BlockAnnounceHandler.handle
   msg = codec.decode(frame.payload, wire_msg.HeadStatus)
   
   # Update peer tip
   tip_manager.on_tip_received(
       session_id=session_id,
       height=msg.head_height,
       hash_hex=msg.head_hash.hex(),
   )
   ```

3. **Check if Sync Needed**
   ```python
   local_height = await self._get_local_height()
   
   if msg.head_height > local_height + 2:  # Gap > 2 blocks
       await events.emit("syncCheck", {
           "reason": "peer_ahead",
           "local_height": local_height,
           "peer_height": msg.head_height,
       })
   ```

4. **Sync Engine Responds**
   - Sync service listens for `syncCheck` events
   - Initiates header/block download from peer
   - Catches up to network tip

## Configuration

### Handler Configuration

```python
# In NodeService.__post_init__()
self.peer_registry = PeerRegistry()
self.tip_manager = TipManager(
    registry=self.peer_registry,
    poll_interval_s=30.0,        # Poll peer tips every 30s
    freshness_window_s=600.0,    # 10 minute freshness window
)

self.block_announce_handler = BlockAnnounceHandler(
    cfg=self.cfg,
    codec=codec,
    deps=self.deps,
    gossip=self.gossip,
    tip_manager=self.tip_manager,
    registry=self.peer_registry,
    events=self.events,
)
```

### Tuning Parameters

**Sync Trigger Threshold:**
```python
# In BlockAnnounceHandler.handle()
if msg.head_height > local_height + 2:  # Allow 2 block lag
    # Trigger sync
```

**Tip Freshness Window:**
```python
# In TipManager.__init__()
freshness_window_s=600.0  # 10 minutes
```

**Poll Interval:**
```python
# In TipManager.__init__()
poll_interval_s=30.0  # 30 seconds
```

## Metrics

The handler tracks the following metrics:

```python
metrics = {
    "announcements_sent": 0,       # HEAD_STATUS broadcasts sent
    "announcements_received": 0,   # HEAD_STATUS messages received
    "peer_tips_updated": 0,        # Peer tip updates processed
    "sync_triggered": 0,           # Sync checks triggered
}
```

Access via:
```python
metrics = node_service.block_announce_handler.get_metrics()
```

## Testing

### Unit Tests

Run the unit tests:
```bash
pytest p2p/tests/test_block_announce_handler.py -v
```

Test coverage includes:
- Handler initialization
- Message ID registration
- HEAD_STATUS message handling
- Peer tip updates
- Sync triggering when behind
- Broadcasting functionality
- Event subscription/unsubscription
- Metrics tracking

### Integration Testing

To test with live nodes:

1. **Start two nodes:**
   ```bash
   # Node 1 (on port 9001)
   ./node --p2p-listen tcp://0.0.0.0:9001 --data-dir /tmp/node1
   
   # Node 2 (on port 9002, connect to node1)
   ./node --p2p-listen tcp://0.0.0.0:9002 --data-dir /tmp/node2 \
          --seeds tcp://127.0.0.1:9001
   ```

2. **Mine a block on Node 1:**
   ```bash
   # On node1
   curl -X POST http://localhost:8545/mine
   ```

3. **Verify Node 2 receives announcement:**
   ```bash
   # Check logs for:
   # "Peer tip updated" with node1's new height
   # "Peer ahead: ... triggering sync check"
   tail -f /tmp/node2/logs/p2p.log | grep "HEAD_STATUS\|Peer tip\|Peer ahead"
   ```

4. **Check metrics:**
   ```bash
   curl http://localhost:8545/metrics | jq '.p2p.block_announce'
   ```

## Troubleshooting

### No announcements sent

**Check:**
- Is the handler started? (`await handler.start()` called?)
- Is EventBus emitting `newHead` events when blocks are accepted?
- Check logs for "Broadcast HEAD_STATUS" messages

### Peer tips not updating

**Check:**
- Are peers connected and identity validated?
- Check `peer_registry._sessions` for CONNECTED state
- Verify HEAD_STATUS messages are being received (check router logs)
- Check TipManager logs for "Peer tip updated"

### Sync not triggering

**Check:**
- Is the gap threshold met? (peer_height > local_height + 2)
- Is EventBus emitting `syncCheck` events?
- Is the sync service subscribed to `syncCheck` events?

### High announcement rate

**Adjust:**
- Deduplicate by checking `_last_broadcast_height` in handler
- Increase lag threshold before broadcasting
- Rate-limit announcements (e.g., max 1 per second)

## Performance Considerations

### Broadcast Efficiency

- **Gossip engine** handles mesh fanout with deduplication
- **Direct send** ensures all connected peers receive updates
- Both paths run concurrently for reliability

### Network Load

- HEAD_STATUS messages are small (~100 bytes)
- Broadcast rate matches block production rate
- Gossip mesh limits fanout (typically 6-12 peers)

### Memory Usage

- TipManager stores one tip per peer session
- PeerRegistry maintains session state (typically < 1KB per peer)
- Broadcast loop uses minimal memory (async iterator)

## Future Enhancements

1. **Compact Block Relay:**
   - Use `CompactAnnounce` with tx short-ids
   - Reduce bandwidth for blocks with known transactions

2. **Network Best Height Propagation:**
   - Include `network_best_height` in HEAD_STATUS
   - Enable multi-hop height awareness

3. **Selective Announcement:**
   - Only announce to peers that are behind
   - Reduce redundant broadcasts

4. **Priority Peers:**
   - Fast-path announcements to high-reputation peers
   - Improve propagation latency

## References

- **Protocol Spec:** `p2p/protocol/block_announce.py`
- **Wire Messages:** `p2p/wire/messages.py` (HeadStatus, BlockAnnounce)
- **Message IDs:** `p2p/wire/message_ids.py` (MsgID.HEAD_STATUS, MsgID.BLOCK_ANNOUNCE)
- **Phase 4 Implementation:** `p2p/node/tip_manager.py`, `p2p/node/peer_registry.py`
