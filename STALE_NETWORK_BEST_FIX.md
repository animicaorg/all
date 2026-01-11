# Sync Stall Fix: Clear Inflight Requests on stale_network_best

## Problem Statement

Nodes were getting stuck during sync with the following symptoms:
```
Sync phase:       HEADERS
In-flight:        headers=1 blocks=0
Last header error: stale_network_best
Last recovery:    stale_network_best (attempt 0)
```

The node would remain in this state indefinitely, unable to make progress.

## Root Cause

When the `stale_network_best` condition was detected (all connected peers report a height ≤ local height, but network_best_height > local height), the code would:

1. Call `_force_peer_refresh` to enable seeding mode and find new peers
2. Call `_sync_kick` with aggressive=True to boost sync

**However** - it did not clear the stale inflight header request that was blocking new requests.

The stale request remained in `_sync_inflight_header_requests`, causing:
- `_sync_inflight_headers = 1` (blocking new requests)
- Sync loop would see inflight request and skip scheduling new ones
- Node stuck forever waiting for a response that would never come

## Solution

Added a call to `_reset_sync_state` when handling `stale_network_best`:

```python
elif empty_reason == "stale_network_best":
    self._force_peer_refresh(reason="stale_network_best")
    self._reset_sync_state(reason="stale_network_best")  # <-- ADDED THIS
    self._sync_kick(
        reason="stale_network_best",
        aggressive=True,
    )
    tried_peers.add(peer.remote)
```

### What _reset_sync_state does

This method clears all sync pipeline state:
- All inflight header and block requests
- All queues (header queue, block queue, retry queue)
- All error states
- Peer-specific pending state
- Duplicate detection state

This is the same approach used by the watchdog when detecting a stall.

## Recovery Flow

With the fix, when `stale_network_best` is detected:

1. **_force_peer_refresh**: Enable seeding mode, clear dial backoff, find new peers
2. **_reset_sync_state**: Clear ALL inflight requests and state (including the stuck request)
3. **_sync_kick(aggressive=True)**: Immediately retry with fresh state and boosted sync

The node can now:
- Start with a clean slate
- Immediately request headers from new peers
- Make rapid progress with boosted sync parameters

## Performance Impact

This fix enables **ultra-fast recovery** from stale network state:
- No waiting for request timeouts
- No manual intervention needed
- Immediate retry with fresh peers
- Aggressive sync boost for rapid catch-up

## Testing

See `test_stale_network_best_fix.py` for validation tests.

## Files Changed

- `p2p/node/p2p_service.py` - Added `_reset_sync_state` call on line 8540

## Related Issues

This fix addresses the specific case reported in the issue where:
- Local head: 5394
- Best peer head: 5394
- Sync stuck in HEADERS phase with in-flight: headers=1

The node needed to sync "really fast" but was completely stuck. With this fix, it will recover immediately and sync at maximum speed.
