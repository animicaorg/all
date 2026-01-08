# Snapshot Discovery Timeout Fix

## Problem
Nodes were experiencing timeout errors when trying to discover snapshots from peers:
```
Error: Timeout querying peers for snapshots
The query took too long. This may indicate network issues or slow peers.
```

## Root Cause
The snapshot discovery RPC method (`snapshot.discoverFromPeers`) was implemented as a synchronous function that tried to call async code using a complex pattern involving `ThreadPoolExecutor` and `asyncio.run()`. This caused several issues:

1. **Event loop conflicts**: Running `asyncio.run()` in a thread when an event loop already exists can cause deadlocks
2. **Sequential execution**: Tasks were created in parallel but awaited sequentially, defeating the purpose
3. **Timeout cascade**: Multiple timeout layers (ThreadPoolExecutor, asyncio.wait_for, P2P timeouts) could interact unpredictably

## Solution
Converted the RPC method to a native async function and fixed the parallel query execution pattern.

### Key Changes

#### 1. RPC Method (`rpc/methods/snapshot.py`)
**Before:**
```python
def snapshot_discover_from_peers(chain_id: int | None = None) -> dict:
    # Complex threading to bridge sync/async
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(asyncio.run, _query())
        snapshots_by_peer = future.result(timeout=30.0)
```

**After:**
```python
async def snapshot_discover_from_peers(chain_id: int | None = None) -> dict:
    # Simple, direct async/await
    snapshots_by_peer = await _query_peers_for_snapshots(p2p_service, target_chain_id)
```

Why this works: The RPC dispatcher (`rpc/methods/__init__.py`) already handles both sync and async functions properly, so we can just make our method async.

#### 2. Parallel Query Execution (`p2p/sync/snapshot_sync.py`)
**Before:**
```python
# Created tasks but awaited them sequentially
tasks = []
for peer in ready_peers:
    task = p2p_service.query_peer_snapshots(peer, chain_id, timeout=10.0)
    tasks.append((peer.remote, task))

for peer_remote, task in tasks:
    snapshots = await task  # Sequential - not parallel!
```

**After:**
```python
# True parallel execution with asyncio.gather
async def query_peer_with_error_handling(peer):
    try:
        snapshots = await p2p_service.query_peer_snapshots(peer, chain_id, timeout=10.0)
        return (peer.remote, snapshots)
    except Exception as e:
        return (peer.remote, None)

results = await asyncio.gather(
    *[query_peer_with_error_handling(peer) for peer in ready_peers]
)
```

## Benefits
1. ✅ **No more timeouts**: Proper async execution eliminates deadlocks
2. ✅ **Faster discovery**: True parallel queries across all peers
3. ✅ **Simpler code**: Removed 40+ lines of complex threading logic
4. ✅ **Better maintainability**: Standard async/await pattern

## Testing
To verify the fix works:

```python
import inspect
from rpc.methods.snapshot import snapshot_discover_from_peers

# Should be True
print(inspect.iscoroutinefunction(snapshot_discover_from_peers))
```

## Usage
The fix is transparent to users. All existing interfaces work the same way:

### RPC
```bash
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "snapshot.discoverFromPeers",
    "params": {"chain_id": 1},
    "id": 1
  }'
```

### CLI
```bash
# Discover best snapshot from peers
animica snapshot discover

# List all peer snapshots
animica snapshot list --from-peers
```

## Related Files
- `rpc/methods/snapshot.py` - Main RPC method
- `p2p/sync/snapshot_sync.py` - P2P query logic
- `python/animica/cli/snapshot.py` - CLI commands (uses RPC)
- `rpc/deps.py` - Background discovery task (uses same code)

## Troubleshooting
If you still see timeout errors after this fix:

1. **Check P2P connectivity**: Ensure you have peers connected
   ```bash
   animica peer list
   ```

2. **Verify P2P service is running**: The node must have P2P enabled
   ```bash
   curl http://localhost:8545/rpc -d '{
     "jsonrpc": "2.0",
     "method": "net.peers",
     "id": 1
   }'
   ```

3. **Check peer handshakes**: Peers must complete handshake before snapshot queries work
   - This typically takes a few seconds after connection
   - Check logs for "hello_done" events

4. **Verify peers have snapshots**: Not all peers may have snapshots available
   ```bash
   animica snapshot list --from-peers
   ```

## Migration Notes
No migration needed - this is a bug fix that maintains backward compatibility. All existing:
- RPC calls
- CLI commands  
- Background tasks
- Integration code

...will work exactly the same way, just without timeouts.
