# Core P2P Sync Improvements

This document describes the sync improvements made to the legacy core_p2p implementation.

## Problem Statement

The original core_p2p implementation had minimal sync recovery mechanisms:
- Only sent `getheaders` once during initial handshake
- No timeout handling for inflight block requests
- No continuous sync checking to detect stalls
- Could get stuck if initial sync didn't complete or peers were unresponsive

## Solution

Added comprehensive sync recovery mechanisms to ensure continuous sync progress:

### 1. Timeout Handling for Block Requests

**File**: `p2p/core_p2p/sync_manager.py`

Added `request_timeout` field (default 30 seconds) to track how long blocks have been inflight:

```python
request_timeout: float = 30.0  # Timeout for inflight block requests (seconds)
```

Added `timeout_stale_requests()` method that:
- Checks all inflight block requests
- Identifies requests that have exceeded timeout
- Removes them from inflight state
- Re-queues them for retry
- Returns count of timed-out requests

**Benefits**:
- Prevents sync from getting permanently stuck on unresponsive peers
- Automatically retries failed block downloads
- No manual intervention needed

### 2. Continuous Sync Checking

**File**: `p2p/core_p2p/service.py`

Added `_sync_check_loop()` background task that runs every 10 seconds (configurable via `ANIMICA_P2P_CORE_SYNC_CHECK_SEC`):

**Stall Detection**:
- Tracks chain height between checks
- Detects when height stops progressing AND no work is pending/inflight
- Increments stall counter when detected

**Recovery Actions** (after 20+ seconds of stall):
1. Requests headers from all connected peers
2. Resets stall counter
3. Logs stall event with diagnostic info

**Pending Block Requests**:
- Detects when blocks are queued but not requested
- Automatically sends `getdata` requests to random peer
- Handles case where initial requests were missed or lost

### 3. Enhanced Logging

Added structured logging throughout sync operations:
- Timeout events with count
- Stall detection with current state (height, pending, inflight, peer count)
- Debug logging for request failures

## Configuration

### Environment Variables

- `ANIMICA_P2P_CORE_SYNC_CHECK_SEC` (default: 10)
  - Interval in seconds between sync checks
  - Lower values = faster stall detection but more CPU
  - Higher values = slower detection but less overhead

- `ANIMICA_P2P_CORE_ENABLE` (default: True)
  - Whether to enable core_p2p service
  - Set to "0" or "false" to disable

### SyncManager Parameters

```python
SyncManager(
    chain: ChainAdapter,
    request_timeout: float = 30.0,  # Block request timeout
    max_inflight: int = 4096,       # Max parallel block downloads
)
```

## Testing

See `p2p/tests/test_core_p2p_sync_improvements.py` for comprehensive tests covering:
- Basic timeout functionality
- Partial timeout (some requests complete, some timeout)
- No duplicate re-queueing
- Configuration validation
- Integration scenarios

Run tests:
```bash
python3 p2p/tests/test_core_p2p_sync_improvements.py
```

## Metrics & Monitoring

The sync check loop logs important events:

**Timeout Events**:
```json
{
  "message": "core p2p timed out stale block requests",
  "count": 5
}
```

**Stall Detection**:
```json
{
  "message": "core p2p sync appears stalled, requesting headers",
  "height": 12345,
  "pending": 0,
  "inflight": 0,
  "peers": 3
}
```

**Pending Requests**:
```json
{
  "message": "core p2p requesting pending blocks",
  "pending": 100
}
```

## Performance Impact

- **CPU**: Minimal - one check every 10 seconds per node
- **Memory**: Negligible - only tracks timestamps for inflight requests
- **Network**: Only sends additional requests when stalled (rare under normal operation)

## Comparison with Modern P2P

| Feature | Core P2P (Legacy) | Modern P2P (NodeService) |
|---------|-------------------|--------------------------|
| Timeout handling | ✅ 30s default | ✅ 20s default |
| Stall detection | ✅ 20s threshold | ✅ 15-30s adaptive |
| Continuous sync | ✅ Every 10s | ✅ 1ms tick rate |
| Max parallelism | 4096 blocks | 4096 blocks |
| Protocol | Bitcoin-style inv/getdata | Gossip + inv/getdata |
| Complexity | Low (simple) | High (feature-rich) |

**When to use Core P2P**:
- Need simple, reliable sync
- Don't need advanced features (gossip topics, flow control)
- Prefer battle-tested Bitcoin-style protocol
- Lower complexity for debugging

**When to use Modern P2P**:
- Need advanced features (multiple gossip topics, QUIC/WebSocket)
- Want faster recovery (1ms vs 10s check interval)
- Need fine-grained flow control
- Building on modern P2P stack

## Future Improvements

Potential enhancements:
1. Adaptive timeout based on peer latency
2. Peer reputation scoring (faster timeout for slow peers)
3. Parallel header downloads from multiple peers
4. Priority queue for blocks (download tip blocks first)
5. Metrics export (Prometheus/StatsD)

## Troubleshooting

### Sync stuck at height X

Check logs for:
1. "core p2p sync appears stalled" - should auto-recover
2. "core p2p timed out stale block requests" - should auto-retry
3. Peer count in stall logs - need at least 1 connected peer

### High timeout rate

Possible causes:
- Network congestion
- Slow peers
- Firewall blocking responses

Solutions:
- Increase `request_timeout` via SyncManager
- Add faster peers as seeds
- Check firewall rules

### No sync progress

Check:
1. Are peers connected? (`peers` field in logs)
2. Is core_p2p enabled? (`ANIMICA_P2P_CORE_ENABLE`)
3. Are there pending blocks? (check logs)
4. Is chain progressing? (height should increase)

## References

- Bitcoin P2P Protocol: https://developer.bitcoin.org/devguide/p2p_network.html
- Bitcoin Inv/GetData: https://developer.bitcoin.org/reference/p2p_networking.html#inv
- Core P2P Implementation: `p2p/core_p2p/`
- Tests: `p2p/tests/test_core_p2p_*.py`
