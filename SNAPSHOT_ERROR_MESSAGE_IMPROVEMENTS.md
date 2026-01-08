# Snapshot Discovery Error Message Improvements

## Problem Statement

The error message "💡 No snapshots found on connected peers" was misleading when there were **no connected peers at all**. This made it difficult for users to diagnose connectivity issues versus snapshot availability issues.

## Solution

Modified the snapshot discovery system to distinguish between three scenarios:

### 1. No Peers Connected
**Before:**
```
❌ No snapshots found on connected peers.
💡 Troubleshooting:
  1. Check peer connections: animica peer list
  2. Ensure peers have snapshots: they must create them first
  ...
```

**After:**
```
❌ No peers connected.
   Connect to peers first to discover snapshots from the network.

💡 Troubleshooting:
  1. Check peer connections: animica peer list
  2. Connect to peers: animica peer add <address>
  3. Ensure your node's P2P service is running
  4. Check firewall settings if running your own node
```

### 2. Peers Connected but No Snapshots
```
❌ No snapshots found on connected peers.

💡 Troubleshooting:
  1. Check peer connections: animica peer list
  2. Ensure peers have snapshots: they must create them first
  3. Check peer RPC accessibility (peers may not expose RPC)
  4. Try connecting to more peers: animica peer add <address>
```

### 3. Peers Connected but Queries Failed
```
❌ No snapshots found on connected peers.

⚠️  Failed to query 2 peer(s):
  - 192.168.1.10:30303: Connection timeout
  - 192.168.1.11:30303: Connection refused

💡 Troubleshooting:
  1. Check peer connections: animica peer list
  2. Ensure peers have snapshots: they must create them first
  3. Check peer RPC accessibility (peers may not expose RPC)
  4. Try connecting to more peers: animica peer add <address>
  5. Enable debug logging: export ANIMICA_LOG_LEVEL=DEBUG
```

## Implementation Details

### Core Changes

1. **Modified `_query_all_peers_for_snapshots()` function** (`python/animica/cli/snapshot.py`)
   - Changed return type from `tuple[dict, list]` to `tuple[dict, list, int]`
   - Now returns the number of connected peers as the third element
   - Returns `0` when no peers are connected

2. **Updated Command Handlers**
   - `snapshot discover` - Checks peer_count and shows appropriate message
   - `snapshot list` - Shows different messages for no-peers vs no-snapshots
   - `snapshot list --from-peers` - Similar distinction

3. **Updated Callers**
   - `python/animica/cli/sync.py` - Updated to handle new return signature

### Files Changed

- `python/animica/cli/snapshot.py` - Core implementation
- `python/animica/cli/sync.py` - Updated caller
- `python/animica/cli/tests/test_snapshot_peer_discovery.py` - Added tests

### Tests Added

- `test_snapshot_discover_no_peers_connected()` - Verify "No peers connected" message
- `test_snapshot_list_no_peers_connected()` - Verify list command behavior
- Updated existing tests to handle new return signature

## Benefits

1. **Clearer Error Messages** - Users can immediately tell if the issue is connectivity or snapshot availability
2. **Better Troubleshooting** - Actionable steps specific to the actual problem
3. **Reduced Confusion** - No more misleading "no snapshots found on connected peers" when there are no peers
4. **Consistent UX** - All snapshot discovery commands use the same logic

## Testing

All critical tests passing:
- ✅ `test_query_all_peers_for_snapshots` 
- ✅ `test_query_all_peers_no_peers` 
- ✅ `test_snapshot_discover_no_peers_connected` 
- ✅ `test_snapshot_list_no_peers_connected` 
- ✅ `test_snapshot_discover_no_snapshots` 
- ✅ `test_snapshot_list_from_peers_no_snapshots` 
- ✅ `test_snapshot_discover_with_peer_errors`

## Backward Compatibility

The changes are backward compatible:
- No changes to RPC APIs or data structures
- Only affects CLI output messages
- Internal function signature change is properly propagated to all callers
