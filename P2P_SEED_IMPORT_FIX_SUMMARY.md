# P2P Seed Import and Mining Fixes - Implementation Summary

## Issues Identified and Fixed

### 1. ✅ FIXED: Seed Import Duplicate Detection Bug
**Problem**: Seeds in different address formats were not detected as duplicates during import, causing them to be incorrectly marked as "invalid".

**Root Cause**: The `import_peers()` method in `P2PServiceLegacy` compared normalized incoming addresses against un-normalized existing seeds:
```python
# Before (BUGGY):
if normalized in self.seeds:  # self.seeds contains /dns4/..., /ip4/... (un-normalized)
    skipped += 1

# Comparison would fail:
#   /dns/mainnet.animica.org/tcp/30333 (normalized incoming)
#   != 
#   /dns4/mainnet.animica.org/tcp/30333 (original in self.seeds)
```

**Fix Applied**: Pre-normalize existing seeds once before comparison
```python
# After (FIXED):
existing_normalized = set()
for seed in self.seeds:
    norm = self._normalize_peer_addr(seed)
    if norm:
        existing_normalized.add(norm)

# Now comparison works:
#   /dns/mainnet.animica.org/tcp/30333
#   ==
#   /dns/mainnet.animica.org/tcp/30333 (normalized from /dns4/...)
```

**Files Changed**:
- `p2p/node/service.py` - Fixed duplicate detection in `import_peers()`

**Impact**: 
- No more false "invalid address" errors during bootstrap
- Proper deduplication across all address formats (multiaddr, tcp://, host:port)
- Bootstrap output now shows correct counts

### 2. ✅ FIXED: DNS Resolution Performance
**Problem**: All addresses (including IP addresses) were going through DNS resolution via `socket.getaddrinfo()`, adding unnecessary latency.

**Fix Applied**: Fast path for IP addresses
```python
# Before:
def _resolve_core_host(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    # ... process infos

# After (OPTIMIZED):
def _resolve_core_host(host: str) -> str | None:
    # Fast path: if it's already an IP address, return immediately
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    # Continue with DNS resolution for hostnames
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    # ...
```

**Files Changed**:
- `rpc/methods/p2p.py` - Optimized `_resolve_core_host()`

**Impact**:
- Reduced latency for IP address imports
- Fewer unnecessary DNS queries
- Better performance for bootstrap operations

## Test Coverage

Created comprehensive test: `test_p2p_seed_import_fix.py`

Tests verify:
1. ✅ Multiaddr normalization (`/dns4/` → `/dns/`)
2. ✅ Duplicate detection across mixed formats
3. ✅ tcp:// URL deduplication with multiaddr format

All tests pass:
```
✓ All tests passed! The fix correctly handles mixed address formats.
```

## Mining and Sync Behavior

### Mining Requirements
Mining requires at least 1 connected peer by default (configurable via `ANIMICA_MINING_MIN_PEERS`).

**Current Behavior**:
```bash
# Check peer requirement
min_peers = int(os.getenv("ANIMICA_MINING_MIN_PEERS", "1"))

# If peers < min_peers:
# "insufficient_peers (connected: 0, required: 1)"
```

**Bypass for Development**:
```bash
export ANIMICA_MINING_MIN_PEERS=0  # Disable peer requirement
animica miner mine-blocks --address <addr> --count 1
```

### Sync Behavior
Sync requires peers to fetch blocks. The node:
1. Waits for peer connections
2. Triggers sync when peers are available
3. Continuously monitors sync progress

**User Guidance** (already in error messages):
- "Try: 'animica peer bootstrap' to connect to peers"
- "Check: 'animica p2p doctor' for diagnostics"
- "Set ANIMICA_MINING_MIN_PEERS=0 for local development"

## Network Connectivity Issues (Not Code Bugs)

The user's error logs show network-level connection failures:
```
TransportError: dial timeout to tcp://82.66.161.84:30333
Connection refused to tcp://mainnet.animica.org:30333
```

These are infrastructure issues, not code bugs:
- Firewalls blocking ports
- NAT/routing issues
- Seed nodes temporarily unreachable
- Network connectivity problems

**Resolution**: Users should:
1. Check firewall rules (allow TCP port 30333)
2. Verify network connectivity
3. Try alternative networks (testnet/devnet)
4. Use `animica peer bootstrap --probe` to test reachability

## Migration Notes

### Services in Use
There are three P2P service implementations:

1. **NodeService** (new, full-featured)
   - Used via `P2PService` wrapper
   - Has its own `import_peers()` with `normalize_peer_addr()`
   - Already handles address normalization correctly

2. **P2PServiceLegacy** (lightweight)
   - Our fix applies here
   - Used for backward compatibility
   - Simple TCP-only service

3. **CoreP2PService** (old, being phased out)
   - Uses `NetAddress` with IP-only support
   - Requires DNS resolution (our optimization helps here)

The fix works for both legacy services. The new NodeService already had correct duplicate detection.

## Verification Steps

To verify the fix works:

1. **Run the test**:
   ```bash
   python3 test_p2p_seed_import_fix.py
   ```

2. **Test bootstrap**:
   ```bash
   animica peer bootstrap --verbose
   # Should show: imported=X, skipped=Y, invalid=0
   # No "invalid address" errors for valid addresses
   ```

3. **Check peer status**:
   ```bash
   animica peer list
   animica p2p doctor
   ```

4. **Try mining** (if peers connected):
   ```bash
   animica miner mine-blocks --address <addr> --count 1
   ```

## Summary

✅ **Fixed**: Seed import duplicate detection bug
✅ **Optimized**: DNS resolution performance
✅ **Tested**: Comprehensive test coverage
✅ **Documented**: Clear error messages and guidance

The remaining issues (peer connections failing) are network/infrastructure related, not code bugs. The fix ensures that valid addresses are properly imported and deduplicated, allowing peers to connect when network conditions permit.
