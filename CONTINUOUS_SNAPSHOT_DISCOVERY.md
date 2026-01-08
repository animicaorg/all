# Continuous Snapshot Discovery Implementation

## Overview

This document describes the continuous snapshot discovery feature that enables nodes to automatically and persistently discover and download snapshots from peers, eliminating the need for manual intervention and ensuring fast sync even when snapshots become available after node startup.

## Problem Statement

Previously, snapshot discovery was a **one-time operation** at node startup:
- Ran once after P2P service started
- Waited maximum 30 seconds for peers to connect
- If no snapshots were found, never retried
- Required manual intervention if peers with snapshots connected later
- Nodes fell back to slow block-by-block sync if initial discovery failed

This meant that:
1. **Timing-dependent**: If peers weren't connected within 30s, snapshots were never discovered
2. **Non-resilient**: Network issues during startup meant missing out on fast sync
3. **Manual workaround needed**: Users had to manually run `animica snapshot discover`
4. **Poor UX**: New nodes synced slowly even when snapshots were available

## Solution: Continuous Discovery with Retry

The new implementation adds a **continuous retry mechanism** that:
- Periodically attempts snapshot discovery
- Continues until a snapshot is successfully imported
- Automatically discovers snapshots that become available after startup
- Respects configurable retry limits and intervals
- Gracefully falls back to block-by-block sync when appropriate

### Key Features

✅ **Automatic Retry** - No manual intervention needed  
✅ **Configurable** - Tune retry interval and maximum attempts  
✅ **Resilient** - Handles network issues and peer timing  
✅ **Non-blocking** - Runs in background without delaying startup  
✅ **Smart Stopping** - Stops when snapshot imported or node synced  
✅ **Well-logged** - Clear visibility into retry attempts

## Architecture

### High-Level Flow

```
┌─────────────────────────────────────┐
│  Node Startup                       │
│  (RPC + P2P services start)         │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Background Discovery Task Starts   │
│  (_background_snapshot_discovery)   │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Wait for Initial Peers             │
│  (max 30s, check every 5s)          │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Continuous Discovery Loop          │
│  (continuous_snapshot_discovery)    │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Loop: Try Snapshot Bootstrap       │
│  ├─ Check node height               │
│  ├─ Query peers for snapshots       │
│  ├─ Select highest snapshot         │
│  └─ Download and import             │
└──────────────┬──────────────────────┘
               │
               ├─► Success ────────────────┐
               │                            │
               ├─► No snapshots found ──┐  │
               │                         │  │
               └─► Error occurred ──────┤  │
                                         │  │
                    ┌────────────────────┘  │
                    ▼                       │
         ┌─────────────────────┐           │
         │  Wait retry_interval │           │
         │  (default: 60s)      │           │
         └─────────┬─────────────           │
                   │                        │
                   │  ┌──────────────────┐  │
                   └─►│ Retry count < max?│ │
                      └─────┬──────────────  │
                            │                │
                         No │ Yes            │
                            │ │              │
                            │ └──────────────┘
                            ▼
                  ┌──────────────────┐
                  │  Stop Discovery   │
                  │  (fallback to     │
                  │   block sync)     │
                  └──────────────────┘
```

### Components

#### 1. `continuous_snapshot_discovery()` (p2p/sync/snapshot_sync.py)

Main retry loop that:
- Continuously attempts `try_snapshot_bootstrap()`
- Checks if node still needs a snapshot before each attempt
- Respects retry interval between attempts
- Stops when max retries reached, snapshot imported, or node synced
- Handles exceptions gracefully

#### 2. `_background_snapshot_discovery()` (rpc/deps.py)

Background task that:
- Waits for initial peer connections
- Launches continuous discovery loop
- Runs as non-blocking async task

#### 3. `try_snapshot_bootstrap()` (p2p/sync/snapshot_sync.py)

Single discovery attempt that:
- Queries all connected peers for snapshots
- Also checks static RPC URL if configured
- Selects highest available snapshot
- Downloads and imports if found

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_SNAPSHOT_SYNC_ENABLED` | `true` | Enable/disable snapshot sync feature |
| `ANIMICA_SNAPSHOT_AUTO_DISCOVER` | `true` | Enable automatic peer discovery |
| `ANIMICA_SNAPSHOT_RETRY_INTERVAL` | `60` | Seconds between retry attempts |
| `ANIMICA_SNAPSHOT_MAX_RETRIES` | `0` | Maximum retry attempts (0 = unlimited) |
| `ANIMICA_SNAPSHOT_MIN_HEIGHT` | `1000` | Height threshold for using snapshots |
| `ANIMICA_SNAPSHOT_TIMEOUT` | `600` | Timeout for snapshot operations (seconds) |
| `ANIMICA_SNAPSHOT_RPC_URL` | _(none)_ | Optional static snapshot source |

### Usage Examples

**Default (Continuous retry, unlimited):**
```bash
# Just start the node - it will keep trying until snapshot found
animica node up
```

**Fast retry for testing:**
```bash
export ANIMICA_SNAPSHOT_RETRY_INTERVAL=10  # Retry every 10 seconds
animica node up
```

**Limited retries:**
```bash
export ANIMICA_SNAPSHOT_MAX_RETRIES=10  # Try max 10 times
export ANIMICA_SNAPSHOT_RETRY_INTERVAL=30  # Wait 30s between tries
animica node up
```

**Disable continuous discovery:**
```bash
export ANIMICA_SNAPSHOT_AUTO_DISCOVER=false
animica node up

# Then manually discover when needed
animica snapshot discover
```

## Behavior Examples

### Scenario 1: Fast Success

```
[2026-01-08 03:00:00] INFO  P2P service started successfully
[2026-01-08 03:00:00] INFO  Starting automatic snapshot discovery from peers...
[2026-01-08 03:00:05] INFO  Found 3 connected peer(s), starting continuous snapshot discovery...
[2026-01-08 03:00:05] INFO  Starting continuous snapshot discovery (interval=60s, max_retries=unlimited)
[2026-01-08 03:00:05] DEBUG Snapshot discovery attempt 1 (current height: 0)
[2026-01-08 03:00:06] INFO  Found best snapshot at height 5000 from http://peer1:8545/rpc
[2026-01-08 03:00:10] INFO  Successfully bootstrapped from snapshot at height 5000
[2026-01-08 03:00:10] INFO  Successfully bootstrapped from snapshot after 1 attempt(s)
```

### Scenario 2: Retry Until Success

```
[2026-01-08 03:00:00] INFO  P2P service started successfully
[2026-01-08 03:00:00] INFO  Starting automatic snapshot discovery from peers...
[2026-01-08 03:00:30] INFO  No peers connected initially, will retry periodically...
[2026-01-08 03:00:30] INFO  Starting continuous snapshot discovery (interval=60s, max_retries=unlimited)
[2026-01-08 03:00:30] DEBUG Snapshot discovery attempt 1 (current height: 0)
[2026-01-08 03:00:30] DEBUG No connected peers available for snapshot query
[2026-01-08 03:00:30] DEBUG No snapshots found on attempt 1
[2026-01-08 03:00:30] DEBUG Waiting 60s before next snapshot discovery attempt
[2026-01-08 03:01:30] DEBUG Snapshot discovery attempt 2 (current height: 0)
[2026-01-08 03:01:30] INFO  Querying 2 peer(s) for available snapshots
[2026-01-08 03:01:30] DEBUG No snapshots found on attempt 2
[2026-01-08 03:01:30] DEBUG Waiting 60s before next snapshot discovery attempt
[2026-01-08 03:02:30] DEBUG Snapshot discovery attempt 3 (current height: 0)
[2026-01-08 03:02:31] INFO  Found best snapshot at height 8000 from http://peer3:8545/rpc
[2026-01-08 03:02:45] INFO  Successfully bootstrapped from snapshot at height 8000
[2026-01-08 03:02:45] INFO  Successfully bootstrapped from snapshot after 3 attempt(s)
```

### Scenario 3: Max Retries Reached

```
[2026-01-08 03:00:00] INFO  Starting continuous snapshot discovery (interval=60s, max_retries=5)
[2026-01-08 03:00:00] DEBUG Snapshot discovery attempt 1 (current height: 0)
[2026-01-08 03:00:00] DEBUG No snapshots found on attempt 1
[2026-01-08 03:01:00] DEBUG Snapshot discovery attempt 2 (current height: 0)
[2026-01-08 03:01:00] DEBUG No snapshots found on attempt 2
...
[2026-01-08 03:04:00] DEBUG Snapshot discovery attempt 5 (current height: 0)
[2026-01-08 03:04:00] DEBUG No snapshots found on attempt 5
[2026-01-08 03:04:00] INFO  Reached maximum retry attempts (5), falling back to block-by-block sync
[2026-01-08 03:04:00] DEBUG Continuous snapshot discovery ended
```

### Scenario 4: Node Syncs While Retrying

```
[2026-01-08 03:00:00] INFO  Starting continuous snapshot discovery (interval=60s, max_retries=unlimited)
[2026-01-08 03:00:00] DEBUG Snapshot discovery attempt 1 (current height: 100)
[2026-01-08 03:00:00] DEBUG No snapshots found on attempt 1
[2026-01-08 03:01:00] DEBUG Snapshot discovery attempt 2 (current height: 800)
[2026-01-08 03:01:00] DEBUG No snapshots found on attempt 2
[2026-01-08 03:02:00] DEBUG Snapshot discovery attempt 3 (current height: 1500)
[2026-01-08 03:02:00] INFO  Node at height 1500, no longer need snapshot bootstrap
[2026-01-08 03:02:00] DEBUG Continuous snapshot discovery ended
```

## Testing

### Unit Tests

Located in `tests/integration/test_snapshot_continuous_discovery.py`:

- `test_continuous_snapshot_discovery_retries` - Verifies retry until success
- `test_continuous_discovery_stops_on_max_retries` - Validates max retry limit
- `test_continuous_discovery_stops_when_synced` - Tests stop on sync threshold
- `test_continuous_discovery_respects_stop_event` - Validates graceful stop
- `test_continuous_discovery_handles_exceptions` - Error handling
- `test_snapshot_retry_environment_variables` - Config validation

### Manual Testing

1. **Test fast discovery:**
   ```bash
   # Terminal 1: Start node with snapshots
   animica node up --data-dir=/tmp/node-a
   animica snapshot create
   
   # Terminal 2: Start syncing node
   export ANIMICA_SNAPSHOT_RETRY_INTERVAL=10
   animica node up --data-dir=/tmp/node-b
   
   # Watch logs for discovery attempts
   tail -f /tmp/node-b/logs/*.log | grep snapshot
   ```

2. **Test delayed peer connection:**
   ```bash
   # Start node before peers are available
   animica node up --data-dir=/tmp/node-c
   
   # Wait, then connect peers
   sleep 120
   animica peer add <peer-address>
   
   # Verify snapshot is discovered on next retry
   ```

3. **Test max retries:**
   ```bash
   export ANIMICA_SNAPSHOT_MAX_RETRIES=5
   export ANIMICA_SNAPSHOT_RETRY_INTERVAL=5
   animica node up --data-dir=/tmp/node-d
   
   # Should stop after 5 attempts if no snapshots found
   ```

## Performance Considerations

### Resource Usage

- **CPU**: Minimal - only active during discovery attempts
- **Memory**: Negligible - no large buffers or caches
- **Network**: One RPC query per peer per attempt (batched in parallel)
- **Disk**: None during discovery (only during import)

### Retry Intervals

Choose retry interval based on your needs:

- **Fast (10-30s)**: Test environments, impatient users
  - Pros: Quick discovery
  - Cons: More network activity
  
- **Moderate (60-120s)**: Production default
  - Pros: Good balance
  - Cons: May take minutes to discover
  
- **Slow (300-600s)**: Stable networks with rare snapshot updates
  - Pros: Minimal overhead
  - Cons: Slow to discover new snapshots

### Max Retries

- **Unlimited (0)**: Default for production
  - Keeps trying until snapshot found or node synced
  - Best for nodes that expect to use snapshots
  
- **Limited (5-20)**: Conservative fallback
  - Gives up after N attempts
  - Falls back to block sync quickly
  - Best for uncertain environments

## Troubleshooting

### Continuous discovery not running

**Symptoms:**
- No "Starting continuous snapshot discovery" log message
- Node syncs block-by-block from genesis

**Possible causes:**
1. Auto-discovery disabled
2. Snapshot sync disabled
3. Node already above height threshold

**Solutions:**
```bash
# Check configuration
env | grep ANIMICA_SNAPSHOT

# Enable auto-discovery
export ANIMICA_SNAPSHOT_AUTO_DISCOVER=true
export ANIMICA_SNAPSHOT_SYNC_ENABLED=true

# Check node height
animica sync status

# Restart node
animica node restart
```

### Retries but never finds snapshots

**Symptoms:**
- Continuous "No snapshots found on attempt N" messages
- Never progresses to download

**Possible causes:**
1. No peers have snapshots
2. Peers don't expose RPC endpoints
3. RPC queries failing

**Solutions:**
```bash
# Manually check peers
animica peer list

# Query peers directly for snapshots
animica snapshot list --from-peers

# Check peer RPC connectivity
curl http://<peer-ip>:8545/rpc -d '{"jsonrpc":"2.0","id":1,"method":"snapshot.list","params":{}}'

# Configure static snapshot source
export ANIMICA_SNAPSHOT_RPC_URL=http://snapshots.example.com:8545/rpc
```

### Too many retry attempts

**Symptoms:**
- Excessive log messages
- Slow to fallback to block sync

**Solutions:**
```bash
# Increase retry interval
export ANIMICA_SNAPSHOT_RETRY_INTERVAL=300  # 5 minutes

# Set max retries
export ANIMICA_SNAPSHOT_MAX_RETRIES=10

# Or disable auto-discovery
export ANIMICA_SNAPSHOT_AUTO_DISCOVER=false
```

## Migration Guide

### From Previous Version

The new continuous discovery is **fully backward compatible**:

1. **No configuration changes needed** - Works with existing settings
2. **Manual commands still work** - `animica snapshot discover` still available
3. **Static URLs still supported** - `ANIMICA_SNAPSHOT_RPC_URL` still works
4. **Can be disabled** - Set `ANIMICA_SNAPSHOT_AUTO_DISCOVER=false`

### Upgrading

Simply update to the new version:
```bash
git pull
# No configuration changes needed
animica node restart
```

New environment variables are optional - defaults work well.

## Future Enhancements

Potential improvements for future versions:

1. **Adaptive retry interval** - Slow down if consistently failing
2. **Peer quality scoring** - Prefer fast/reliable peers
3. **Partial snapshot updates** - Download delta from partial snapshot
4. **Progress reporting** - Show discovery progress in status
5. **DHT integration** - Advertise/discover via distributed hash table
6. **Snapshot metadata cache** - Cache peer snapshot info between attempts

## Related Documentation

- [CHAIN_SNAPSHOT_SYNC.md](CHAIN_SNAPSHOT_SYNC.md) - Overall snapshot system
- [AUTOMATIC_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md](AUTOMATIC_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md) - Initial implementation
- [P2P_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md](P2P_SNAPSHOT_DISCOVERY_IMPLEMENTATION.md) - Peer discovery details

---

**Implementation Date:** January 8, 2026  
**Status:** Complete and Tested  
**Breaking Changes:** None - Fully backward compatible
