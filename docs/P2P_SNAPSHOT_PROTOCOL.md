# P2P Snapshot Protocol

## Overview

The Animica P2P network supports snapshot discovery and transfer via dedicated P2P messages, allowing nodes to bootstrap from peers without requiring HTTP/RPC access.

## Problem Solved

Previously, snapshot discovery assumed:
- P2P peers run on port 30333
- RPC endpoints are available on port 8545
- Peers expose their RPC publicly

These assumptions failed because:
1. P2P ports are configurable and vary across deployments
2. RPC endpoints are typically firewalled or localhost-only for security
3. Peers connected via P2P don't advertise HTTP endpoints

## Solution

The P2P snapshot protocol uses dedicated wire messages for:
1. **Snapshot discovery** (GET_SNAPSHOTS/SNAPSHOTS)
2. **Snapshot chunk transfer** (GET_SNAPSHOT_CHUNK/SNAPSHOT_CHUNK)

## Protocol Messages

### GET_SNAPSHOTS (0x0305)

Request list of available snapshots from a peer.

**Fields:**
- `chain_id` (optional): Filter by chain ID; if None, returns all chains

**Example:**
```python
from p2p.wire.messages import GetSnapshots

request = GetSnapshots(chain_id=0)  # Request snapshots for chain 0
```

### SNAPSHOTS (0x0306)

Response containing list of available snapshots.

**Fields:**
- `snapshots`: List of SnapshotInfo objects

**SnapshotInfo:**
- `chain_id`: Chain ID
- `checkpoint_height`: Snapshot checkpoint height
- `checkpoint_hash`: Checkpoint block hash (hex string)
- `blocks_count`: Number of blocks in snapshot
- `accounts_count`: Number of accounts in snapshot
- `size_mb`: Total size in megabytes
- `timestamp`: Unix timestamp when snapshot was created

**Example:**
```python
from p2p.wire.messages import Snapshots, SnapshotInfo

response = Snapshots(snapshots=[
    SnapshotInfo(
        chain_id=0,
        checkpoint_height=1000,
        checkpoint_hash="abcd...",
        blocks_count=1000,
        accounts_count=500,
        size_mb=125.5,
        timestamp=1704700000
    )
])
```

### GET_SNAPSHOT_CHUNK (0x0307)

Request a specific chunk of a snapshot.

**Fields:**
- `chain_id`: Chain ID
- `checkpoint_height`: Snapshot checkpoint height
- `chunk_name`: Chunk file name (e.g., "blocks.tar.zst", "state.tar.zst")

**Example:**
```python
from p2p.wire.messages import GetSnapshotChunk

request = GetSnapshotChunk(
    chain_id=0,
    checkpoint_height=1000,
    chunk_name="blocks.tar.zst"
)
```

### SNAPSHOT_CHUNK (0x0308)

Response with snapshot chunk data.

**Fields:**
- `chain_id`: Chain ID
- `checkpoint_height`: Snapshot checkpoint height
- `chunk_name`: Chunk file name
- `data`: Chunk file data (bytes)
- `found`: True if chunk exists, False otherwise

**Example:**
```python
from p2p.wire.messages import SnapshotChunk

response = SnapshotChunk(
    chain_id=0,
    checkpoint_height=1000,
    chunk_name="blocks.tar.zst",
    data=b"...",  # Actual chunk data
    found=True
)
```

## Server-Side Implementation

The `SnapshotHandler` in `p2p/protocol/snapshot.py` automatically handles snapshot requests:

1. **Snapshot Discovery**: Responds to GET_SNAPSHOTS by scanning the local snapshots directory
2. **Chunk Serving**: Responds to GET_SNAPSHOT_CHUNK by reading and returning chunk files

**Configuration:**
- Snapshots directory: `~/.animica/snapshots/` or `$ANIMICA_DATA_DIR/snapshots/`
- Snapshot directory format: `chain-{chain_id}-height-{height}/`
- Expected files: `manifest.json`, chunk files (e.g., `blocks.tar.zst`, `state.tar.zst`)

**Integration:**
The SnapshotHandler is automatically registered in the P2P service:

```python
from p2p.protocol.snapshot import SnapshotHandler

# In P2PService._mount_protocols()
self.router.add_handler(SnapshotHandler())
```

## Client-Side Usage

### Current Status

**Working:**
- Snapshot discovery via explicit RPC URLs (http://...)
- Server-side P2P snapshot serving (GET_SNAPSHOTS/GET_SNAPSHOT_CHUNK handlers)

**Pending:**
- Client-side P2P snapshot download requires request/response API in P2P service
- Stub function `_download_and_import_snapshot_via_p2p()` in place

### Future Implementation

When the P2P service supports synchronous request/response patterns:

```python
# Pseudocode for P2P download
async def download_snapshot_via_p2p(peer, chain_id, height):
    # 1. Send GET_SNAPSHOTS to discover available snapshots
    snapshots_response = await peer.send_request(
        GetSnapshots(chain_id=chain_id)
    )
    
    # 2. Find target snapshot
    target = find_snapshot_at_height(snapshots_response.snapshots, height)
    
    # 3. Download each chunk
    for chunk_info in target.chunks:
        chunk_response = await peer.send_request(
            GetSnapshotChunk(
                chain_id=chain_id,
                checkpoint_height=height,
                chunk_name=chunk_info.name
            )
        )
        save_chunk(chunk_response.data)
    
    # 4. Import snapshot
    import_snapshot(snapshot_dir)
```

## Snapshot Directory Structure

```
~/.animica/snapshots/
├── chain-1-height-1000/
│   ├── manifest.json
│   ├── blocks.tar.zst
│   └── state.tar.zst
├── chain-1-height-2000/
│   ├── manifest.json
│   ├── blocks.tar.zst
│   └── state.tar.zst
└── chain-2-height-500/
    ├── manifest.json
    ├── blocks.tar.zst
    └── state.tar.zst
```

**manifest.json format:**
```json
{
  "chain_id": 1,
  "checkpoint_height": 1000,
  "checkpoint_hash": "abcd1234...",
  "blocks_count": 1000,
  "accounts_count": 500,
  "timestamp": 1704700000,
  "chunks": [
    {"name": "blocks.tar.zst", "size": 104857600, "hash": "..."},
    {"name": "state.tar.zst", "size": 52428800, "hash": "..."}
  ]
}
```

## Security Considerations

1. **Chunk verification**: Always verify chunk hashes against manifest
2. **Size limits**: Enforce maximum chunk sizes to prevent DoS
3. **Rate limiting**: Limit snapshot requests per peer to prevent abuse
4. **Access control**: Consider allowing snapshot serving only to trusted peers

## Migration from HTTP/RPC

### Before
```bash
# Assumed peers expose RPC on port 8545
animica snapshot list --peer 1.2.3.4:30333
# Tried to query http://1.2.3.4:8545/rpc (often failed)
```

### After
```bash
# Only query explicit HTTP URLs
animica snapshot list --peer http://1.2.3.4:8545/rpc

# P2P discovery happens automatically via connected peers
# No port assumptions - uses actual P2P connections
```

### Code Changes

**Removed hardcoded assumptions:**
```python
# OLD (removed)
if ":" in peer_address:
    host, port = peer_address.rsplit(":", 1)
    rpc_url = f"http://{host}:8545"  # Wrong!

# NEW
if not peer_address.startswith("http"):
    # Don't assume RPC port - use P2P protocol
    skip_rpc_query(peer_address)
```

## Testing

### Manual Testing

1. **Create a test snapshot:**
```bash
# Create snapshot at height 100
animica snapshot create --height 100 --chain-id 1
```

2. **Start two nodes with P2P enabled:**
```bash
# Node 1 (has snapshot)
animica node --p2p-port 30333 --chain-id 1

# Node 2 (will query node 1)
animica node --p2p-port 30334 --chain-id 1 --seed /ip4/127.0.0.1/tcp/30333
```

3. **Query snapshots via P2P (when client-side implemented):**
```bash
animica snapshot list  # Should discover snapshots from connected peers
```

### Automated Testing

```python
# Test GET_SNAPSHOTS encoding/decoding
from p2p.wire.messages import GetSnapshots, Snapshots, SnapshotInfo
from p2p.wire.encoding import encode_payload, decode_payload

request = GetSnapshots(chain_id=0)
encoded = encode_payload(request)
decoded = decode_payload(encoded)
assert decoded["chain_id"] == 1

# Test GET_SNAPSHOT_CHUNK encoding/decoding  
from p2p.wire.messages import GetSnapshotChunk, SnapshotChunk

request = GetSnapshotChunk(chain_id=0, checkpoint_height=100, chunk_name="test.tar.zst")
encoded = encode_payload(request)
decoded = decode_payload(encoded)
assert decoded["chunk_name"] == "test.tar.zst"
```

## Troubleshooting

### Issue: "P2P snapshot download from peer X is not yet implemented"

**Cause:** Client-side P2P download requires request/response API in P2P service.

**Workaround:** Use explicit RPC URLs for snapshot downloads:
```bash
animica snapshot list --peer http://peer-ip:8545/rpc
```

### Issue: "No snapshots available from peers"

**Possible causes:**
1. No peers connected (check `animica p2p peers`)
2. Peers don't have snapshots in their snapshots directory
3. Client-side P2P discovery not yet implemented (see above)

**Solution:** Ensure snapshots directory exists on peers and contains valid snapshots.

### Issue: "Chunk not found" error

**Possible causes:**
1. Snapshot was deleted after discovery
2. Chunk file is corrupted or missing
3. Wrong chunk name in request

**Solution:** Re-create the snapshot or use a different peer.

## Future Enhancements

1. **Compression negotiation**: Allow peers to negotiate chunk compression format
2. **Partial chunk transfer**: Support resuming interrupted downloads
3. **Snapshot gossip**: Announce new snapshots to peers via gossip
4. **Chunk deduplication**: Share chunks across multiple snapshots
5. **Erasure coding**: Support erasure-coded chunks for redundancy
6. **Torrent-style transfer**: Download different chunks from different peers

## References

- P2P Wire Protocol: `p2p/wire/`
- Message Definitions: `p2p/wire/messages.py`
- Message IDs: `p2p/wire/message_ids.py`
- Snapshot Handler: `p2p/protocol/snapshot.py`
- Snapshot Sync: `p2p/sync/snapshot_sync.py`
- CLI Commands: `python/animica/cli/snapshot.py`
