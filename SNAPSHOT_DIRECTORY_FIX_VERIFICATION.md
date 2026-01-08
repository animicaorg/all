# Snapshot Directory Fix - Verification Guide

## Problem Summary

Snapshots were being created automatically every 2000 blocks but were not discoverable via RPC. When users ran `animica snapshot list` or `animica snapshot list --from-peers`, no snapshots were found even though they existed on disk.

## Root Cause

The `_get_snapshots_dir()` function in `rpc/methods/snapshot.py` was trying to access `ctx.cfg.data_dir`, which doesn't exist in the `_ConfigView` dataclass. This caused the RPC layer to look in the wrong directory for snapshots.

## Solution

Fixed the RPC snapshot methods to use `ctx.data_root` from the RPC context, and updated BlockImporter to use consistent directory resolution logic.

## How to Verify the Fix

### 1. Check Snapshot Creation (Local Node)

```bash
# Start a node and let it sync some blocks
animica node start

# Wait for blocks to be mined/synced past height 2000

# Check if snapshots were created automatically
animica snapshot list

# Expected output: Should show snapshots at heights 2000, 4000, 6000, etc.
```

### 2. Check Snapshot Directory

```bash
# Check the snapshots directory
ls -la ~/.animica/snapshots/

# Expected: Should see directories like:
# chain-1-height-2000/
# chain-1-height-4000/
# chain-1-height-6000/
# etc.
```

### 3. Check RPC Endpoint Directly

```bash
# Query the RPC endpoint for snapshots
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "snapshot.list",
    "params": {}
  }'

# Expected: JSON response with "success": true and list of snapshots
```

### 4. Test Peer Discovery

```bash
# On one node, check peers
animica peer list

# Query peers for snapshots
animica snapshot list --from-peers

# Expected: Should show snapshots from connected peers
```

### 5. Test Snapshot Discovery

```bash
# Find the best available snapshot from peers
animica snapshot discover

# Expected: Should show the highest height snapshot available from peers
```

## Environment Variables

The following environment variables affect snapshot directory resolution:

- `ANIMICA_DATA_DIR`: Base directory for chain data
  - If set: snapshots go in `$ANIMICA_DATA_DIR/snapshots`
  - If not set: snapshots go in `~/.animica/snapshots`

- `ANIMICA_SNAPSHOT_INTERVAL`: How often to create snapshots (default: 2000 blocks)

- `ANIMICA_SNAPSHOT_AUTO_CREATE`: Enable/disable automatic snapshot creation (default: true)

## Directory Structure

```
~/.animica/                     # Base directory (or $ANIMICA_DATA_DIR)
├── chain-1/                    # Chain-specific data (DB, etc.)
│   ├── animica.db              # Blocks and state
│   └── ...
└── snapshots/                  # Global snapshots directory
    ├── chain-1-height-2000/    # Snapshot for chain 1 at height 2000
    │   ├── manifest.json
    │   ├── blocks.cbor.gz
    │   └── state.cbor.gz
    ├── chain-1-height-4000/    # Snapshot for chain 1 at height 4000
    └── ...
```

## Testing Checklist

- [ ] Snapshots are created automatically at configured intervals
- [ ] `animica snapshot list` returns local snapshots
- [ ] `animica snapshot list --from-peers` returns peer snapshots
- [ ] `animica snapshot discover` finds best snapshot
- [ ] RPC endpoint `snapshot.list` returns correct results
- [ ] Snapshots are created in the correct directory
- [ ] Multiple chains can coexist (snapshots named with chain ID)

## Troubleshooting

### "No snapshots found on local node"

1. Check if snapshots are being created:
   ```bash
   ls -la ~/.animica/snapshots/
   ```

2. Check if auto-creation is enabled:
   ```bash
   echo $ANIMICA_SNAPSHOT_AUTO_CREATE  # Should be unset or "true"
   ```

3. Check node logs for snapshot creation messages:
   ```bash
   grep -i snapshot ~/.animica/logs/node.log
   ```

### "No snapshots found on connected peers"

1. Check if peers are connected:
   ```bash
   animica peer list
   ```

2. Check if peers have snapshots:
   ```bash
   # Query a specific peer's RPC endpoint
   curl -X POST http://<peer-ip>:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"snapshot.list","params":{}}'
   ```

3. Check firewall rules allow RPC connections (port 8545)

### Snapshots in wrong directory

If you have `ANIMICA_DATA_DIR` set, snapshots will be in:
```bash
$ANIMICA_DATA_DIR/snapshots/
```

Otherwise, they'll be in:
```bash
~/.animica/snapshots/
```

To check which is being used:
```bash
echo $ANIMICA_DATA_DIR
```

## Related Files

- `rpc/methods/snapshot.py`: RPC methods for snapshot management
- `core/chain/block_import.py`: Automatic snapshot creation logic
- `p2p/sync/snapshot_sync.py`: P2P snapshot synchronization
- `python/animica/cli/snapshot.py`: CLI commands for snapshot management

## Additional Notes

- Snapshots are created asynchronously in background threads to avoid blocking block import
- Snapshot creation can take several minutes for large state
- Snapshots are compressed by default to save disk space
- Each snapshot is self-contained and can be used for fast sync independently
