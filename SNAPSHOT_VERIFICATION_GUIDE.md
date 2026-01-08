# Snapshot System Verification and Usage Guide

## Overview

The Animica snapshot system enables fast chain synchronization by automatically creating, sharing, and using chain snapshots. This document explains how to verify the system is working and how to use it.

## Quick Verification

Run the verification script to check if the snapshot system is properly configured:

```bash
python3 scripts/verify_snapshot_system.py
```

This will check:
- ✅ Environment configuration
- ✅ RPC method registration  
- ✅ Disk snapshots existence
- ✅ BlockImporter configuration

## How It Works

### 1. Automatic Snapshot Creation

**When**: Snapshots are automatically created every 2000 blocks (configurable) as your node imports blocks.

**Where**: Snapshots are stored in `~/.animica/snapshots/chain-{id}-height-{height}/`

**How to verify**:
```bash
# Check if snapshots exist
ls -la ~/.animica/snapshots/

# Check logs for snapshot creation
grep -i "snapshot" ~/.animica/logs/*.log
```

Expected log messages:
```
INFO Creating disk snapshot at height 2000
INFO Snapshot created successfully at height 2000 (elapsed: 45.2s)
INFO Found 3 missing snapshots, will create in background
```

### 2. Snapshot Sharing via RPC

**What**: Snapshots are automatically shared via RPC methods that peers can query.

**How to verify**:
```bash
# List available snapshots on your node
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "snapshot.list",
    "params": {"chain_id": 1}
  }'
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "success": true,
    "snapshots": [
      {
        "chain_id": 1,
        "checkpoint_height": 2000,
        "checkpoint_hash": "0x...",
        "blocks_count": 2000,
        "accounts_count": 150,
        "size_mb": 42.5
      }
    ],
    "count": 1
  }
}
```

### 3. Automatic Snapshot Discovery and Sync

**When**: New nodes starting from genesis will automatically:
1. Query connected peers for available snapshots
2. Download the highest available snapshot
3. Import the snapshot for fast sync
4. Continue P2P sync from snapshot height

**How to verify**:
```bash
# Start a fresh node
rm -rf /tmp/test-node-data
ANIMICA_DATA_DIR=/tmp/test-node-data animica node up

# Check logs for snapshot bootstrap
grep -i "snapshot" /tmp/test-node-data/logs/*.log
```

Expected log messages:
```
INFO Querying 3 peer(s) for available snapshots
INFO Peer 10.0.0.1:30303 has 2 snapshot(s): heights [2000, 4000]
INFO Found best snapshot at height 4000
INFO Downloading snapshot to temporary directory
INFO Successfully imported downloaded snapshot
INFO Snapshot bootstrap completed successfully
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_SNAPSHOT_INTERVAL` | `2000` | Blocks between automatic snapshots |
| `ANIMICA_SNAPSHOT_AUTO_CREATE` | `true` | Enable/disable auto-creation |
| `ANIMICA_SNAPSHOT_SYNC_ENABLED` | `true` | Enable/disable snapshot-based sync |
| `ANIMICA_SNAPSHOT_RPC_URL` | _(none)_ | Optional static RPC source (uses peer discovery if not set) |
| `ANIMICA_SNAPSHOT_MIN_HEIGHT` | `1000` | Minimum height gap to use snapshots |
| `ANIMICA_SNAPSHOT_TIMEOUT` | `600` | Timeout for snapshot operations (seconds) |

### Example Configuration

```bash
# Enable snapshot creation every 5000 blocks
export ANIMICA_SNAPSHOT_INTERVAL=5000

# Use a trusted snapshot source
export ANIMICA_SNAPSHOT_RPC_URL=http://snapshots.animica.org:8545/rpc

# Start node
animica node up
```

## Manual Operations

### Create Snapshot Manually

```bash
# Via CLI
animica snapshot create --height 10000

# Via RPC
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "snapshot.create",
    "params": {"height": 10000, "compress": true}
  }'
```

### List Available Snapshots

```bash
# Via CLI
animica snapshot list

# Via RPC
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "snapshot.list",
    "params": {}
  }'
```

### Delete Old Snapshots

```bash
# Via CLI
animica snapshot delete 2000

# Via RPC
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "snapshot.delete",
    "params": {"height": 2000}
  }'
```

## Troubleshooting

### Snapshots Not Being Created

**Check 1**: Verify auto-creation is enabled
```bash
python3 scripts/verify_snapshot_system.py
```

**Check 2**: Verify node is importing blocks
```bash
animica sync status
```

**Check 3**: Check logs for errors
```bash
grep -i "snapshot" ~/.animica/logs/*.log | grep -i "error\|fail"
```

**Common Causes**:
- Node not actively importing blocks
- Disk space insufficient
- `ANIMICA_SNAPSHOT_AUTO_CREATE=false` set
- Height not yet reached snapshot interval (2000, 4000, etc.)

### Snapshots Not Being Discovered by Peers

**Check 1**: Verify RPC server is running and accessible
```bash
curl http://127.0.0.1:8545/health
```

**Check 2**: Verify snapshots are listed via RPC
```bash
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"snapshot.list","params":{}}'
```

**Check 3**: Verify peer connectivity
```bash
animica net peers
```

**Common Causes**:
- RPC not accessible from peer network
- Firewall blocking port 8545
- No snapshots created yet
- Peers not connected

### New Nodes Not Using Snapshots

**Check 1**: Verify snapshot sync is enabled
```bash
echo $ANIMICA_SNAPSHOT_SYNC_ENABLED  # Should be "true" or empty (defaults to true)
```

**Check 2**: Verify node height is below threshold
```bash
# Snapshot sync only works if current height < ANIMICA_SNAPSHOT_MIN_HEIGHT (default: 1000)
animica sync status
```

**Check 3**: Check logs for bootstrap attempt
```bash
grep -i "snapshot bootstrap\|snapshot sync" ~/.animica/logs/*.log
```

**Common Causes**:
- Node already synced past minimum height
- No peers available with snapshots
- Network connectivity issues
- `ANIMICA_SNAPSHOT_SYNC_ENABLED=false` set

## Performance Expectations

### Snapshot Creation
- **Time**: ~30-60 seconds per 2000 blocks
- **Disk I/O**: High (sequential writes)
- **CPU**: Moderate (compression)
- **Blocks import**: Not blocked (asynchronous creation)

### Snapshot Download and Import
- **Download**: ~5-15 minutes for 100K blocks (depends on network)
- **Import**: ~2-5 minutes for 100K blocks
- **Total**: **~7-20 minutes vs 2-6 hours** for full P2P sync

### Disk Space
- **Per Snapshot**: ~100-500MB (compressed)
- **Accumulation**: Snapshots accumulate over time
- **Management**: Manually delete old snapshots if disk space is limited

## Best Practices

### For Node Operators

1. **Enable auto-creation** (default): Let your node create snapshots automatically
2. **Ensure RPC accessibility**: Allow peers to access your RPC on port 8545
3. **Monitor disk space**: Delete old snapshots if space is limited
4. **Check logs regularly**: Watch for snapshot creation/sync messages

### For Devnet/Testnet Operators

1. **Set snapshot interval appropriately**: Smaller intervals for faster testing
   ```bash
   export ANIMICA_SNAPSHOT_INTERVAL=1000  # More frequent snapshots
   ```

2. **Configure a trusted snapshot source**: Speed up devnet node bootstrapping
   ```bash
   export ANIMICA_SNAPSHOT_RPC_URL=http://devnet-snapshots.local:8545/rpc
   ```

3. **Pre-create snapshots**: Manually create snapshots at key heights
   ```bash
   animica snapshot create --height 10000
   ```

### For End Users

1. **Use default settings**: Snapshot sync is automatic with peer discovery
2. **Check sync progress**: Monitor logs to see if snapshot was used
   ```bash
   grep -i "snapshot bootstrap" ~/.animica/logs/*.log
   ```

3. **Be patient**: Initial download may take time depending on network

## Integration with Other Systems

### With CI/CD

```bash
# In CI/CD pipeline, disable snapshots to save disk space
export ANIMICA_SNAPSHOT_AUTO_CREATE=false
export ANIMICA_SNAPSHOT_SYNC_ENABLED=false
```

### With Docker

```yaml
# docker-compose.yml
services:
  animica-node:
    image: animica/node:latest
    environment:
      - ANIMICA_SNAPSHOT_INTERVAL=2000
      - ANIMICA_SNAPSHOT_AUTO_CREATE=true
      - ANIMICA_SNAPSHOT_SYNC_ENABLED=true
    volumes:
      - ./data:/root/.animica
      - ./snapshots:/root/.animica/snapshots
```

### With Monitoring

```bash
# Monitor snapshot creation
watch -n 10 'ls -lh ~/.animica/snapshots/'

# Monitor snapshot usage
tail -f ~/.animica/logs/node.log | grep -i snapshot
```

## Additional Resources

- **Full Documentation**: See `SNAPSHOT_AUTO_CREATION.md`
- **P2P Discovery**: See `P2P_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md`  
- **Sync Details**: See `CHAIN_SNAPSHOT_SYNC.md`
- **Verification Script**: Run `python3 scripts/verify_snapshot_system.py`
- **Integration Tests**: See `tests/integration/test_snapshot_end_to_end.py`

## Support

If you encounter issues not covered here:

1. Run the verification script: `python3 scripts/verify_snapshot_system.py`
2. Check logs for errors: `grep -i "snapshot.*error" ~/.animica/logs/*.log`
3. Report issues with:
   - Verification script output
   - Relevant log snippets
   - Node configuration (env vars)
   - Node height and sync status
