# Fix Summary: Nodes Stuck in Handshaking Phase

## Problem Description

Nodes were getting stuck in "handshaking" phase indefinitely, showing peers as:
```
inbound peer unknown (...) [handshaking]
```

The timeout mechanism (`ANIMICA_P2P_HANDSHAKE_TIMEOUT`, default 3 seconds) was not triggering properly, leaving peers in limbo.

## Root Cause

The `hello_done` event was being set **before** all validation checks completed in the handshake flow. The sequence was:

1. Peer connects
2. HELLO message received and basic validation passes
3. `peer.hello_done.set()` called (line 6736)
4. **Timeout watchdog stops monitoring** (sees hello_done is set)
5. Additional validation checks run (e.g., self_peer check, duplicate peer_id check)
6. Validation fails with exception
7. **Peer is stuck**: hello_done is set but peer is not fully connected or properly dropped

## Affected Code Paths

After the premature `hello_done.set()` at line 6736, there were several paths that could fail:

1. **Lines 6814, 6833**: Self-peer validation checks that raise `PeerMisbehavior`
2. **Line 6877-6880**: Duplicate peer_id detection with early return
3. **Lines 6885-6896**: Peerstore operations that could fail

## Solution

### Change 1: Move hello_done.set() to End of Validation
```python
# OLD (line 6736):
peer.hello_done.set()
# ... more validation code ...

# NEW (line 6904):
await self._send(peer, MsgID.HELLO_ACK, HelloAck(accepted=True, reason=None))

# Set hello_done ONLY after all validations pass and HELLO_ACK is sent
# This ensures the timeout watchdog doesn't stop monitoring if validation fails
peer.hello_done.set()
```

### Change 2: Handle Early Return in Duplicate Peer Case
```python
# NEW (line 6876-6878):
if dup_peer.session_id == peer.session_id:
    # Set hello_done before dropping to stop timeout watchdog
    peer.hello_done.set()
    await self._drop_peer(peer, reason="duplicate_peer_id")
    return
```

## Impact

### Before Fix
- Peers that failed validation after line 6736 would get stuck
- Timeout watchdog stopped monitoring too early
- Manual intervention required to clear stuck peers
- Could accumulate "ghost" peers over time

### After Fix
- Timeout watchdog monitors until handshake truly completes
- Failed validations properly trigger timeout and peer drop
- Early returns signal watchdog to stop cleanly
- No stuck peers in handshaking state

## Testing

### Simulation Test
Created a simulation test that demonstrates:
- ✗ **Buggy behavior**: hello_done set early → validation fails → peer stuck
- ✓ **Fixed behavior**: hello_done set late → validation fails → timeout triggers
- ✓ **Success case**: validation passes → hello_done set → handshake completes

### Existing Tests
All existing p2p tests pass:
- `p2p/tests/test_peer_registry.py` (2 tests passed)
- `p2p/tests/test_handshake.py` (4 tests passed)

## Files Changed

- `p2p/node/p2p_service.py`:
  - Line 6736: Removed premature `hello_done.set()` (replaced with comment)
  - Line 6878: Added `hello_done.set()` before early return
  - Line 6904: Added `hello_done.set()` after all validations

## Backward Compatibility

✅ **Fully backward compatible**
- No API changes
- No protocol changes
- No configuration changes
- All validation logic unchanged
- Only timing of hello_done event changed

## Verification Steps

To verify the fix is working:

1. Monitor peer count: `animica peer list`
2. Check for peers stuck in handshaking: No peers should show `[handshaking]` for more than the timeout period (default 3 seconds)
3. Check logs for "Peer handshake timeout" messages: These should now appear when validation fails
4. Monitor peer connection stability: Properly validated peers should connect normally

## Related Documentation

- `MAINNET_CHAIN_ID_AND_PEER_DEBUGGING.md` (lines 129-145): Documents the original issue
- `GENESIS_SYNC_FIX_VERIFICATION.md` (line 174): Verification criteria for handshake fixes
