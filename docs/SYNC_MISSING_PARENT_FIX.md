# Sync Missing Parent Deadlock Fix

## Problem Statement

Nodes were experiencing sync deadlocks with "missing parent" errors, especially when:

1. **Local head height equals best peer height but hashes differ** (fork at same height)
2. **In-flight blocks > 0 but queued_blocks = 0 and pending_headers = 0**
3. **Sync-cache serves orphan blocks repeatedly** causing tight loops
4. **Parent blocks not being fetched** leading to permanent orphan state

### Observed Symptoms

```
Phase: SYNCING_BLOCKS
Local head: 5458
Best peer head: 5458 (different hash)
In-flight blocks: 61
Queued blocks: 0
Pending headers: 0
Last block error: "missing parent"
Block error peer: "sync-cache"
```

This indicates:
- A fork exists at the same height (local and peer have different blocks at height 5458)
- Blocks are in-flight but their parents are missing
- The sync-cache is serving orphan blocks without parent availability checks
- No mechanism to backfill missing parents or recover from this state

## Root Causes

### 1. No Parent Backfill Mechanism
When a block import failed with "missing parent", the orphan was buffered but no explicit request was made to fetch the missing parent block. This left orphans waiting indefinitely.

### 2. Sync-Cache Orphan Loop
The sync-cache could serve the same orphan block repeatedly. Each attempt would fail with "missing parent", the block would remain in cache, and the next request would retry the same orphan - creating an infinite loop.

### 3. Missing Parent Deadlock
When all in-flight blocks have missing parents and the queue is empty, sync stalls completely:
- In-flight blocks can't be imported (missing parents)
- Queue is empty (no new blocks requested)
- Watchdog doesn't detect this specific condition
- System is deadlocked

### 4. Fork at Same Height Not Detected
When local and peer heights match but hashes differ, no fork detection occurred. The node would try to sync blocks from a different chain without realizing it needed to find a common ancestor first.

### 5. Block Request Pipeline Doesn't Verify Parent Availability
Blocks were requested in height order without checking if their parents were available, leading to out-of-order imports and orphans.

## Solution

### 1. Enhanced Orphan Parent Backfill

**File:** `p2p/node/p2p_service.py` - `_handle_missing_parent()`

**Changes:**
- Added `_orphan_parent_requests` tracking dict to record when parents were requested
- Implemented rate limiting (default 5s) to prevent duplicate parent requests
- Priority queueing: parent blocks are added to the front of the queue (`appendleft`)
- Automatic cleanup: tracking dict is pruned to max 1000 entries to prevent unbounded growth
- Enhanced logging with orphan and parent hash details

**Configuration:**
```bash
# Rate limit for parent backfill requests (seconds)
export ANIMICA_P2P_ORPHAN_PARENT_REQUEST_INTERVAL=5.0

# Enable/disable parent backfill (default: enabled)
export ANIMICA_P2P_ORPHAN_PARENT_BACKFILL=true
```

**How it works:**
1. Block import fails with "missing parent"
2. Check if parent was requested recently (rate limiting)
3. If allowed, add parent to front of block queue
4. Mark parent as requested in tracking dict
5. When parent arrives, orphan pool in `block_import.py` automatically retries children

### 2. Fixed Sync-Cache Orphan Loop

**File:** `p2p/node/p2p_service.py` - `_try_import_cached_block()`

**Changes:**
- Detects when cached block is an orphan (via `_is_orphan_reason()`)
- Immediately invalidates orphan from cache to prevent re-serving
- Extracts parent hash from the orphan block
- Schedules parent fetch from **real peers** (not cache)
- Logs orphan detection with block and parent hashes

**How it works:**
1. Cache returns a block
2. Attempt to import block
3. If import fails with "missing parent":
   - Invalidate block from cache
   - Decode block to extract parent_hash
   - Add parent_hash to block queue for fetch from peers
   - Return false (cache miss)
4. Next request for the same block will be a cache miss
5. Block will be fetched from peers after parent is available

### 3. Enhanced Sync Watchdog

**File:** `p2p/node/p2p_service.py` - `_sync_watchdog_check()`

**Changes:**
- Added specific detection for missing parent deadlock condition:
  ```python
  missing_parent_deadlock = (
      sync_inflight_blocks > 0
      and not sync_block_queue
      and sync_last_block_error == "missing parent"
  )
  ```
- Logs in-flight block hashes for debugging (first 5)
- Clears in-flight blocks and re-queues them
- Resets orphan parent tracking to allow fresh retry
- Triggers aggressive sync kick

**How it works:**
1. Watchdog detects no progress for `ANIMICA_SYNC_WATCHDOG_TIMEOUT_S` (default 60s)
2. Checks for missing parent deadlock condition
3. If detected:
   - Log all in-flight block hashes
   - Move in-flight blocks back to queue
   - Clear tracking to allow parent re-requests
   - Trigger sync with `aggressive=True`
4. This breaks the deadlock and allows retry with fresh state

### 4. Fork Detection at Same Height

**File:** `p2p/node/p2p_service.py` - `_sync_loop()`

**Changes:**
- Added comparison of local and peer head hashes when heights match
- Logs fork detection with both hashes
- Triggers header sync with walk-back to find common ancestor
- Uses `locator_mode="fork_resolution"` for visibility

**How it works:**
1. Compare local height and best peer height
2. If heights match, compare hashes
3. If hashes differ, log fork detection
4. Build locator starting from local head - 10 blocks
5. Request headers from peer to find common ancestor
6. Existing header sync logic handles fork choice
7. Better chain is adopted via normal reorg mechanism

### 5. Block Request Pipeline with Parent Verification

**File:** `p2p/node/p2p_service.py` - `_enqueue_missing_blocks()`

**Changes:**
- Sort blocks by height to ensure ancestor→descendant order
- For each block, check parent availability:
  ```python
  parent_available = (
      has_block(parent_hash)
      or parent_hash in block_queue
      or parent_hash in inflight_blocks
      or parent_hash in block_buffer
  )
  ```
- If parent not available but parent header exists, auto-enqueue parent first
- If parent header also missing, skip block (defer until parent chain known)
- Log parent auto-enqueue for debugging

**How it works:**
1. Headers arrive and are sorted by height
2. For each header, check if we need its block
3. Before enqueueing block, verify parent:
   - Parent in DB? OK, enqueue
   - Parent in queue/in-flight? OK, enqueue
   - Parent header known? Enqueue parent first, then child
   - Parent header unknown? Skip, will retry later
4. This ensures blocks are always requested with parent available

## Configuration

All new features have configuration environment variables:

```bash
# Orphan parent backfill
export ANIMICA_P2P_ORPHAN_PARENT_BACKFILL=true  # Enable/disable
export ANIMICA_P2P_ORPHAN_PARENT_REQUEST_INTERVAL=5.0  # Rate limit (seconds)

# Sync watchdog
export ANIMICA_SYNC_WATCHDOG_TIMEOUT_S=60  # Time before recovery (seconds)

# Orphan buffer
export ANIMICA_P2P_ORPHAN_TTL_S=60  # Orphan TTL (seconds)
export ANIMICA_P2P_MAX_ORPHANS=128  # Max orphans in buffer

# Sync stall detection
export ANIMICA_SYNC_STALL_TIMEOUT_S=20.0  # Stall timeout (seconds)
```

## Debugging

### Check for Missing Parent Deadlock

Look for these log entries:

```
# Fork detection
WARNING: Fork detected: local and peer heights match but hashes differ
  height: 5458
  local_hash: 0xabc123...
  peer_hash: 0xdef456...
  peer: 144.126.133.21:30333

# Missing parent deadlock
ERROR: Missing parent deadlock detected
  inflight_blocks: 61
  inflight_block_hashes: ['abc123', 'def456', ...]
  queued_blocks: 0
  last_block_error: missing parent

# Orphan parent backfill
INFO: Buffered orphan; requesting missing parent block
  orphan_hash: 0xabc123...
  parent_hash: 0xdef456...
  parent_height: 5457
  rate_limited: false

# Cache orphan loop
WARNING: Cached block is orphan; invalidating from cache
  hash: 0xabc123...
  reason: missing parent
INFO: Scheduled parent block fetch from peers (not cache)
  orphan_hash: 0xabc123...
  parent_hash: 0xdef456...
```

### Manual Recovery

If sync is stuck:

1. **Force sync** (clears "at_tip" errors):
   ```bash
   animica sync force
   ```

2. **Check sync status**:
   ```bash
   animica sync status
   ```
   Look for:
   - `last_block_error: missing parent`
   - `inflight_blocks > 0 and queued_blocks = 0`
   - `last_error_peer: sync-cache`

3. **Wait for watchdog** (60 seconds by default):
   - Watchdog will detect and auto-recover
   - Look for "Missing parent deadlock detected" log

4. **Restart node** (last resort):
   - Clears all sync state
   - Node will re-sync from checkpoint or genesis

## Testing

Test suite: `test_sync_missing_parent_fix.py`

Run with:
```bash
python test_sync_missing_parent_fix.py
```

Tests cover:
1. ✅ Orphan parent backfill rate limiting
2. ✅ Orphan parent tracking cleanup
3. ✅ Sync cache orphan invalidation
4. ✅ Missing parent deadlock detection
5. ✅ Fork detection at same height
6. ✅ Parent availability check
7. ✅ Orphan buffer TTL expiration

## Performance Impact

**Minimal overhead:**
- Orphan parent tracking: O(1) lookup, pruned to 1000 entries max
- Cache orphan detection: same code path as before, just adds invalidation
- Fork detection: single hash comparison per sync tick when heights match
- Parent verification: O(1) lookups in existing data structures
- Watchdog enhancement: adds one conditional check per tick

**Benefits:**
- **Prevents deadlocks**: Nodes no longer get stuck on missing parents
- **Faster sync**: Blocks are fetched in proper order with parents first
- **Fewer retries**: Cache orphan loops eliminated
- **Better fork handling**: Forks detected and resolved automatically
- **Self-healing**: Watchdog auto-recovers from stuck states

## Migration

**No migration needed.** Changes are backward compatible:
- New state variables are optional and default to safe values
- Existing sync state is preserved
- Configuration defaults maintain current behavior
- Can be disabled via environment variables if needed

## Verification

After deploying, verify the fix is working:

1. **Check logs for new entries**:
   ```bash
   grep "Buffered orphan; requesting missing parent block" /path/to/logs
   grep "Missing parent deadlock detected" /path/to/logs
   grep "Fork detected: local and peer heights match" /path/to/logs
   ```

2. **Monitor sync progress**:
   - Sync should complete without stuck states
   - No repeated "missing parent" errors for same block
   - In-flight blocks should drain properly

3. **Check metrics**:
   - `blocks_applied` should increase steadily
   - `blocks_rejected` should not spike
   - `stall_recoveries` counts watchdog interventions

## Related Issues

- Fixes sync deadlock at same height with different hashes
- Fixes sync-cache orphan loops
- Fixes missing parent with in-flight blocks
- Improves fork detection and resolution
- Enhances orphan block handling

## References

- Block import orphan pool: `core/chain/block_import.py` - `_remember_orphan()`, `_process_orphans()`
- Sync orchestrator: `p2p/node/p2p_service.py`
- Sync cache: `p2p/sync/cache_store.py`
- Test suite: `test_sync_missing_parent_fix.py`
