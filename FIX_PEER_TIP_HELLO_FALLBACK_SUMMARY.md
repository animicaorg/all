# Fix: Node Sync Issue - No Fresh Peer Tips

## Problem Statement

Nodes were getting stuck at genesis (height 0) with the following symptoms:

```
Chain ID: 0
Head height: 0
Sync status: SYNCING
sync_status_reason: 'no_fresh_peer_tips'
peer_tips_total: 0
peer_tips_fresh: 0
peer_tips_stale: 0
peers_total: 1
```

Despite having connected peers that successfully completed the bootstrap handshake, nodes were unable to sync because the peer tip tracking system wasn't working correctly.

## Root Cause

The issue had multiple contributing factors:

### 1. Missing Tip Tracker Entries

The `_peer_tip_tracker` dictionary stores peer head information with timestamps for freshness checking. However, there were scenarios where:

- Peers completed handshake but their tip info wasn't immediately added to the tracker
- Tracker entries could be missing after node restarts or state resets
- Initial connection might not trigger tracker updates

When `_peer_tip_freshness_snapshot()` and `_compute_best_remote_info()` checked for peer tips, they immediately skipped peers without tracker entries, resulting in:
- `peer_tips_total: 0` (no peers counted)
- `best_remote_height: None` (no network height known)
- `sync_status_reason: 'no_fresh_peer_tips'` (unable to sync)

### 2. No Automatic Recovery

When the node detected `no_fresh_peer_tips`, it didn't automatically:
- Poll peers to get fresh tip information
- Use fallback data from peer hello messages
- Trigger recovery actions to get out of the stuck state

This meant nodes would remain stuck indefinitely unless manually restarted or forced to sync.

## The Fix

### 1. Hello Message Fallback in `_compute_best_remote_info()`

**Location:** `p2p/node/p2p_service.py`, lines 12736-12773

Added fallback logic to use peer's hello message when tip tracker has no entry:

```python
info = self._peer_tip_tracker.get(peer.remote)
if info is None:
    # FIX: Fallback to peer's hello head_height if tip tracker has no entry
    try:
        hello_height = (peer.hello or {}).get("head_height")
        hello_hash = (peer.hello or {}).get("head_hash")
        hello_age = now - peer.hello_received_at if peer.hello_received_at else float('inf')
        
        if hello_height is not None and hello_age <= PEER_TIP_FRESHNESS_SEC:
            peer_height = int(hello_height)
            if peer_height >= 0:
                if best_height is None or peer_height > best_height:
                    best_height = peer_height
                    # ... update best_hash, best_peer, best_age
    except (ValueError, TypeError):
        # Log and continue
        pass
    continue
```

**Impact:**
- Nodes can now use peer head heights from hello messages even without tracker entries
- Prevents `best_remote_height: None` which caused sync stalling
- Gracefully handles missing tracker data

### 2. Hello Message Fallback in `_peer_tip_freshness_snapshot()`

**Location:** `p2p/node/p2p_service.py`, lines 12808-12832

Added similar fallback logic for freshness checking:

```python
info = self._peer_tip_tracker.get(peer.remote)
if info is None:
    # FIX: Fallback to hello age if tip tracker has no entry
    try:
        hello_age = now - peer.hello_received_at if peer.hello_received_at else float('inf')
        if hello_age <= PEER_TIP_FRESHNESS_SEC:
            fresh += 1
        else:
            stale += 1
    except Exception:
        stale += 1
    continue
```

**Impact:**
- Peers are now counted even without tracker entries
- `peer_tips_total` reflects actual connected peer count
- `peer_tips_fresh` accurately tracks peers with recent hello messages

### 3. Automatic Peer Head Polling

**Location:** `p2p/node/p2p_service.py`, lines 11589-11613

Added logic to automatically poll peer heads when no network height is available:

```python
network_best_height = self._network_best_height()

# FIX: If we have no network best height but have connected peers,
# immediately poll peer heads to get fresh tip information.
if network_best_height is None and len(self._peers) > 0:
    time_since_last_poll = min(...)  # Check last poll time
    
    if time_since_last_poll > 5.0:
        log.info("No network best height available - polling peer heads")
        self._create_child_task(
            self._poll_peer_heads(reason="no_network_best_height", force=True),
            name="p2p.poll_peer_heads_recovery",
        )
```

**Impact:**
- Nodes automatically attempt to get fresh peer tips when stuck
- Prevents indefinite stalling in "no_fresh_peer_tips" state
- Non-blocking async task doesn't delay sync loop

### 4. Enhanced Diagnostic Logging

**Location:** Throughout `_peer_tip_freshness_snapshot()` and `_compute_best_remote_info()`

Added comprehensive logging to diagnose peer filtering:

```python
# Track why peers are filtered out
filtered_hello_not_done = 0
filtered_identity_not_ok = 0
filtered_repo_state_not_ok = 0
filtered_chain_mismatch = 0

# Log when no peers pass filters
if total == 0 and len(self._peers) > 0:
    log.warning(
        "No peers passed tip freshness filters",
        extra={
            "total_peers": len(self._peers),
            "filtered_hello_not_done": filtered_hello_not_done,
            "filtered_identity_not_ok": filtered_identity_not_ok,
            "filtered_repo_state_not_ok": filtered_repo_state_not_ok,
            "filtered_chain_mismatch": filtered_chain_mismatch,
            "chain_id_filter": chain_id,
        },
    )
```

**Impact:**
- Operators can now diagnose WHY peers aren't being counted
- Logs clearly show if issue is identity, chain mismatch, or other factors
- Helps identify configuration or genesis mismatch issues

## Testing

Created comprehensive test in `test_peer_tip_hello_fallback.py` that validates:

1. **Hello Fallback in Freshness Check**
   - Peer with no tracker entry is counted using hello age
   - Returns `(total=1, fresh=1, stale=0)`

2. **Hello Fallback in Best Remote Info**
   - Peer head height from hello is used when tracker has no entry
   - Returns correct `best_height` and `best_peer`

3. **Sync Status Integration**
   - Sync status doesn't show "no_fresh_peer_tips" with valid peer
   - `best_remote_height` is correctly populated
   - `peer_tips_total` and `peer_tips_fresh` are accurate

## Before vs After

### Before Fix

```
Chain ID: 0
Head height: 0
Sync status: SYNCING
sync_status_reason: 'no_fresh_peer_tips'

peer_tips_total: 0          # Peers not counted without tracker entry
peer_tips_fresh: 0          # No fresh peers detected
peer_tips_stale: 0

best_remote_height: None    # No network height known
network_best_height: None

Peers: total=1              # Peer IS connected
Bootstrap: success=True     # Peer IS valid

Result: Node stuck, cannot sync
```

### After Fix

```
Chain ID: 0
Head height: 0
Sync status: SYNCING
sync_status_reason: 'behind'  # Clear reason

peer_tips_total: 1          # Peer counted using hello fallback
peer_tips_fresh: 1          # Hello age used for freshness
peer_tips_stale: 0

best_remote_height: 100     # Height from peer hello
network_best_height: 100    # Network height known

Peers: total=1              # Peer IS connected
Bootstrap: success=True     # Peer IS valid

Result: Node syncs successfully
```

## Scenarios Fixed

### Scenario 1: Fresh Node Bootstrap

**Before:**
- Node starts at genesis
- Connects to peer
- Peer hello received but tracker not updated
- Stuck at genesis with "no_fresh_peer_tips"

**After:**
- Node starts at genesis
- Connects to peer
- Uses hello head_height as fallback
- Begins syncing immediately

### Scenario 2: Post-Restart Recovery

**Before:**
- Node restarts after crash
- Peers reconnect
- Tip tracker empty initially
- Cannot determine network height
- Stuck until tracker populated

**After:**
- Node restarts after crash
- Peers reconnect
- Uses hello messages as fallback
- Syncs immediately without waiting for tracker updates

### Scenario 3: Genesis Watchdog Recovery

**Before:**
- Watchdog clears sync state during recovery
- Tip tracker also cleared
- No peer info available after reset
- Recovery fails, still stuck

**After:**
- Watchdog clears sync state
- Hello fallback provides peer info
- Recovery succeeds with fresh data

## Technical Details

### Freshness Window

Uses `PEER_TIP_FRESHNESS_SEC = 600.0` (10 minutes) for:
- Tip tracker entry age checking
- Hello message age checking
- Best remote info validation

This provides tolerance for:
- Up to 60 missed HEAD_STATUS broadcasts (at 10s interval)
- Network latency and temporary disconnections
- Brief peer communication issues

### Safety Measures

1. **Age Validation:**
   - Both tracker age and hello age must be ≤ 600s
   - Stale data is never used for sync decisions

2. **Error Handling:**
   - All type conversions wrapped in try/except
   - Invalid data gracefully skipped
   - Detailed error logging

3. **Non-Blocking:**
   - Auto-poll runs as async task
   - Doesn't block sync loop
   - Rate-limited to once per 5 seconds

## Deployment Notes

### Changes

- **File:** `p2p/node/p2p_service.py`
- **Lines Modified:** ~180 lines (mostly additions)
- **Breaking Changes:** None
- **Config Changes:** None required

### Compatibility

- ✅ Fully backward compatible
- ✅ No protocol changes
- ✅ No database migrations
- ✅ No configuration changes needed

### Rollout

1. Deploy updated code
2. Restart nodes
3. Nodes immediately use fallback logic
4. No manual intervention needed

### Monitoring

After deployment, monitor:

```bash
# Check sync status
animica node status

# Look for these improvements:
# - peer_tips_total > 0 (peers counted)
# - peer_tips_fresh > 0 (fresh peers detected)
# - best_remote_height has value (network height known)
# - sync_status_reason NOT "no_fresh_peer_tips"
```

Check logs for:
```
"No network best height available - polling peer heads"  # Auto-recovery triggered
"Using peer hello head_height as fallback"              # Fallback in use
"Peer counted as fresh using hello age"                 # Freshness fallback active
```

## Impact Summary

### Before Fix
- ❌ Nodes stuck at genesis with connected peers
- ❌ Manual restart required to recover
- ❌ Poor user experience
- ❌ Unreliable sync behavior
- ❌ No visibility into root cause

### After Fix
- ✅ Nodes sync reliably from genesis
- ✅ Automatic recovery from stuck states
- ✅ Excellent user experience
- ✅ Robust peer tip tracking
- ✅ Clear diagnostic logging

## Related Issues

This fix addresses similar issues documented in:
- `FIX_NODE_SYNC_HIGHEST_HEIGHT.md` - Network best height fallback
- `SYNC_STALL_FIX_SUMMARY.md` - General sync stall recovery
- `GENESIS_SYNC_FIX_SUMMARY.md` - Genesis-specific sync issues

## Status

✅ **Ready for Deployment**

All changes:
- Tested with dedicated test suite
- Backward compatible
- No breaking changes
- No configuration needed
- Immediate improvement after deployment
