# Sync Missing Parent Deadlock Fix - Quick Reference

## Problem
Nodes getting stuck in BLOCKS phase with:
- `in_flight_blocks > 0`
- `queued_blocks = 0`
- `pending_headers = 0`
- `last_block_error = "missing parent"`
- `error_peer = "sync-cache"`

## Root Causes
1. **No parent backfill** - orphans wait indefinitely for parents
2. **Sync-cache loops** - serves same orphan repeatedly
3. **Missing parent deadlock** - in-flight blocks can't import, queue empty
4. **Fork not detected** - same height, different hash not handled
5. **No parent verification** - blocks requested without checking parent availability

## Fixes Applied

### 1. Orphan Parent Backfill ✅
**Where:** `p2p/node/p2p_service.py` - `_handle_missing_parent()`

**What:** Automatically requests missing parent blocks with rate limiting

**Config:**
```bash
export ANIMICA_P2P_ORPHAN_PARENT_BACKFILL=true  # default
export ANIMICA_P2P_ORPHAN_PARENT_REQUEST_INTERVAL=5.0  # seconds
```

**Logs:**
```
INFO: Buffered orphan; requesting missing parent block
  orphan_hash: 0xabc...
  parent_hash: 0xdef...
  parent_height: 5457
  rate_limited: false
```

### 2. Sync-Cache Orphan Loop Fix ✅
**Where:** `p2p/node/p2p_service.py` - `_try_import_cached_block()`

**What:** Detects orphans from cache, invalidates them, fetches parent from peers

**Logs:**
```
WARNING: Cached block is orphan; invalidating from cache
INFO: Scheduled parent block fetch from peers (not cache)
```

### 3. Watchdog Deadlock Detection ✅
**Where:** `p2p/node/p2p_service.py` - `_sync_watchdog_check()`

**What:** Detects missing parent deadlock, clears in-flight, re-queues blocks

**Config:**
```bash
export ANIMICA_SYNC_WATCHDOG_TIMEOUT_S=60  # default
```

**Logs:**
```
ERROR: Missing parent deadlock detected
  inflight_blocks: 61
  inflight_block_hashes: [...]
WARNING: Cleared inflight blocks and reset orphan tracking
```

### 4. Fork Detection ✅
**Where:** `p2p/node/p2p_service.py` - `_sync_loop()`

**What:** Compares hashes when heights match, triggers header sync for common ancestor

**Logs:**
```
WARNING: Fork detected: local and peer heights match but hashes differ
  height: 5458
  local_hash: 0xabc...
  peer_hash: 0xdef...
```

### 5. Parent Availability Checks ✅
**Where:** `p2p/node/p2p_service.py` - `_enqueue_missing_blocks()`

**What:** Verifies parent exists before enqueueing block, auto-enqueues parent if needed

**Logs:**
```
DEBUG: Auto-enqueued parent block
  parent_hash: 0xdef...
  parent_height: 5457
  child_hash: 0xabc...
  child_height: 5458
```

## Debugging Commands

### Check sync status
```bash
animica sync status
```
Look for:
- `last_block_error: missing parent`
- `inflight_blocks > 0` and `queued_blocks = 0`

### Force sync (clears errors)
```bash
animica sync force
```

### Check logs
```bash
# Orphan parent backfill
grep "Buffered orphan; requesting missing parent" /path/to/logs

# Missing parent deadlock
grep "Missing parent deadlock detected" /path/to/logs

# Fork detection
grep "Fork detected: local and peer heights match" /path/to/logs

# Cache orphan loop
grep "Cached block is orphan; invalidating" /path/to/logs
```

## Testing

Run test suite:
```bash
python test_sync_missing_parent_fix.py
```

Expected output:
```
✓ Test 1 PASSED: Orphan parent backfill rate limiting works
✓ Test 2 PASSED: Orphan parent tracking cleanup works
✓ Test 3 PASSED: Sync cache orphan invalidation works
✓ Test 4 PASSED: Missing parent deadlock detection and recovery works
✓ Test 5 PASSED: Fork detection at same height works
✓ Test 6 PASSED: Parent availability check works
✓ Test 7 PASSED: Orphan buffer TTL expiration works
Results: 7 passed, 0 failed
```

## Recovery Flow

When node gets stuck on missing parent:

1. **Watchdog detects** (after 60s of no progress):
   - Logs: "Missing parent deadlock detected"
   - Clears in-flight blocks
   - Re-queues blocks
   - Resets orphan tracking

2. **Blocks re-queued**:
   - Parent availability checked
   - Parents fetched first if needed
   - Blocks processed in order

3. **Parent arrives**:
   - Block import orphan pool retries children
   - Chain progresses normally

## Performance

- **Overhead:** Minimal (O(1) lookups, small tracking dicts)
- **Benefit:** Prevents deadlocks, faster sync, fewer retries
- **Self-healing:** Automatic recovery from stuck states

## Verification

After deployment:

1. **No stuck syncs** - nodes should complete sync without deadlocks
2. **No repeated errors** - same block shouldn't fail repeatedly with "missing parent"
3. **Fork handling** - forks at same height should be detected and resolved
4. **Steady progress** - `blocks_applied` metric increases consistently

## Files Changed

- `p2p/node/p2p_service.py` - Main sync orchestrator (5 methods enhanced)
- `test_sync_missing_parent_fix.py` - Test suite (7 tests)
- `docs/SYNC_MISSING_PARENT_FIX.md` - Full documentation

## Configuration Summary

```bash
# Orphan handling
export ANIMICA_P2P_ORPHAN_PARENT_BACKFILL=true
export ANIMICA_P2P_ORPHAN_PARENT_REQUEST_INTERVAL=5.0
export ANIMICA_P2P_ORPHAN_TTL_S=60
export ANIMICA_P2P_MAX_ORPHANS=128

# Watchdog
export ANIMICA_SYNC_WATCHDOG_TIMEOUT_S=60

# Stall detection
export ANIMICA_SYNC_STALL_TIMEOUT_S=20.0
```

All features enabled by default, no migration needed.
