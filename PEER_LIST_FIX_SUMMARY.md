# Peer List CLI Fix - Implementation Summary

## Issue Fixed
`animica peer list` showed "No peers connected" after node startup, even though the node was running and P2P service was initialized.

## Root Cause
The P2P service (`P2PService`) was created during RPC context initialization but:
1. Never registered with the global service registry (`p2p.register_service()`)
2. Never started (`await p2p_service.start()`)
3. RPC methods couldn't access it to retrieve peer data

## Solution Overview

### Minimal Changes (Surgical Fix)
- **1 import added**: `import p2p` in `rpc/deps.py`
- **1 function call added**: `p2p.register_service(p2p_service)` in `rpc/deps.py`
- **2 lifecycle calls added**: `await p2p_service.start()` and `await p2p_service.stop()`
- **1 helper function added**: `_get_p2p_service()` in `rpc/methods/p2p.py`
- **2 functions refactored**: `list_peers()` and `add_peer()` to use P2PService directly

### Files Modified
1. `rpc/deps.py` - Register and manage P2P service lifecycle (20 lines changed)
2. `rpc/methods/p2p.py` - Access P2PService via global registry (150 lines refactored)

### Files Verified (No Changes)
- `ops/docker/docker-compose.mainnet.yml` - RPC port mapping correct ✓
- `python/animica/config.py` - Network config correct ✓
- `p2p/__init__.py` - Global registry already exists ✓
- `p2p/node/service.py` - P2PService structure correct ✓
- `python/animica/cli/peer.py` - CLI logic correct ✓

## Technical Details

### Global Service Registry Pattern
The fix uses the existing global service registry in `p2p/__init__.py`:

```python
# Register during initialization
p2p.register_service(p2p_service)

# Access from RPC methods
service = p2p.get_service()
if service and hasattr(service, 'peers'):
    connected_peers = service.peers
```

### P2PService Lifecycle
```
1. RPC Context Build → Create P2PService → Register globally
2. RPC Startup → await p2p_service.start() → Bind listeners, connect to seeds
3. Running → RPC methods access via p2p.get_service()
4. RPC Shutdown → await p2p_service.stop() → Close connections
```

### RPC Method Fallback Chain
`list_peers()` tries multiple sources in order:
1. P2PService.peers property (lightweight service) ✅ Primary
2. ConnectionManager.list_peers() (full NodeService) - Advanced deployments
3. PeerStore.list_known() (persistent storage) - Fallback when RPC unavailable

## Testing

### Unit Tests ✅
- P2P service registration works
- Global registry functions correctly
- RPC methods can access registered service

### Integration Tests ✅
- `list_peers()` returns correct peer data
- Peer objects properly serialized to JSON
- Multiple peer sources work correctly

### E2E Simulation ✅
Complete flow verified:
1. RPC starts → P2P service initialized and registered
2. P2P service starts → Connects to seed nodes
3. CLI calls `p2p.listPeers` RPC method
4. RPC retrieves peers from P2PService.peers
5. CLI displays peer information

### Code Review ✅
- Minor logging optimization suggestions (not blocking)
- Code consistent with existing patterns in codebase
- No security vulnerabilities detected

## Expected Behavior

### Before Fix
```bash
animica peer list
No peers connected.
```

### After Fix
```bash
animica peer list

Connected Peers: 2

1. Peer: peer_mainnet_seed1
   Address: 5.189.152.183:30333
   Status: connected
   Direction: outbound

2. Peer: peer_mainnet_seed2
   Address: 144.126.133.21:30333
   Status: connected
   Direction: outbound
```

## Configuration

### Environment Variables
- `ANIMICA_P2P_ENABLE=true` (default) - Enable P2P service
- `P2P_SEEDS="seed1,seed2,..."` - Configure seed nodes
- `ANIMICA_PEER_STORE_PATH` - Custom peer store location

### Docker Compose
Already correctly configured:
- RPC port: `0.0.0.0:8545:8545` ✓
- P2P port: `30333:30333` ✓
- P2P enabled by default ✓

## Deployment Notes

### Zero Downtime
- Changes are backwards compatible
- P2P can be disabled if needed
- RPC methods gracefully degrade if P2P unavailable

### Monitoring
RPC logs will show:
```
[INFO] Initialized and registered P2P service with peer store at ~/.animica/p2p/mainnet
[INFO] P2P service started successfully
[INFO] Started full P2P service (listeners: ['/ip4/0.0.0.0/tcp/30333'])
```

### Troubleshooting
If peer list still shows empty:
1. Check P2P_ENABLE=true in environment
2. Check RPC server logs for P2P service start messages
3. Verify seeds are configured (P2P_SEEDS or network defaults)
4. Check firewall allows inbound connections on P2P port

## Related Work

This fix builds on:
- PR #446: Persistent peer storage (PeerStore)
- PR #447: CLI display and seed nodes
- PR #448: Inbound connection tracking

## Performance Impact

**Negligible**: 
- Service registration is one-time at startup
- P2P service runs asynchronously
- No blocking operations in request path
- `len()` operations on debug logs are O(1)

## Security

**No vulnerabilities introduced**:
- ✅ CodeQL analysis passed
- ✅ No new external dependencies
- ✅ No new network exposure
- ✅ Proper error handling and fallbacks

## Conclusion

**Impact**: High - Fixes user-facing CLI command
**Risk**: Low - Minimal changes, well tested, backwards compatible
**Complexity**: Low - Uses existing infrastructure and patterns

The fix successfully enables the peer list CLI to access real-time peer data from the running node via RPC, providing visibility into P2P network connectivity.
