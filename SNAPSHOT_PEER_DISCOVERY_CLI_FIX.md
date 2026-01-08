# Snapshot Peer Discovery Fix - Summary

## Problem Resolved

When resetting nodes or syncing from scratch, snapshots existed on other nodes/peers but were not discoverable. Users would see "No snapshots found" even though snapshots were available on connected peers.

## Solution

Added peer snapshot discovery functionality to the CLI commands, enabling nodes to query connected peers for available snapshots.

## New Features

### 1. Query Peers for Snapshots

```bash
# List snapshots from all connected peers
animica snapshot list --from-peers
```

Output:
```
Querying connected peers for snapshots...

Found 3 snapshot(s) from 2 peer(s):

Chain 1 - Height 2000
  Hash: 0xbbb...
  Blocks: 2001
  Accounts: 100
  Size: 20.30 MB
  Source: 192.168.1.10:30303

Chain 1 - Height 1500
  Hash: 0xccc...
  Blocks: 1501
  Accounts: 75
  Size: 15.80 MB
  Source: 192.168.1.11:30303

Snapshots by peer:
  http://192.168.1.10:8545/rpc: 2 snapshot(s) at heights [1000, 2000]
  http://192.168.1.11:8545/rpc: 1 snapshot(s) at heights [1500]
```

### 2. Discover Best Snapshot

```bash
# Find the highest snapshot from all peers
animica snapshot discover
```

Output:
```
🔍 Discovering snapshots from connected peers...

✅ Found 3 total snapshot(s) from 2 peer(s)

🏆 Best snapshot (highest height):
  Chain ID:         1
  Height:           2000
  Hash:             0xbbb...
  Blocks:           2001
  Accounts:         100
  Size:             20.30 MB
  Source Peer:      192.168.1.10:30303
  Source RPC:       http://192.168.1.10:8545/rpc

💡 To use this snapshot for fast sync:
  1. Ensure ANIMICA_SNAPSHOT_SYNC_ENABLED=true (default)
  2. Restart your node - it will auto-discover and use this snapshot
  3. Or manually query: animica snapshot get 2000
```

### 3. Sync Status Shows Available Snapshots

```bash
animica sync status
```

When your node is significantly behind the network, sync status will now automatically check for available snapshots:

```
🔍 Checking for available snapshots from peers...

✨ Snapshot available at height 2000 from peer 192.168.1.10:30303
   Use snapshots for faster sync:
   - Restart node with ANIMICA_SNAPSHOT_SYNC_ENABLED=true (default)
   - Or view snapshots: animica snapshot list --from-peers
   - Or discover best: animica snapshot discover
```

## How It Works

1. **Peer Query**: The CLI queries all connected peers via RPC for their available snapshots
2. **Parallel Discovery**: All peers are queried simultaneously for efficiency
3. **Aggregation**: Snapshots from all peers are collected and sorted by height
4. **Best Selection**: The highest snapshot (most recent) is identified as the best candidate

## Technical Implementation

### New Functions

- `_get_peers()` - Retrieves connected peers from the node
- `_query_peer_snapshots()` - Queries a single peer for its snapshots
- `_query_all_peers_for_snapshots()` - Queries all peers in parallel

### Updated Commands

- `animica snapshot list --from-peers` - New flag to query peers
- `animica snapshot discover` - New command to find best snapshot
- `animica sync status` - Enhanced to show available snapshots

## Benefits

1. **Zero Configuration**: No need to manually configure snapshot sources
2. **Automatic Discovery**: Finds snapshots from any connected peer
3. **Best Selection**: Always recommends the highest (most recent) snapshot
4. **Fast Sync**: Users can quickly identify and use available snapshots
5. **Better Visibility**: Clear view of snapshot availability across the network

## Usage Examples

### Scenario 1: New Node Joining Network

```bash
# Check if any peers have snapshots
animica snapshot discover

# If snapshots found, restart node to use them
# Node will automatically discover and use the best snapshot
animica node restart
```

### Scenario 2: Node Reset/Resync

```bash
# After resetting data, check available snapshots
animica snapshot list --from-peers

# View sync status to see recommendations
animica sync status

# Node will auto-discover snapshots on restart
```

### Scenario 3: Verifying Snapshot Availability

```bash
# Check local snapshots
animica snapshot list

# Check peer snapshots
animica snapshot list --from-peers

# Compare and verify
```

## Troubleshooting

### "No snapshots found on connected peers"

**Causes:**
- No peers are connected
- Connected peers don't have snapshots
- Peer RPC endpoints are not accessible

**Solutions:**
1. Check peer connections: `animica peer list`
2. Add more peers: `animica peer add <address>`
3. Verify peers have created snapshots
4. Check network/firewall settings

### Peer RPC Not Accessible

Peers must expose their RPC endpoints for snapshot discovery to work. By default, the system assumes peers run RPC on port 8545.

**Configuration:**
- Ensure peers allow RPC connections
- Check firewall rules permit port 8545
- Verify peer addresses are correct

## Implementation Notes

- Snapshot discovery is **non-blocking** - failures on individual peers don't prevent discovery from other peers
- All peer queries have **timeouts** to prevent hanging
- Queries are made in **parallel** for efficiency
- Results include **source tracking** so users know which peer has which snapshot

## See Also

- [CHAIN_SNAPSHOT_SYNC.md](../CHAIN_SNAPSHOT_SYNC.md) - Overall snapshot sync documentation
- [P2P_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md](../P2P_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md) - Node startup snapshot discovery
- [SNAPSHOT_AUTO_CREATION.md](../SNAPSHOT_AUTO_CREATION.md) - Automatic snapshot creation

## Testing

Unit tests verify:
- Peer query functionality
- Snapshot aggregation from multiple peers
- Best snapshot selection
- Error handling for unavailable peers
- JSON output formatting

Run tests:
```bash
pytest python/animica/cli/tests/test_snapshot_peer_discovery.py -v
```
