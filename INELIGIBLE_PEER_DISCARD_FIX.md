# Ineligible Peer Block Discard Fix

## Problem Statement

Nodes experience sync stalls when peers have `handshake_pending` status. The problem manifests as:

```
Height 5865 | headers 6139 | blocks 5865 | peers 4 | HEADERS
Height 5865 | headers 6139 | blocks 5865 | peers 6 | HEADERS
Height 5865 | headers 6139 | blocks 5865 | peers 4 | HEADERS
⚠ No progress for 15 seconds
Diagnostics:
  eligible_peers_for_headers: ['113.22.229.190:51428', '84.211.73.155:34112', '86.61.75.216:30333', '62.169.17.132:30333']
  ineligible_peers_for_headers: {'5.189.172.222:59462': 'handshake_pending'}
  last_block_error_peer: sync-cache
```

### Root Cause

1. **Blocks stuck from ineligible peers**: When a peer becomes ineligible (e.g., enters `handshake_pending` state), blocks that are already in the sync buffer or inflight from that peer remain there
2. **Waiting indefinitely**: The sync logic waits for these blocks to arrive, but they never will because the peer is ineligible
3. **No cleanup**: There was no mechanism to discard blocks from peers that become ineligible
4. **Force peer not prioritized**: The trusted verifier peer (144.126.133.21:30333) was not being prioritized during recovery

## Solution

### 1. Added `_discard_blocks_from_ineligible_peers()` Function

This new function:
- Checks all blocks in the buffer and inflight queues against current eligible peers
- Discards blocks from peers that are no longer eligible
- Re-queues those blocks to be fetched from eligible peers
- Clears the sync cache if the error peer is ineligible

**Location**: `p2p/node/p2p_service.py` (lines ~3555-3625)

```python
def _discard_blocks_from_ineligible_peers(self) -> dict[str, int]:
    """
    Discard blocks from ineligible peers in the sync buffer and inflight blocks.
    This prevents stalls when peers become ineligible (e.g., handshake_pending).
    Returns counts of discarded blocks by source.
    """
    # Get current eligible peers
    eligible_block_peers, ineligible_block_peers = self._eligible_block_peers()
    eligible_remotes = {peer.remote for peer in eligible_block_peers}
    
    # Discard blocks from ineligible peers in buffer
    # Discard blocks from ineligible peers in inflight
    # Clear cache if error peer is ineligible
    # Re-queue blocks for fetching from eligible peers
```

### 2. Enhanced `_handle_sync_stall()` to Use Cleanup

The stall handler now calls `_discard_blocks_from_ineligible_peers()` before attempting recovery:

**Location**: `p2p/node/p2p_service.py` (lines ~3630-3695)

```python
def _handle_sync_stall(self, *, reason: str) -> None:
    # ...
    
    # Discard blocks from ineligible peers before attempting recovery
    discarded = self._discard_blocks_from_ineligible_peers()
    
    # Continue with existing stall recovery logic
    # ...
    
    log.warning(
        "Block sync stall handled",
        extra={
            # ...
            "discarded_blocks": discarded,
        },
    )
```

### 3. Added Periodic Cleanup in Sync Loop

To prevent accumulation of stale blocks, the sync loop now periodically checks for ineligible peer blocks:

**Location**: `p2p/node/p2p_service.py` (lines ~9368-9373)

```python
# Periodically discard blocks from ineligible peers to prevent stalls
# This is especially important when peers have handshake_pending status
if now - self._sync_last_progress_at > self._sync_stall_timeout / 2:
    self._discard_blocks_from_ineligible_peers()
```

### 4. Prioritized Force Peers in Selection

Both `_select_sync_peer()` and `_select_block_peer()` now prioritize force peers:

**Location**: `p2p/node/p2p_service.py` (lines ~10064-10073, ~10146-10169)

```python
def _select_sync_peer(self, ...) -> Optional[_PeerState]:
    # ...
    
    # Prioritize force peers (those in FORCE_SYNC_HEADER_PEERS)
    force_peers = [p for p in eligible if p.remote in FORCE_SYNC_HEADER_PEERS and p.remote not in avoid_remotes]
    if force_peers:
        # Always prefer force peers - return the first available one
        # These are trusted verifier nodes (like 144.126.133.21:30333)
        return force_peers[0]
    
    # Continue with normal peer selection
    # ...
```

## Expected Behavior

### Before the Fix

```
1. Node syncing at height 5865
2. Peer enters handshake_pending state
3. Blocks from that peer stuck in buffer/inflight
4. Node waits for blocks indefinitely
5. Sync stalls permanently
6. Manual intervention required: `animica sync force`
```

### After the Fix

```
1. Node syncing at height 5865
2. Peer enters handshake_pending state
3. Periodic cleanup detects ineligible peer
4. Blocks from ineligible peer are discarded
5. Blocks are re-queued for fetching from eligible peers
6. Force peer (144.126.133.21:30333) is prioritized
7. Sync recovers automatically
8. No manual intervention needed
```

## Testing

### Unit Tests

Created comprehensive test suite in `p2p/tests/test_discard_ineligible_peers.py`:

- `test_discard_buffer_blocks_from_ineligible_peers`: Verifies blocks in buffer are discarded
- `test_discard_inflight_blocks_from_ineligible_peers`: Verifies inflight blocks are discarded
- `test_no_discard_when_all_peers_eligible`: Verifies no-op when all peers are eligible
- `test_clear_cache_when_error_peer_ineligible`: Verifies cache clearing
- `test_force_peers_prioritized_in_sync_peer_selection`: Verifies force peer prioritization
- `test_force_peer_bypasses_eligibility_checks`: Verifies force peers bypass checks

### Manual Verification

Use the provided verification script:

```bash
python3 verify_ineligible_peer_fix.py --rpc http://127.0.0.1:8545/rpc --force-sync --monitor 60
```

This script:
- Checks initial sync status
- Triggers a force sync
- Monitors sync recovery for 60 seconds
- Reports on:
  - Height progress
  - Peer eligibility status
  - Handshake_pending peers
  - Force peer connectivity

### Expected Log Output

When the fix is working, you should see log messages like:

```
INFO: Discarded blocks from ineligible peers
  discarded_buffer: 3
  discarded_inflight: 5
  ineligible_peers: ['5.189.172.222:59462']
  eligible_peers: ['144.126.133.21:30333', '113.22.229.190:51428', ...]

WARNING: Block sync stall handled
  reason: blocks stalled
  new_peer: 144.126.133.21:30333
  discarded_blocks: {buffer: 3, inflight: 5, cache: 0}
```

## Files Modified

- `p2p/node/p2p_service.py`: Core sync logic changes (~150 lines added)
- `p2p/tests/test_discard_ineligible_peers.py`: Comprehensive test suite (new file, ~280 lines)
- `verify_ineligible_peer_fix.py`: Manual verification script (new file, ~200 lines)

## Backward Compatibility

✅ **Fully backward compatible**:
- No API changes
- No protocol changes
- No database schema changes
- No configuration changes
- Only internal sync logic modified

## Performance Impact

- **CPU**: Negligible - one additional eligibility check per stall_timeout/2 seconds
- **Memory**: No change - blocks are moved, not duplicated
- **Network**: Improved - faster recovery means less time in stalled state
- **Sync Time**: Improved - automatic recovery eliminates manual intervention delays

## Monitoring

After deployment, monitor:

1. **Stall frequency**: Check logs for "Block sync stalled" messages
2. **Recovery rate**: Check for "Discarded blocks from ineligible peers" messages
3. **Force peer usage**: Check for "new_peer: 144.126.133.21:30333" in stall logs
4. **Manual intervention**: Track usage of `animica sync force` command (should decrease)

### Metrics Commands

```bash
# Count stall detections
grep "Block sync stalled" /var/log/animica/node.log | wc -l

# Count automatic recoveries
grep "Discarded blocks from ineligible peers" /var/log/animica/node.log | wc -l

# Check force peer selection
grep "144.126.133.21" /var/log/animica/node.log | grep "new_peer"

# Monitor sync status
animica sync status --json | jq '{phase, height, ineligible_peers_for_blocks}'
```

## Rollback Plan

If issues arise, the changes can be easily reverted:

```bash
git revert <commit-hash>
git push origin main
```

The changes are minimal and isolated to the sync loop, making rollback safe and straightforward.

## Related Issues

This fix addresses:
- Sync stalls with handshake_pending peers
- Blocks stuck in buffer/inflight from ineligible peers
- Lack of force peer prioritization during recovery
- Accumulation of stale blocks over time

## Conclusion

This fix resolves the sync stall issue caused by ineligible peers by:
1. Actively discarding blocks from ineligible peers
2. Re-queuing blocks for fetching from eligible peers
3. Prioritizing trusted force peers during recovery
4. Adding periodic cleanup to prevent accumulation

The result is a more robust sync process that can automatically recover from peer eligibility changes without requiring manual intervention.
