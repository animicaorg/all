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

Nodes can automatically download and import snapshots on first sync:

```bash
# Enable snapshot sync (default: enabled)
export ANIMICA_SNAPSHOT_SYNC_ENABLED=true

# Configure snapshot source RPC
export ANIMICA_SNAPSHOT_RPC_URL=http://snapshots.animica.org:8545/rpc

# Minimum height gap to use snapshots (default: 1000)
export ANIMICA_SNAPSHOT_MIN_HEIGHT=1000

# Snapshot operation timeout (default: 600 seconds)
export ANIMICA_SNAPSHOT_TIMEOUT=600

# Start node - will try snapshot bootstrap first
animica node up
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_SNAPSHOT_SYNC_ENABLED` | `true` | Enable automatic snapshot bootstrap |
| `ANIMICA_SNAPSHOT_RPC_URL` | _(none)_ | RPC endpoint to fetch snapshots from |
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
1. Node starts with empty chain
2. Check if snapshot bootstrap should be attempted
   - Is snapshot sync enabled?
   - Is current height below threshold?
   - Is snapshot RPC configured?
3. If yes, query available snapshots
4. Download and import best snapshot (highest height)
5. Continue P2P sync from checkpoint to current head
6. If snapshot fails, fall back to full P2P sync
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

- Ensure `ANIMICA_SNAPSHOT_RPC_URL` is configured
- Check that snapshot RPC endpoint is reachable
- Verify snapshots exist for your chain ID

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

1. **HTTP Chunk Download**: Direct download of snapshot chunks via HTTP
2. **Torrent Distribution**: P2P distribution of snapshots via BitTorrent
3. **Incremental Snapshots**: Delta snapshots between checkpoints
4. **Streaming Import**: Import while downloading (pipelined)
5. **Multiple Checkpoint Levels**: Snapshots at different intervals (every 10K, 50K, 100K blocks)

## See Also

- [SYNC_PERFORMANCE_OPTIMIZATION.md](../SYNC_PERFORMANCE_OPTIMIZATION.md) - P2P sync optimization
- [p2p/checkpoints/README.md](../p2p/checkpoints/README.md) - Checkpoint mechanism
- [execution/state/snapshots.py](../execution/state/snapshots.py) - State snapshots for execution layer
