# Sync Status Accuracy Fix - Summary

## Problem Statement

Nodes were reporting `SYNCHRONIZED` even when significantly behind the network. 

**Example:**
- Node A: height 927, status SYNCHRONIZED, peers 14
- Node B: height 1666, status SYNCHRONIZED, peers 10

Node A is 739 blocks behind but incorrectly claims to be synchronized. This is a critical bug that prevents operators from knowing if their node is catching up.

## Root Causes

### 1. Stale Peer Tip Timestamps
**Location:** `p2p/node/p2p_service.py::_update_peer_head()`

**Problem:** `hello_received_at` timestamp was only updated when peer height *increased*. If a peer repeatedly announced the same height (e.g., when syncing stalled), the timestamp would stay at handshake time, eventually becoming stale (>60s old).

**Example:**
```python
# BEFORE - buggy code
if height <= current:
    # Returns early without updating timestamp!
    return
# Only updated if height increased
peer.hello_received_at = time.time()
```

**Fix:** Always update timestamp on ANY peer head update:
```python
# AFTER - fixed code
# CRITICAL FIX: Always update hello_received_at
peer.hello_received_at = time.time()

# Rest of logic continues...
```

### 2. Missing Fresh Peer Tip Tracking
**Location:** `p2p/node/p2p_service.py`

**Problem:** No method existed to compute best remote height with strict freshness checking. The existing `_network_best_height()` method had staleness checks but wasn't clear about when peer tips should be considered fresh.

**Fix:** Added `_compute_best_remote_info()` method:
```python
def _compute_best_remote_info(self, *, chain_id: Optional[int] = None) -> tuple[...]:
    """
    Compute best remote head info with strict freshness checking.
    
    Only considers peers with:
    - Same chain_id (if specified)
    - Fresh tip info (updated within 60s)
    - Valid numeric height > 0
    
    Returns (None, None, None, None) if no fresh peer tips available.
    """
    TIP_FRESHNESS_SEC = 60.0
    # ... implementation
```

### 3. Flawed Synchronized Logic
**Location:** `p2p/node/p2p_service.py::_build_sync_status_snapshot()`

**Problem:** The synchronized determination allowed `True` even when:
- No fresh peer tip information available (`best_remote_height == None`)
- Using fallback logic that didn't check tip freshness
- No concept of acceptable lag (ALLOWED_LAG)

**Example of buggy logic:**
```python
# BEFORE - could be synchronized without knowing network state
if target_height is not None:
    synchronized = (best_block_height > 0 and ...)
elif anchored_tip:  # Could be stale!
    synchronized = best_block_height > 0
```

**Fix:** New strict logic with ALLOWED_LAG:
```python
# AFTER - requires fresh peer tips
ALLOWED_LAG = 2  # Blocks

# Rule 1: NEVER synchronized if no fresh best_remote info
if best_remote_height is None:
    synchronized = False
    sync_status_reason = "no_fresh_peer_tips"
# Rule 2: Check if within ALLOWED_LAG
elif behind_by is not None:
    if behind_by <= ALLOWED_LAG:
        synchronized = (best_block_height > 0 and ...)
    else:
        synchronized = False
        sync_status_reason = f"behind_by_{behind_by}_blocks"
```

## Implementation Changes

### File: `p2p/node/p2p_service.py`

#### 1. Updated `SyncStatusSnapshot` dataclass
Added new fields:
- `best_remote_height: Optional[int]` - Best advertised height from fresh peer tips
- `best_remote_hash: Optional[str]` - Hash of best remote tip
- `best_remote_peer: Optional[str]` - Peer ID/address of best remote
- `best_remote_age_sec: Optional[float]` - Age of tip info in seconds
- `behind_by: Optional[int]` - Blocks behind best remote (None if unknown)
- `sync_status_reason: Optional[str]` - Reason for sync status (e.g., "no_fresh_peer_tips")

#### 2. Added `_compute_best_remote_info()` method
- Scans all connected peers
- Filters by chain_id if specified
- Only uses peers with tip info updated within 60s
- Returns best height/hash/peer/age or (None, None, None, None)

#### 3. Fixed `_update_peer_head()` method
- Now ALWAYS updates `hello_received_at` timestamp
- Ensures peer tip info stays fresh during sync

#### 4. Updated `_build_sync_status_snapshot()` method
- Calls `_compute_best_remote_info()` to get fresh peer tips
- Computes `behind_by = best_remote_height - local_height`
- Uses new strict synchronized logic with ALLOWED_LAG=2
- Populates all new fields in snapshot

### File: `python/animica/cli/sync.py`

#### 1. Extract new fields from sync_status
```python
best_remote_height = sync_status.get("best_remote_height")
best_remote_hash = sync_status.get("best_remote_hash")
best_remote_peer = sync_status.get("best_remote_peer")
best_remote_age_sec = sync_status.get("best_remote_age_sec")
behind_by = sync_status.get("behind_by")
sync_status_reason = sync_status.get("sync_status_reason")
```

#### 2. Display "Best Remote Head" section
Shows:
- Height and hash of best remote peer
- Peer address/ID
- Tip age (e.g., "2.5s ago")
- **Behind by count** (e.g., "Behind by: 739 blocks")
- Warning if no fresh peer tips available

#### 3. Add fields to JSON output
All new fields are included when `--json` flag is used.

## Example Output

### Before Fix
```
Sync Status:
  Status:    SYNCHRONIZED  ✓
  
Network:
  Peers:     14 connected
```
❌ **Misleading!** Node is 739 blocks behind but claims synchronized.

### After Fix
```
Best Remote Head (from fresh peer tips):
  Height:    1666
  Hash:      0xabcd...
  Peer:      peer.example.com:30333
  Tip Age:   2.5s ago
  Behind by: 739 blocks  ⚠

Sync Status:
  Status:    SYNCING  ⚠
  Progress:  927 / 1666
  Remaining: 739 blocks
```
✅ **Accurate!** Clearly shows node is behind and syncing.

### JSON Output
```json
{
  "height": 927,
  "best_remote_height": 1666,
  "best_remote_hash": "0xabcd...",
  "best_remote_peer": "peer.example.com:30333",
  "best_remote_age_sec": 2.5,
  "behind_by": 739,
  "sync_status_reason": "behind_by_739_blocks",
  "sync_state": "SYNCING",
  "synchronized": false
}
```

## Test Coverage

Created `p2p/tests/test_sync_status_accuracy.py` with 7 test cases:

1. ✅ **test_behind_node_reports_syncing** - Node 739 blocks behind reports SYNCING
2. ✅ **test_no_fresh_peer_tips_not_synchronized** - No fresh tips prevents SYNCHRONIZED
3. ✅ **test_stale_tips_ignored** - Stale tips (>60s) are ignored
4. ✅ **test_small_lag_within_allowed** - 1 block behind (within ALLOWED_LAG=2) reports SYNCHRONIZED
5. ✅ **test_at_tip_synchronized** - Node at same height as peer reports SYNCHRONIZED
6. ✅ **test_wrong_chain_id_ignored** - Peers on wrong chain_id are ignored
7. ✅ **test_genesis_node_with_fresh_peer** - Genesis node with peer ahead reports SYNCING

All tests pass.

## Acceptance Criteria

✅ **On node at height 927 with peer at 1666:**
- `animica sync status` shows SYNCING (not SYNCHRONIZED)
- Shows `behind_by: 739`
- Shows best peer tip with height 1666
- Shows reason: "behind_by_739_blocks"

✅ **On node at height 1666:**
- Can show SYNCHRONIZED (at tip)
- Shows `behind_by: 0` or small number within ALLOWED_LAG

✅ **No scenario with unknown best_remote may show SYNCHRONIZED:**
- If `best_remote_height == None`, status is SYNCING
- Shows reason: "no_fresh_peer_tips"

✅ **Works via RPC for GUI:**
- `sync.getStatus` RPC returns all new fields
- GUI can display accurate sync status

## Configuration

### TIP_FRESHNESS_SEC (hardcoded in `_compute_best_remote_info`)
- **Value:** 60 seconds
- **Purpose:** Only use peer tips updated within last 60s
- **Rationale:** Ensures we're using recent network state, not stale handshake data

### ALLOWED_LAG (hardcoded in `_build_sync_status_snapshot`)
- **Value:** 2 blocks
- **Purpose:** Allow small lag due to network propagation delay
- **Rationale:** Being 1-2 blocks behind is acceptable for SYNCHRONIZED status

Both values can be made configurable in future if needed.

## Backward Compatibility

✅ **Existing fields unchanged:**
- `network_best_height` still present (uses different logic)
- `synchronized` boolean still present
- All other existing fields unchanged

✅ **New fields are additive:**
- Old clients ignore new fields
- Old RPC consumers continue to work

✅ **CLI output enhanced:**
- New section added but doesn't break existing parsing
- JSON output includes all new fields but old fields still present

## Migration Notes

### For Node Operators
- Update to this version and run `animica sync status`
- You may see status change from SYNCHRONIZED to SYNCING if you were actually behind
- This is correct - your node was incorrectly reporting synchronized before

### For GUI/Frontend Developers
- Use new `best_remote_*` fields instead of `network_best_height` for accuracy
- Display `behind_by` count to users
- Show `sync_status_reason` when not synchronized
- Check `best_remote_height != null` before trusting synchronized status

### For Monitoring/Alerting
- Alert on `synchronized == false` AND `behind_by > threshold`
- Alert on `sync_status_reason == "no_fresh_peer_tips"` (peer connectivity issue)
- Use `best_remote_age_sec` to detect peer tip staleness

## Related Issues

This fix addresses the core issue reported:
- Node A at height 927 reporting SYNCHRONIZED
- Node B at height 1666 reporting SYNCHRONIZED
- Both nodes have different views of "synchronized"

Now both nodes will report accurate status based on fresh peer tip information.

## Future Improvements

1. **Make TIP_FRESHNESS_SEC configurable** - Allow operators to tune staleness threshold
2. **Make ALLOWED_LAG configurable** - Allow different lag tolerance per deployment
3. **Add peer tip update telemetry** - Track how often peer tips are updated
4. **Expose stale peer count** - Show how many peers have stale tips in diagnostics
5. **Add peer tip history** - Track peer tip height over time for better debugging

## References

- Problem statement in GitHub issue
- P2P spec: peer handshake and tip advertisement
- Sync spec: synchronized determination logic
