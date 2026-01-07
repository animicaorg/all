# Automatic Snapshot Creation and P2P Snapshot-First Sync

## Overview

Animica now automatically creates chain snapshots every 2000 blocks to enable fast synchronization for new nodes. When a node starts syncing, it will first attempt to download and import a snapshot from peers before falling back to traditional block-by-block P2P sync.

## Features

### 1. Automatic Snapshot Creation

Snapshots are automatically created at regular intervals as blocks are imported:

- **Default interval**: Every 2000 blocks (heights 2000, 4000, 6000, etc.)
- **Asynchronous**: Snapshot creation happens in background threads to avoid blocking block import
- **Compression**: Snapshots are compressed by default to save disk space
- **Location**: `~/.animica/snapshots/chain-{chain_id}-height-{height}/`

### 2. Missing Snapshot Backfill

When a node is past snapshot intervals (e.g., at height 10000), it will automatically detect and create missing snapshots:

- Checks every 100 blocks for missing snapshots
- Creates up to 3 missing snapshots at a time to avoid overwhelming the system
- Prioritizes older snapshots first

### 3. P2P Snapshot-First Sync

New nodes starting from genesis will:

1. **Query peers** for available snapshots via RPC
2. **Download** the latest snapshot if available
3. **Import** the snapshot to quickly reach near-tip state
4. **Fall back** to normal P2P sync if snapshots are unavailable or fail

## Configuration

### Environment Variables

#### `ANIMICA_SNAPSHOT_INTERVAL`
- **Default**: `2000`
- **Description**: Number of blocks between automatic snapshot creation
- **Example**: `ANIMICA_SNAPSHOT_INTERVAL=5000` creates snapshots every 5000 blocks

#### `ANIMICA_SNAPSHOT_AUTO_CREATE`
- **Default**: `true`
- **Description**: Enable/disable automatic snapshot creation
- **Example**: `ANIMICA_SNAPSHOT_AUTO_CREATE=false` disables automatic snapshots

#### `ANIMICA_SNAPSHOT_SYNC_ENABLED`
- **Default**: `true`
- **Description**: Enable/disable snapshot-based sync on startup
- **Example**: `ANIMICA_SNAPSHOT_SYNC_ENABLED=false` forces traditional P2P sync

#### `ANIMICA_SNAPSHOT_RPC_URL`
- **Default**: (none)
- **Description**: RPC URL to query for available snapshots
- **Example**: `ANIMICA_SNAPSHOT_RPC_URL=https://rpc.animica.network`

#### `ANIMICA_SNAPSHOT_MIN_HEIGHT`
- **Default**: `1000`
- **Description**: Minimum height below which snapshot sync is attempted
- **Example**: `ANIMICA_SNAPSHOT_MIN_HEIGHT=5000` only uses snapshots if local height < 5000

#### `ANIMICA_SNAPSHOT_TIMEOUT`
- **Default**: `600` (10 minutes)
- **Description**: Timeout in seconds for snapshot operations
- **Example**: `ANIMICA_SNAPSHOT_TIMEOUT=1200` allows 20 minutes for large snapshots

## Usage

### Starting a New Node

When starting a fresh node, snapshot sync happens automatically:

```bash
# Node will automatically try to bootstrap from snapshots
animica node start
```

Logs will show:
```
INFO  Querying snapshots from https://rpc.animica.network
INFO  Found snapshot at height 10000
INFO  Downloading snapshot to temporary directory
INFO  Successfully imported downloaded snapshot
INFO  Snapshot bootstrap completed successfully
```

### Manually Creating Snapshots

You can manually create snapshots via CLI:

```bash
# Create snapshot at current chain head
animica snapshot create

# Create snapshot at specific height
animica snapshot create --height 8000

# List available snapshots
animica snapshot list

# Get snapshot manifest
animica snapshot get 8000
```

### Manually Importing Snapshots

```bash
# Import a snapshot from a directory
animica snapshot import /path/to/snapshot

# Import with verification
animica snapshot import /path/to/snapshot --verify
```

## Snapshot Format

Each snapshot consists of:

- **manifest.json**: Metadata including chain_id, checkpoint height/hash, block count, state info
- **blocks.cbor** (or chunks): CBOR-encoded blocks up to checkpoint height
- **state.cbor** (or chunks): CBOR-encoded state (accounts, storage, code) at checkpoint
- **chunks/**: Directory with compressed chunks if data is large

Example manifest:
```json
{
  "version": 1,
  "chain_id": 1,
  "checkpoint_height": 10000,
  "checkpoint_hash": "0x1234...",
  "timestamp": 1704067200,
  "blocks_count": 10000,
  "accounts_count": 5000,
  "storage_keys_count": 25000,
  "compressed": true,
  "chunks": [
    {"name": "blocks_0.cbor.gz", "size": 104857600, "hash": "0xabcd..."},
    {"name": "state_0.cbor.gz", "size": 52428800, "hash": "0xef01..."}
  ]
}
```

## Performance Impact

- **Block import**: Minimal impact; snapshot creation happens asynchronously
- **Disk space**: ~100-500MB per snapshot depending on chain state size
- **Sync time**: New nodes can sync to near-tip in minutes instead of hours/days

## Monitoring

Check snapshot creation in logs:

```bash
# View snapshot-related logs
animica logs | grep snapshot

# Sample output:
INFO  Creating disk snapshot at height 2000
INFO  Snapshot created successfully at height 2000 (elapsed: 45.2s)
INFO  Found 3 missing snapshots, will create in background
```

## Technical Details

### Block Import Integration

Snapshots are created in `core/chain/block_import.py` when blocks become canonical:

1. Block import calls `_apply_reorg()` when fork choice updates
2. For each new canonical block, checks if height is at snapshot interval
3. If yes, spawns background thread to create snapshot
4. Every 100 blocks, checks for missing snapshots and creates up to 3

### Peer Discovery

Peers advertise available snapshots via RPC:

1. Node queries peer's `snapshot.list` RPC method
2. Peer returns list of available snapshot heights and metadata
3. Node selects best snapshot (typically highest height)
4. Node downloads snapshot via `snapshot.downloadChunk` or HTTP

### Snapshot Sync Flow

```
┌─────────────────┐
│  Node Startup   │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│ Current height < 1000?  │─── No ──> Use normal P2P sync
└────────┬────────────────┘
         │ Yes
         ▼
┌─────────────────────────┐
│ Query peers for         │
│ available snapshots     │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ Snapshots available?    │─── No ──> Use normal P2P sync
└────────┬────────────────┘
         │ Yes
         ▼
┌─────────────────────────┐
│ Download & import       │
│ best snapshot           │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ Success?                │─── No ──> Use normal P2P sync
└────────┬────────────────┘
         │ Yes
         ▼
┌─────────────────────────┐
│ Continue P2P sync       │
│ from snapshot height    │
└─────────────────────────┘
```

## Troubleshooting

### Snapshots not being created

Check:
1. `ANIMICA_SNAPSHOT_AUTO_CREATE=true` is set (default)
2. Node is actively importing blocks
3. Disk space is available in `~/.animica/snapshots/`
4. Logs for snapshot creation errors

### Snapshot sync failing

Check:
1. `ANIMICA_SNAPSHOT_RPC_URL` is set to a valid peer
2. Peer has snapshots available (check with `animica snapshot list --rpc <peer-url>`)
3. Network connectivity to peer
4. Increase `ANIMICA_SNAPSHOT_TIMEOUT` if downloads are slow

### Large disk usage

Snapshots accumulate over time. To manage disk space:

```bash
# List all snapshots
animica snapshot list

# Delete old snapshots
animica snapshot delete 2000
animica snapshot delete 4000

# Keep only recent snapshots (e.g., last 5)
# Manual cleanup recommended
```

## Future Improvements

- Automatic snapshot pruning (keep only N most recent)
- Snapshot verification/validation before import
- Distributed snapshot hosting via IPFS/Arweave
- Incremental snapshots (only state delta since previous)
- Snapshot compression optimization
