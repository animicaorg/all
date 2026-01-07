# Automatic Snapshot Creation and P2P Sync

## Overview

This document describes the automatic snapshot creation and peer-to-peer snapshot discovery features implemented for the Animica blockchain.

## Features

### 1. Automatic Snapshot Creation

Nodes automatically create snapshots at regular block intervals (default: every 2000 blocks). This ensures that recent snapshots are always available for fast sync.

**Key Benefits:**
- No manual intervention required
- Snapshots created in background (non-blocking)
- Automatic cleanup of old snapshots
- Configurable intervals and retention

### 2. P2P Snapshot Discovery

Nodes can discover and download snapshots from peers on startup, enabling truly decentralized fast sync without relying on centralized snapshot servers.

**Key Benefits:**
- Decentralized fast sync
- No single point of failure
- Automatic peer selection
- Fallback to RPC if P2P unavailable

## Configuration

### Automatic Snapshot Creation

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ANIMICA_SNAPSHOT_INTERVAL` | `2000` | Block interval for snapshot creation |
| `ANIMICA_SNAPSHOT_ENABLED` | `true` | Enable/disable automatic snapshots |
| `ANIMICA_SNAPSHOT_RETENTION` | `5` | Number of snapshots to retain |
| `ANIMICA_SNAPSHOT_DIR` | `~/.animica/snapshots` | Directory for snapshots |

### P2P Snapshot Sync

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ANIMICA_SNAPSHOT_SYNC_ENABLED` | `true` | Enable P2P snapshot sync |
| `ANIMICA_SNAPSHOT_QUERY_TIMEOUT` | `30` | Peer query timeout (seconds) |
| `ANIMICA_SNAPSHOT_DOWNLOAD_TIMEOUT` | `600` | Download timeout (seconds) |
| `ANIMICA_SNAPSHOT_MAX_PEERS` | `5` | Max peers to query |

## How It Works

### Automatic Creation Flow

```
Block Import → Height Check → Height % 2000 == 0? 
    ↓ Yes
Background Task → Export Snapshot → Save to Disk → Cleanup Old
```

1. **Trigger**: When a block is imported and becomes canonical at height N where N % 2000 == 0
2. **Background**: Snapshot creation happens in a thread pool to avoid blocking
3. **Export**: Full chain state (blocks + accounts + storage) exported to CBOR
4. **Cleanup**: Old snapshots beyond retention limit are automatically removed

**Example Timeline:**
```
Height 0     → Genesis (no snapshot)
Height 2000  → Snapshot created automatically
Height 4000  → Snapshot created, height 0 kept (retention=5)
Height 6000  → Snapshot created
Height 8000  → Snapshot created
Height 10000 → Snapshot created
Height 12000 → Snapshot created, height 2000 removed (retention=5)
```

### P2P Discovery Flow

```
Node Startup → Query Peers → Select Best → Download → Import → Continue Sync
```

1. **Discovery**: Node queries connected peers for available snapshots
2. **Selection**: Best snapshot selected (highest height, most recent)
3. **Download**: Manifest + chunks downloaded from peer
4. **Verification**: SHA3-256 hash verification of all chunks
5. **Import**: Snapshot imported into local databases
6. **Sync**: Node continues P2P sync from snapshot height

**Fallback Order:**
1. Try P2P snapshot discovery from peers
2. Fall back to RPC snapshot endpoint if configured
3. Fall back to full P2P sync from genesis

## P2P Protocol

### Message Types

**0x09xx — Snapshots (fast sync)**

| ID | Name | Description |
|----|------|-------------|
| `0x0900` | `SNAPSHOT_LIST_REQ` | Request available snapshots |
| `0x0901` | `SNAPSHOT_LIST_RESP` | List of available snapshots |
| `0x0902` | `SNAPSHOT_GET_MANIFEST` | Request snapshot manifest |
| `0x0903` | `SNAPSHOT_MANIFEST` | Snapshot manifest response |
| `0x0904` | `SNAPSHOT_GET_CHUNK` | Request snapshot chunk |
| `0x0905` | `SNAPSHOT_CHUNK` | Snapshot chunk data |

### Protocol Example

**1. Query Peers for Snapshots:**
```python
# Node sends to all connected peers
SNAPSHOT_LIST_REQ {
    chain_id: 1  # Optional filter
}
```

**2. Peer Responds with Available Snapshots:**
```python
SNAPSHOT_LIST_RESP {
    snapshots: [
        {
            chain_id: 1,
            checkpoint_height: 10000,
            checkpoint_hash: "0x1234...",
            timestamp: 1704672000,
            blocks_count: 10001,
            accounts_count: 5000,
            size_bytes: 157286400
        },
        {
            chain_id: 1,
            checkpoint_height: 8000,
            checkpoint_hash: "0x5678...",
            timestamp: 1704658000,
            blocks_count: 8001,
            accounts_count: 4500,
            size_bytes: 134217728
        }
    ]
}
```

**3. Node Selects Best and Requests Manifest:**
```python
SNAPSHOT_GET_MANIFEST {
    chain_id: 1,
    checkpoint_height: 10000
}
```

**4. Peer Sends Manifest:**
```python
SNAPSHOT_MANIFEST {
    chain_id: 1,
    checkpoint_height: 10000,
    checkpoint_hash: "0x1234...",
    timestamp: 1704672000,
    blocks_count: 10001,
    accounts_count: 5000,
    storage_keys_count: 25000,
    chunks: [
        {
            name: "blocks.cbor.gz",
            type: "blocks",
            size: 104857600,
            hash: "0xabcd..."
        },
        {
            name: "state.cbor.gz",
            type: "state",
            size: 52428800,
            hash: "0xef01..."
        }
    ]
}
```

**5. Node Downloads Each Chunk:**
```python
SNAPSHOT_GET_CHUNK {
    chain_id: 1,
    checkpoint_height: 10000,
    chunk_name: "blocks.cbor.gz",
    offset: 0,
    length: 0  # 0 = all
}
```

**6. Peer Streams Chunk Data:**
```python
SNAPSHOT_CHUNK {
    chain_id: 1,
    checkpoint_height: 10000,
    chunk_name: "blocks.cbor.gz",
    offset: 0,
    total_size: 104857600,
    data: <bytes>,
    is_final: true
}
```

## Usage Examples

### Enable Automatic Snapshots

```bash
# Default: enabled with 2000 block interval
animica node up

# Custom interval
export ANIMICA_SNAPSHOT_INTERVAL=5000
animica node up

# Disable automatic snapshots
export ANIMICA_SNAPSHOT_ENABLED=false
animica node up

# Custom retention (keep last 10 snapshots)
export ANIMICA_SNAPSHOT_RETENTION=10
animica node up
```

### Monitor Snapshot Creation

```bash
# Check logs for snapshot creation
animica node logs | grep "snapshot"

# Expected output:
# [INFO] SnapshotManager initialized: interval=2000, enabled=True
# [INFO] Triggering automatic snapshot creation at height 2000
# [INFO] Starting snapshot creation at height 2000
# [INFO] Snapshot created successfully at height 2000: blocks=2001, accounts=100
```

### Verify P2P Snapshot Sync

```bash
# Start fresh node (will attempt P2P snapshot sync)
rm -rf ~/.animica/chain-1
animica node up

# Check logs
animica node logs | grep "snapshot"

# Expected output:
# [INFO] Querying 5 peers for snapshots...
# [INFO] Discovered snapshot from peer: height=10000
# [INFO] Selected best snapshot: height=10000
# [INFO] Downloading snapshot from peer: height=10000
# [INFO] Snapshot download complete: 2 chunks
# [INFO] Successfully synced from snapshot: height=10000
```

### Manual Snapshot Operations

```bash
# Still supported: manual snapshot creation
animica snapshot create --height 5000

# List all snapshots (including auto-created)
animica snapshot list

# Output:
# Chain 1 - Height 10000
#   Hash: 0x1234...
#   Blocks: 10001
#   Accounts: 5000
#   Size: 150.00 MB
#   Path: ~/.animica/snapshots/chain-1-height-10000
# 
# Chain 1 - Height 8000
#   Hash: 0x5678...
#   ...
```

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────┐
│                    Block Import Layer                    │
│  (core/chain/block_import.py)                           │
│                                                          │
│  Block → Height Check → Trigger Snapshot at 2000n       │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│               Snapshot Manager                          │
│  (core/chain/snapshot_manager.py)                       │
│                                                          │
│  • Background thread pool                               │
│  • Pending task tracking                                │
│  • Automatic cleanup                                    │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│           Snapshot Export/Import                        │
│  (core/db/snapshot.py)                                  │
│                                                          │
│  • CBOR encoding                                        │
│  • Compression                                          │
│  • Hash verification                                    │
└─────────────────────────────────────────────────────────┘

                     ▲
                     │ (P2P Download)
                     │
┌─────────────────────────────────────────────────────────┐
│            P2P Snapshot Protocol                        │
│  (p2p/sync/snapshot_protocol.py)                        │
│                                                          │
│  • Peer discovery                                       │
│  • Chunk download                                       │
│  • Snapshot selection                                   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              P2P Message Layer                          │
│  (p2p/wire/messages.py)                                 │
│                                                          │
│  • Protocol messages (0x09xx)                           │
│  • Request/response pairing                             │
│  • CBOR encoding                                        │
└─────────────────────────────────────────────────────────┘
```

## Performance

### Snapshot Creation

| Metric | Value | Notes |
|--------|-------|-------|
| **Interval** | 2000 blocks | ~67 minutes at 2s/block |
| **Creation Time** | 10-30s | Depends on chain size |
| **Disk Usage** | ~150 MB/snapshot | Compressed CBOR |
| **CPU Usage** | Moderate | Background thread |
| **Blocking** | None | Async execution |

### P2P Snapshot Sync

| Metric | Value | Notes |
|--------|-------|-------|
| **Discovery** | 5-30s | Query 5 peers |
| **Download** | 2-10 min | Depends on network |
| **Import** | 2-5 min | Verify + DB writes |
| **Total** | 5-15 min | vs hours for full sync |
| **Speedup** | **10-50x** | Compared to genesis sync |

## Security Considerations

### Trust Model

1. **Snapshot Source Trust**
   - P2P: Trust peer's snapshot data
   - RPC: Trust centralized server
   - Recommendation: Use P2P from multiple peers

2. **Verification**
   - ✅ SHA3-256 hash verification of all chunks
   - ✅ Checkpoint hash validation
   - ✅ State root validation after import
   - ✅ Continue full validation from snapshot height

3. **Attack Vectors**
   - Malicious peer provides invalid snapshot
   - Mitigation: Hash verification, multiple peer sources
   - Corrupted data during download
   - Mitigation: Chunk-level hash verification

### Best Practices

1. ✅ **Enable hash verification** (default: on)
2. ✅ **Query multiple peers** for snapshot availability
3. ✅ **Cross-check checkpoint hashes** against trusted sources
4. ✅ **Run full validation** after snapshot import
5. ✅ **Keep automatic snapshots enabled** for decentralization

## Troubleshooting

### Snapshots Not Being Created

**Symptom:** No snapshots at ~/.animica/snapshots

**Check:**
```bash
# 1. Verify feature is enabled
echo $ANIMICA_SNAPSHOT_ENABLED  # Should be "true" or empty (default: true)

# 2. Check logs
animica node logs | grep "SnapshotManager"

# 3. Check current height
animica chain head
# Height should be >= 2000 for first snapshot
```

**Fix:**
```bash
# Enable explicitly
export ANIMICA_SNAPSHOT_ENABLED=true
animica node restart
```

### P2P Discovery Failing

**Symptom:** "No snapshots available from peers"

**Check:**
```bash
# 1. Verify peers are connected
animica p2p peers
# Should show connected peers

# 2. Check if peers have snapshots
# (Manual verification on peer nodes)

# 3. Check logs
animica node logs | grep "snapshot"
```

**Fix:**
```bash
# Use RPC fallback
export ANIMICA_SNAPSHOT_RPC_URL=http://snapshots.animica.org:8545/rpc
animica node restart
```

### Download Timeout

**Symptom:** "Snapshot download timed out"

**Check:**
```bash
# Check network connectivity
ping snapshots.animica.org

# Check timeout setting
echo $ANIMICA_SNAPSHOT_DOWNLOAD_TIMEOUT
```

**Fix:**
```bash
# Increase timeout
export ANIMICA_SNAPSHOT_DOWNLOAD_TIMEOUT=1200  # 20 minutes
animica node restart
```

### Disk Space Issues

**Symptom:** Snapshot creation fails with disk full error

**Check:**
```bash
# Check disk space
df -h ~/.animica

# Check snapshot directory size
du -h ~/.animica/snapshots
```

**Fix:**
```bash
# Reduce retention
export ANIMICA_SNAPSHOT_RETENTION=2
animica node restart

# Or manually clean up old snapshots
animica snapshot delete 2000
animica snapshot delete 4000
```

## Monitoring

### Metrics to Track

1. **Snapshot Creation**
   - Frequency: Every N blocks (default: 2000)
   - Duration: Typical 10-30s
   - Size: ~150 MB per snapshot

2. **P2P Discovery**
   - Peers queried: Default 5
   - Response rate: >50% (healthy network)
   - Selection time: <30s

3. **Download Performance**
   - Throughput: Depends on network
   - Success rate: >90% (healthy network)
   - Verification time: <60s

### Health Checks

```bash
# Check snapshot manager status
animica node logs | grep "SnapshotManager initialized"

# Check last snapshot created
ls -lh ~/.animica/snapshots/ | tail -1

# Check P2P snapshot capability
animica p2p peers | grep snapshot

# Check snapshot disk usage
du -sh ~/.animica/snapshots/
```

## Future Enhancements

### Planned (Phase 2)

- [ ] **Streaming downloads**: Download + import in parallel
- [ ] **Chunk prioritization**: Download critical chunks first
- [ ] **Multi-peer downloads**: Download chunks from multiple peers
- [ ] **Incremental snapshots**: Delta snapshots between intervals
- [ ] **Snapshot pruning API**: Automatic cleanup policies

### Considered (Phase 3)

- [ ] **BitTorrent protocol**: Efficient multi-peer distribution
- [ ] **Snapshot signing**: Cryptographic proof of authenticity
- [ ] **Compression levels**: Fast vs best tradeoff
- [ ] **Background verification**: Continuous snapshot validation
- [ ] **Snapshot marketplace**: Incentivized snapshot hosting

## References

- [SNAPSHOT_IMPLEMENTATION_SUMMARY.md](SNAPSHOT_IMPLEMENTATION_SUMMARY.md) - Original snapshot feature
- [CHAIN_SNAPSHOT_SYNC.md](CHAIN_SNAPSHOT_SYNC.md) - User guide
- [core/db/snapshot.py](core/db/snapshot.py) - Export/import implementation
- [core/chain/snapshot_manager.py](core/chain/snapshot_manager.py) - Automatic creation
- [p2p/sync/snapshot_protocol.py](p2p/sync/snapshot_protocol.py) - P2P protocol
- [p2p/wire/message_ids.py](p2p/wire/message_ids.py) - Message type definitions
