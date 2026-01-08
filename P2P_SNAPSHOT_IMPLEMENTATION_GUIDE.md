# P2P Snapshot Discovery and Download - Implementation Guide

## Overview

This implementation enables nodes to automatically discover and download snapshots from connected peers over the P2P network, eliminating the need for manual configuration of snapshot sources.

## Problem Solved

Previously, the snapshot system had:
- ✅ Server-side handler (`SnapshotHandler`) that could RESPOND to P2P requests
- ❌ No client-side code to SEND requests and await responses
- ❌ Snapshot discovery fell back to RPC queries which most peers don't expose

Result: Nodes couldn't discover or download snapshots from peers, even when connected to nodes with available snapshots.

## Architecture

### Components

1. **P2P Message Protocol** (`p2p/wire/messages.py`)
   - `GET_SNAPSHOTS` (0x0305): Request list of available snapshots
   - `SNAPSHOTS` (0x0306): Response with snapshot metadata list
   - `GET_SNAPSHOT_CHUNK` (0x0307): Request a specific snapshot chunk
   - `SNAPSHOT_CHUNK` (0x0308): Response with chunk data

2. **Server Side** (`p2p/protocol/snapshot.py`)
   - `SnapshotHandler`: Handles incoming requests
   - Lists snapshots from `~/.animica/snapshots/`
   - Serves chunk files (`blocks.tar.zst`, `state.tar.zst`)

3. **Client Side** (`p2p/node/p2p_service.py`)
   - `query_peer_snapshots()`: Send GET_SNAPSHOTS and await response
   - `query_peer_snapshot_chunk()`: Send GET_SNAPSHOT_CHUNK and await response
   - `_handle_snapshots()`: Process SNAPSHOTS response
   - `_handle_snapshot_chunk()`: Process SNAPSHOT_CHUNK response

4. **Discovery & Download** (`p2p/sync/snapshot_sync.py`)
   - `_query_peers_for_snapshots()`: Query all connected peers in parallel
   - `_download_and_import_snapshot_via_p2p()`: Download chunks and import
   - `try_snapshot_bootstrap()`: Orchestrate discovery and selection

### Request/Response Pattern

Uses asyncio Future pattern similar to header sync:

```python
# Store Future in peer state
fut: asyncio.Future = asyncio.get_event_loop().create_future()
peer.pending_snapshot_list = fut

# Send request
await self._send(peer, MsgID.GET_SNAPSHOTS, request)

# Wait for response with timeout
response = await asyncio.wait_for(fut, timeout=10.0)
```

When the response arrives:
```python
# In _handle_snapshots()
fut = peer.pending_snapshot_list
if fut is not None and not fut.done():
    fut.set_result(snapshots)
```

## Data Flow

### Snapshot Discovery

```
┌─────────────┐                                    ┌─────────────┐
│   Node A    │                                    │   Node B    │
│  (Client)   │                                    │  (Server)   │
└──────┬──────┘                                    └──────┬──────┘
       │                                                  │
       │ 1. GET_SNAPSHOTS(chain_id=1)                   │
       │─────────────────────────────────────────────────>│
       │                                                  │
       │                2. List local snapshots          │
       │                   from ~/.animica/snapshots/    │
       │                                                  │
       │ 3. SNAPSHOTS([snap1, snap2, ...])               │
       │<─────────────────────────────────────────────────│
       │                                                  │
       │ 4. Future.set_result(snapshots)                 │
       │                                                  │
```

### Snapshot Download

```
┌─────────────┐                                    ┌─────────────┐
│   Node A    │                                    │   Node B    │
│  (Client)   │                                    │  (Server)   │
└──────┬──────┘                                    └──────┬──────┘
       │                                                  │
       │ For each chunk (blocks.tar.zst, state.tar.zst): │
       │                                                  │
       │ 1. GET_SNAPSHOT_CHUNK(height, chunk_name)       │
       │─────────────────────────────────────────────────>│
       │                                                  │
       │                2. Read chunk file               │
       │                                                  │
       │ 3. SNAPSHOT_CHUNK(data, found=True)             │
       │<─────────────────────────────────────────────────│
       │                                                  │
       │ 4. Write to temp directory                      │
       │                                                  │
```

## Implementation Details

### 1. P2P Service Extensions

Added to `_PeerState`:
```python
pending_snapshot_list: Optional[asyncio.Future] = None
pending_snapshot_chunk: Optional[asyncio.Future] = None
```

New methods in `P2PService`:
```python
async def query_peer_snapshots(
    self, peer: _PeerState, chain_id: Optional[int] = None, timeout: float = 10.0
) -> Optional[list[dict[str, Any]]]:
    """Query a peer for available snapshots via P2P."""
    
async def query_peer_snapshot_chunk(
    self,
    peer: _PeerState,
    chain_id: int,
    checkpoint_height: int,
    chunk_name: str,
    timeout: float = 30.0,
) -> Optional[tuple[bytes, bool]]:
    """Query a peer for a specific snapshot chunk via P2P."""
```

Message handlers:
```python
async def _handle_snapshots(self, peer: _PeerState, payload: bytes) -> None:
    """Handle SNAPSHOTS response from peer."""
    
async def _handle_snapshot_chunk(self, peer: _PeerState, payload: bytes) -> None:
    """Handle SNAPSHOT_CHUNK response from peer."""
```

### 2. Snapshot Discovery

```python
async def _query_peers_for_snapshots(
    p2p_service: Any,
    chain_id: int,
) -> dict[str, list[dict[str, Any]]]:
    """Query all connected peers for their available snapshots via P2P messages."""
```

- Accesses `p2p_service._peers` to get connected peers
- Filters to peers with completed handshake (`hello_done.is_set()`)
- Queries all peers in parallel using `query_peer_snapshots()`
- Returns dict mapping `peer:{address}` to snapshot lists

### 3. Snapshot Download

```python
async def _download_and_import_snapshot_via_p2p(
    p2p_service: Any,
    peer_address: str,
    chain_id: int,
    checkpoint_height: int,
    block_db: Any,
    state_db: Any,
) -> bool:
    """Download and import a snapshot from a P2P peer."""
```

Steps:
1. Find peer by address in `p2p_service._peers`
2. Query peer for snapshot list to verify it has the snapshot
3. Create temporary directory
4. Download each chunk (`blocks.tar.zst`, `state.tar.zst`)
5. Create `manifest.json` with metadata
6. Import using `core.db.snapshot.import_snapshot()`
7. Clean up temporary directory

### 4. Integration

Modified `try_snapshot_bootstrap()`:
- Accepts optional `p2p_service` parameter
- Calls `_query_peers_for_snapshots()` to discover snapshots from peers
- Also queries static RPC URL if configured (`ANIMICA_SNAPSHOT_RPC_URL`)
- Aggregates all snapshots from all sources
- Selects highest snapshot by checkpoint height
- Downloads from P2P peer if source is `peer:{address}`
- Falls back to RPC/HTTP download if source is HTTP URL

## Configuration

### Environment Variables

- `ANIMICA_SNAPSHOT_SYNC_ENABLED` (default: `true`): Enable/disable snapshot sync
- `ANIMICA_SNAPSHOT_RPC_URL` (optional): Static RPC URL for additional snapshot source
- `ANIMICA_SNAPSHOT_MIN_HEIGHT` (default: `1000`): Minimum height to use snapshots
- `ANIMICA_SNAPSHOT_TIMEOUT` (default: `600`): Timeout for snapshot operations in seconds
- `ANIMICA_SNAPSHOT_RETRY_INTERVAL` (default: `60`): Interval between discovery retries
- `ANIMICA_SNAPSHOT_MAX_RETRIES` (default: `0`): Maximum retries (0 = unlimited)

### No Manual Configuration Required

The system automatically:
- Discovers snapshots from all connected peers
- Selects the highest available snapshot
- Downloads and imports transparently during node startup

## Usage

### Automatic (Default)

Snapshot discovery and download happens automatically when:
1. Node starts with chain height < 1000
2. P2P peers are connected
3. `ANIMICA_SNAPSHOT_SYNC_ENABLED=true` (default)

### Manual via CLI

The CLI commands now properly use the P2P protocol for snapshot discovery:

```bash
# Discover best snapshot from connected peers via P2P protocol
# (Works automatically, no need for peers to expose RPC)
animica snapshot discover

# List snapshots from connected peers via P2P protocol
animica snapshot list --from-peers

# List local snapshots + highest peer snapshot
animica snapshot list

# Query specific node via RPC (if peer explicitly exposes RPC)
# Note: Most peers don't expose RPC for security reasons
animica snapshot list --rpc http://peer-node:8545
```

**How it works:**
1. CLI calls `snapshot.discoverFromPeers` RPC method on your local node
2. Your node uses its P2P service to query connected peers via P2P protocol
3. Peers respond with available snapshots (using GET_SNAPSHOTS/SNAPSHOTS messages)
4. Results are returned to the CLI

**Note:** This means:
- ✅ Works with any P2P-connected peer (no RPC exposure needed)
- ✅ Uses the same P2P protocol as automatic discovery during node startup
- ✅ Peers don't need to expose their RPC endpoints
- ✅ More secure and reliable than trying to query peers' RPC directly

## Testing

### Unit Tests

```bash
python3 test_p2p_snapshot_discovery.py
```

Tests:
- ✅ Query single peer for snapshots
- ✅ Query multiple peers for snapshots
- ✅ Find highest snapshot from multiple peers

### Integration Testing

1. Start two nodes:
   ```bash
   # Node 1 (has snapshot)
   animica node start --chain-id 1
   
   # Node 2 (needs snapshot)
   animica node start --chain-id 1 --bootstrap-peer <node1-address>
   ```

2. Node 2 should automatically:
   - Connect to Node 1
   - Discover available snapshot
   - Download and import snapshot
   - Continue sync from snapshot height

## Monitoring

### Logs

Look for these log messages:

```
INFO:animica.p2p.snapshot_sync:Querying N peer(s) for available snapshots via P2P
INFO:animica.p2p.snapshot_sync:Peer 1.2.3.4:30333 reported M snapshot(s)
INFO:animica.p2p.snapshot_sync:Successfully discovered snapshots from N peer(s)
INFO:animica.p2p.snapshot_sync:Found best snapshot at height X from peer:Y
INFO:animica.p2p.snapshot_sync:Downloading chunk: blocks.tar.zst
INFO:animica.p2p.snapshot_sync:Successfully imported P2P downloaded snapshot
```

### Debugging

Enable debug logging:
```bash
export ANIMICA_LOG_LEVEL=DEBUG
export LOG_LEVEL=DEBUG
```

## Performance

### Network Overhead

- GET_SNAPSHOTS request: ~100 bytes
- SNAPSHOTS response: ~200 bytes per snapshot
- GET_SNAPSHOT_CHUNK request: ~100 bytes
- SNAPSHOT_CHUNK response: Size of chunk (typically 10-100 MB)

### Timeouts

- Snapshot list query: 10 seconds
- Chunk download: 60 seconds
- Total import: Depends on snapshot size

### Parallel Discovery

Queries all connected peers simultaneously, dramatically faster than sequential queries.

## Security Considerations

1. **Encrypted Transport**: All P2P messages use existing encrypted channels
2. **Hash Verification**: `import_snapshot()` verifies chunk hashes
3. **Peer Trust**: Only queries known connected peers (not arbitrary nodes)
4. **Timeout Protection**: All operations have timeouts to prevent DoS

## Troubleshooting

### No snapshots discovered

**Symptoms**: Log shows "No snapshots found on connected peers"

**Causes**:
1. No peers connected
2. Connected peers have no snapshots
3. Peers haven't created snapshots yet

**Solutions**:
```bash
# Check peer connections
animica peer list

# Create snapshot on peer node
animica snapshot create

# Connect to more peers
animica peer add <address>
```

### Download failures

**Symptoms**: Log shows "Timeout downloading chunk" or "Failed to import"

**Causes**:
1. Network issues between peers
2. Peer disconnected during download
3. Corrupted chunk data

**Solutions**:
```bash
# Check network connectivity
ping <peer-ip>

# Try different peer
# System will automatically retry with next peer

# Enable debug logging
export ANIMICA_LOG_LEVEL=DEBUG
```

### P2P service not available

**Symptoms**: Log shows "P2P service does not support snapshot queries yet"

**Cause**: Using older version without P2P snapshot support

**Solution**: Update to latest version with P2P snapshot support

## Future Enhancements

1. **Incremental Snapshots**: Download only changed blocks since last snapshot
2. **Compressed Chunks**: Further reduce bandwidth with better compression
3. **Chunk Resume**: Resume interrupted downloads from where they left off
4. **Peer Reputation**: Track which peers provide good snapshots
5. **Snapshot Caching**: Keep downloaded chunks for serving to other peers

## References

- Original issue: "Snapshots not being displayed or showed or downloaded over p2p"
- Implementation PR: [Link to PR]
- Related docs:
  - `P2P_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md`
  - `SNAPSHOT_AUTOMATION_README.md`
  - `p2p/protocol/snapshot.py`
  - `p2p/sync/snapshot_sync.py`
