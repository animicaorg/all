# Chain Snapshot Sync

## Overview

Chain snapshots provide a fast-sync mechanism that allows new nodes to bootstrap by downloading pre-built chain state instead of syncing from genesis block by block. This dramatically reduces initial sync time from hours to minutes.

## Architecture

### Snapshot Structure

A snapshot contains the complete chain state at a specific checkpoint height:

```
snapshot-directory/
├── manifest.json       # Metadata and integrity hashes
├── blocks.cbor.gz      # All blocks and headers (0 to checkpoint)
└── state.cbor.gz       # Complete state (accounts, storage, code)
```

### Manifest Format

```json
{
  "version": 1,
  "chain_id": 1,
  "checkpoint_height": 55795,
  "checkpoint_hash": "0x0a3205eb...",
  "timestamp": 1704636000,
  "blocks_count": 55796,
  "headers_count": 55796,
  "accounts_count": 1250,
  "storage_keys_count": 45000,
  "code_contracts_count": 150,
  "compressed": true,
  "chunks": [
    {
      "name": "blocks.cbor.gz",
      "type": "blocks",
      "size": 125829120,
      "hash": "0x1234..."
    },
    {
      "name": "state.cbor.gz",
      "type": "state",
      "size": 45678901,
      "hash": "0x5678..."
    }
  ]
}
```

## Usage

### Creating Snapshots

Snapshots are typically created at checkpoint heights by trusted nodes:

```bash
# Create snapshot at current head
animica snapshot create

# Create snapshot at specific height
animica snapshot create --height 55795

# Create without compression (faster but larger)
animica snapshot create --height 55795 --no-compress
```

Via RPC:

```bash
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "snapshot.create",
    "params": {"height": 55795, "compress": true}
  }'
```

### Listing Snapshots

```bash
# List all snapshots
animica snapshot list

# List snapshots for specific chain
animica snapshot list --chain-id 1

# JSON output
animica snapshot list --json
```

### Verifying Snapshots

Before importing, verify snapshot integrity:

```bash
# Verify snapshot at height
animica snapshot verify 55795

# Verify for specific chain
animica snapshot verify 55795 --chain-id 1
```

### Importing Snapshots

**⚠️ WARNING:** Importing a snapshot will overwrite existing chain data!

```bash
# Import snapshot
animica snapshot import /path/to/snapshot

# Import without hash verification (faster but risky)
animica snapshot import /path/to/snapshot --no-verify
```

### Automatic Snapshot Bootstrap

Nodes now **automatically** discover and download snapshots from connected peers on startup:

```bash
# Enable snapshot sync (default: enabled)
export ANIMICA_SNAPSHOT_SYNC_ENABLED=true

# Enable automatic peer discovery (default: enabled)
export ANIMICA_SNAPSHOT_AUTO_DISCOVER=true

# Optional: Configure a specific snapshot source RPC
# If not set, the node will automatically query connected peers for snapshots
export ANIMICA_SNAPSHOT_RPC_URL=http://snapshots.animica.org:8545/rpc

# Minimum height gap to use snapshots (default: 1000)
export ANIMICA_SNAPSHOT_MIN_HEIGHT=1000

# Snapshot operation timeout (default: 600 seconds)
export ANIMICA_SNAPSHOT_TIMEOUT=600

# Start node - will automatically discover and use snapshots from peers
animica node up
```

**How Automatic Discovery Works:**

1. Node starts and begins P2P service
2. **Background task waits for peers to connect (up to 30 seconds)**
3. **Automatically queries all connected peers for their available snapshots**
4. **Aggregates snapshots and selects the highest checkpoint height**
5. If `ANIMICA_SNAPSHOT_RPC_URL` is configured, also queries that endpoint
6. Downloads the best snapshot (highest height) to a temporary directory
7. Verifies chunk hashes for integrity
8. Imports the snapshot into local databases
9. Continues P2P sync from the snapshot checkpoint
10. Falls back to normal P2P sync if snapshot bootstrap fails

**Benefits:**
- ✅ **Zero Configuration**: Works automatically when peers are available
- ✅ **No Manual Commands**: No need to run `animica snapshot discover`
- ✅ **Resilient**: Falls back to normal sync if no snapshots found
- ✅ **Non-Blocking**: Runs in background, doesn't delay node startup

**Note:** If you prefer manual control, you can disable automatic discovery:
```bash
export ANIMICA_SNAPSHOT_AUTO_DISCOVER=false
animica node up
```

Then manually discover and sync:
```bash
animica snapshot discover       # Find best snapshot
animica snapshot list --from-peers   # List all peer snapshots
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_SNAPSHOT_SYNC_ENABLED` | `true` | Enable automatic snapshot bootstrap |
| `ANIMICA_SNAPSHOT_AUTO_DISCOVER` | `true` | Enable automatic peer snapshot discovery on startup |
| `ANIMICA_SNAPSHOT_RPC_URL` | _(none)_ | Optional RPC endpoint to fetch snapshots from. If not set, queries connected peers automatically. |
| `ANIMICA_SNAPSHOT_MIN_HEIGHT` | `1000` | Minimum height gap to use snapshots |
| `ANIMICA_SNAPSHOT_TIMEOUT` | `600` | Timeout for snapshot operations (seconds) |

### Storage Location

Snapshots are stored in the data directory:

```
~/.animica/snapshots/
  ├── chain-1-height-55795/
  │   ├── manifest.json
  │   ├── blocks.cbor.gz
  │   └── state.cbor.gz
  └── chain-1-height-60000/
      ├── manifest.json
      ├── blocks.cbor.gz
      └── state.cbor.gz
```

## Performance

### Sync Time Comparison

**Traditional P2P Sync:**
- Genesis to 100K blocks: ~2-6 hours
- Dependent on network conditions and peer availability

**Snapshot Bootstrap:**
- Download snapshot: ~5-15 minutes (depends on snapshot size and bandwidth)
- Import snapshot: ~2-5 minutes
- **Total: ~7-20 minutes** (4-20x faster)

After snapshot import, node continues P2P sync from checkpoint height to current head.

### Resource Requirements

**During Snapshot Creation:**
- CPU: Moderate (compression)
- Memory: ~1-2 GB
- Disk I/O: High (sequential writes)
- Time: ~10-30 minutes for 100K blocks

**During Snapshot Import:**
- CPU: Moderate (decompression + validation)
- Memory: ~1-2 GB
- Disk I/O: High (sequential writes)
- Time: ~2-5 minutes for 100K blocks

## Security

### Trust Model

Snapshots require trust in the source:

1. **Checkpoint Verification**: Snapshot hash must match a known checkpoint
2. **Chunk Integrity**: Each chunk is hash-verified on import
3. **State Consistency**: After import, node validates state roots
4. **Subsequent P2P Sync**: Node continues normal P2P sync after snapshot

### Best Practices

1. **Use Official Snapshots**: Download from trusted sources (e.g., `snapshots.animica.org`)
2. **Verify Hashes**: Always verify chunk hashes (default behavior)
3. **Cross-Check Heights**: Verify checkpoint height matches known checkpoints
4. **Multiple Sources**: Compare snapshots from multiple sources if available

## Integration with Sync

### Sync Flow with Snapshots

```
1. Node starts with empty or low chain height
2. Check if snapshot bootstrap should be attempted
   - Is snapshot sync enabled?
   - Is current height below threshold?
3. If yes, discover snapshots from multiple sources:
   a. Query all connected P2P peers for their snapshots
   b. Query static RPC URL if ANIMICA_SNAPSHOT_RPC_URL is configured
4. Aggregate all discovered snapshots
5. Select best snapshot (highest checkpoint height)
6. Download and import best snapshot
7. Continue P2P sync from checkpoint to current head
8. If snapshot fails, fall back to full P2P sync from genesis
```

### Checkpoint Integration

Snapshots are aligned with built-in checkpoints:

```python
# Built-in checkpoints (p2p/checkpoints/builtin.py)
MAINNET_CHECKPOINTS = [
    (55795, "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"),
]
```

Snapshots should be created at checkpoint heights for consistency.

## RPC Methods

### snapshot.create

Create a snapshot at specified height.

```json
{
  "method": "snapshot.create",
  "params": {
    "height": 55795,
    "compress": true
  }
}
```

### snapshot.list

List available snapshots.

```json
{
  "method": "snapshot.list",
  "params": {
    "chain_id": 1
  }
}
```

### snapshot.get

Get manifest for specific snapshot.

```json
{
  "method": "snapshot.get",
  "params": {
    "height": 55795,
    "chain_id": 1
  }
}
```

### snapshot.verify

Verify snapshot integrity.

```json
{
  "method": "snapshot.verify",
  "params": {
    "height": 55795,
    "chain_id": 1
  }
}
```

### snapshot.import

Import a snapshot (requires local path).

```json
{
  "method": "snapshot.import",
  "params": {
    "path": "/path/to/snapshot",
    "verify_hashes": true
  }
}
```

### snapshot.delete

Delete a snapshot.

```json
{
  "method": "snapshot.delete",
  "params": {
    "height": 55795,
    "chain_id": 1
  }
}
```

## Troubleshooting

### "No snapshots available"

- Ensure you have at least one connected peer
- Check that connected peers have snapshots available (they must be at least at snapshot interval heights)
- Alternatively, configure `ANIMICA_SNAPSHOT_RPC_URL` to point to a trusted snapshot source
- Verify P2P connectivity with peers using `animica net peers`

### "Snapshot hash mismatch"

- Snapshot may be corrupted during download
- Re-download snapshot and try again
- Use `--no-verify` only if you trust the source completely

### "Already synced past snapshot threshold"

- Node already has significant chain data
- Adjust `ANIMICA_SNAPSHOT_MIN_HEIGHT` if needed
- This is normal and node will continue P2P sync

### Import fails with "Failed to import snapshot"

- Check disk space (need 2-3x snapshot size)
- Verify snapshot integrity with `animica snapshot verify`
- Check logs for specific error details

## Future Improvements

1. **HTTP Chunk Download**: ✅ **COMPLETED** - Direct download of snapshot chunks via HTTP
   - Implemented with RPC method fallback
   - Downloads to temporary directory
   - Automatic cleanup after import
2. **Torrent Distribution**: P2P distribution of snapshots via BitTorrent
3. **Incremental Snapshots**: Delta snapshots between checkpoints
4. **Streaming Import**: Import while downloading (pipelined)
5. **Multiple Checkpoint Levels**: Snapshots at different intervals (every 10K, 50K, 100K blocks)

## See Also

- [SYNC_PERFORMANCE_OPTIMIZATION.md](../SYNC_PERFORMANCE_OPTIMIZATION.md) - P2P sync optimization
- [p2p/checkpoints/README.md](../p2p/checkpoints/README.md) - Checkpoint mechanism
- [execution/state/snapshots.py](../execution/state/snapshots.py) - State snapshots for execution layer
