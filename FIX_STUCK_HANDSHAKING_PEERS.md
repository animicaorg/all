# Fix: Stuck Handshaking Peers Preventing Mining

## Problem Statement

Mining was failing with the following error:
```
Warning: Block template unavailable (insufficient_peers (connected: 0, handshaking: 1, required: 1). 
Try: 'animica peer bootstrap' to connect to peers. 
Last dial error: TransportError: dial timeout to tcp://82.66.161.84:41596. 
Check: 'animica p2p doctor' for diagnostics, or set ANIMICA_MINING_MIN_PEERS=0 for local development.)
```

The issue was that a peer was stuck in the "handshaking" state indefinitely, preventing mining from proceeding.

## Root Cause

The `PeerRegistry` class has a `purge_stale()` method that removes sessions that never completed a handshake within the timeout window (default 3 seconds). However, this method was never being called from the P2P service, so stuck handshaking peers would remain indefinitely.

The sequence was:
1. Peer starts connecting (enters DIALING state)
2. Connection times out or fails during handshake
3. Peer remains in handshaking state forever
4. Mining check sees `handshaking: 1` but `connected: 0`
5. Mining cannot proceed because min_peers requirement is not met

## Solution

Added a call to `purge_stale()` in the `_task_watchdog_loop()` method in `p2p/node/p2p_service_legacy.py`. This loop runs every 5 seconds and now:

1. Calls `purge_stale()` to remove sessions that never completed handshake
2. Logs when stale peers are purged for visibility
3. Allows the system to retry connecting to other peers
4. Enables mining to proceed once a peer successfully connects

## Changes

### 1. p2p/node/p2p_service_legacy.py (lines 3047-3057)

```python
# Purge stale handshaking peers that exceeded timeout
purged = self._peer_registry.purge_stale()
if purged:
    log.info(
        "Purged stale handshaking peers",
        extra={
            "count": len(purged),
            "session_ids": purged,
            "timeout_s": self._peer_registry.handshake_timeout_s,
        }
    )
```

### 2. p2p/tests/test_peer_registry.py (lines 28-30)

Fixed pre-existing test that was using `update_meta()` instead of `mark_identity_validated()`:

```python
# Use mark_identity_validated to properly set identity_ok and state=CONNECTED
registry.mark_identity_validated(s1.session_id, chain_id=1, genesis_hash="0" * 64)
registry.mark_identity_validated(s3.session_id, chain_id=1, genesis_hash="0" * 64)
```

### 3. test_purge_stale_integration.py (new file)

Created integration test validating the exact scenario from the problem statement:
- Test that stuck handshaking peers are removed after timeout
- Test that connected peers are not affected
- Test the exact error scenario (connected: 0, handshaking: 1)

## Impact

### Before Fix
- Peers stuck in handshaking state would remain indefinitely
- Mining would be blocked waiting for connected peers
- Manual intervention required (restart node or use ANIMICA_MINING_MIN_PEERS=0)

### After Fix
- Stuck handshaking peers are automatically purged every 5 seconds
- System can retry connecting to other peers
- Mining proceeds normally once a peer connects
- Better observability with purge logging

## Testing

All existing tests pass:
- `p2p/tests/test_peer_registry.py` (2 tests)
- `p2p/tests/test_handshake_timeout.py` (5 tests)
- `test_purge_stale_integration.py` (3 new tests)

## Configuration

The timeout is controlled by `PeerRegistry.handshake_timeout_s` (default: 3.0 seconds).
Purge runs every 5 seconds as part of the task watchdog loop.

## Verification

To verify the fix is working:

1. Monitor peer status: `animica peer list`
2. Check for purge log messages: "Purged stale handshaking peers"
3. Verify no peers stuck in handshaking state for more than 3-5 seconds
4. Confirm mining proceeds when at least one peer connects successfully

## Related Files

- `p2p/node/peer_registry.py` - Contains the `purge_stale()` method
- `p2p/node/p2p_service_legacy.py` - Main P2P service that now calls `purge_stale()`
- `rpc/methods/miner.py` - Mining gate check that validates peer requirements

## Backward Compatibility

✅ Fully backward compatible
- No API changes
- No protocol changes
- No configuration changes required
- All existing tests pass
