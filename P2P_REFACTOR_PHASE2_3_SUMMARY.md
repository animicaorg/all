# P2P Refactor Phase 2 & 3 Implementation Summary

## Overview
Successfully implemented Phase 2 (Deterministic Handshake) and Phase 3 (Peer Tip Exchange & Tracking) of the P2P subsystem refactor. Both phases integrate cleanly with the existing PeerRegistry state machine from Phase 1.

## Phase 2: HandshakeManager

### Location
`p2p/node/handshake.py`

### Key Features
- **Deterministic state machine**: DIALING → HANDSHAKING → CONNECTED/FAILED
- **Timeout enforcement**:
  - Dial timeout: 8.0s (connection establishment)
  - Handshake timeout: 15.0s (identity validation)
- **Identity validation**: chain_id + genesis_hash (case-insensitive)
- **Exponential backoff**: via PeerRegistry.mark_error()
- **Comprehensive logging**: INFO level for all state transitions and failures

### API
```python
manager = HandshakeManager(
    registry=peer_registry,
    dial_timeout_s=8.0,
    handshake_timeout_s=15.0,
    chain_id=1337,
    genesis_hash="abc..." 
)

# Start handshake
session_id = manager.start_handshake("tcp://peer:8000", "outbound")

# Process Hello message
manager.on_hello_received(session_id, peer_id, version, agent)

# Validate identity
success, error = manager.on_identity_received(session_id, chain_id, genesis_hash)

# Check timeouts periodically
timed_out = manager.check_timeouts()
```

### State Transitions
1. **register()** → DIALING
2. **mark_identified()** → HANDSHAKING (on peer_id received)
3. **mark_identity_validated()** → CONNECTED (on validation success)
4. **mark_identity_failed()** → FAILED (on validation failure)
5. **check_timeouts()** → FAILED (on timeout)

## Phase 3: TipManager

### Location
`p2p/node/tip_manager.py`

### Key Features
- **Periodic tip polling**: identify stale tips (>30s default)
- **Tip storage**: delegates to PeerRegistry.update_peer_tip()
- **Best tip computation**: highest height from fresh peers
- **Freshness tracking**: configurable window (default 600s)
- **Automatic cleanup**: on peer disconnect

### API
```python
manager = TipManager(
    registry=peer_registry,
    poll_interval_s=30.0,
    freshness_window_s=600.0
)

# After handshake complete
should_request = manager.on_handshake_complete(session_id)

# Process received tip
manager.on_tip_received(session_id, height=100, hash_hex="...", tip_time=...)

# Get peers needing refresh
to_poll = manager.poll_peer_tips()

# Get network best
height, hash_hex, peer_id, age = manager.get_best_tip()

# Get statistics
total, fresh, stale = manager.get_tip_stats()
```

### Integration with Handshake
After `mark_identity_validated()` succeeds:
1. Call `tip_manager.on_handshake_complete(session_id)`
2. Send HeadStatus request to peer
3. On response, call `tip_manager.on_tip_received()`

## Tests Added

### test_handshake_timeout.py
- Dial timeout enforcement
- Handshake timeout enforcement
- No timeout for completed handshakes
- Cleanup of timed out sessions
- Multiple timeout types (dial vs handshake)

### test_handshake_identity_validation.py
- Valid identity (matching chain_id + genesis_hash)
- Chain ID mismatch rejection
- Genesis hash mismatch rejection
- Case-insensitive genesis hash comparison
- Empty genesis hash skips validation
- Multiple validation failures
- Remote credentials storage

### test_tip_manager.py
- Tip update storage
- Stale tip identification
- Connected-only polling
- Best tip computation
- Best tip with stale data exclusion
- Tip statistics
- Poll after handshake complete
- Session cleanup
- Poll failure tracking
- No poll without identity
- Poll interval enforcement

## Backward Compatibility

### PeerTipTracker Deprecation
Added TODO comment to `PeerTipTracker` class in `p2p_service_legacy.py`:
```python
"""
TODO: This class is deprecated and will be removed in Phase 4 of the P2P refactor.
Use p2p.node.tip_manager.TipManager + PeerRegistry.update_peer_tip() instead.
...
"""
```

### No Breaking Changes
- All new code is additive
- Old code continues to work
- Both implementations coexist safely
- Migration can happen incrementally

## Validation Results

All manual tests passed:
- ✅ HandshakeManager: state transitions work correctly
- ✅ HandshakeManager: timeouts trigger as expected
- ✅ HandshakeManager: identity validation catches mismatches
- ✅ TipManager: tip updates stored correctly
- ✅ TipManager: polling identifies stale peers
- ✅ TipManager: best tip computed correctly
- ✅ TipManager: statistics accurate

## Next Steps (Phase 4)

1. **Integration**: Wire HandshakeManager into p2p_service_legacy.py
2. **Migration**: Replace PeerTipTracker usage with TipManager
3. **Cleanup**: Remove deprecated code after migration complete
4. **Testing**: Run full integration tests with live network
5. **Metrics**: Add Prometheus metrics for handshake/tip stats

## Files Modified

### New Files
- `p2p/node/handshake.py` (413 lines)
- `p2p/node/tip_manager.py` (237 lines)
- `p2p/tests/test_handshake_timeout.py` (224 lines)
- `p2p/tests/test_handshake_identity_validation.py` (303 lines)
- `p2p/tests/test_tip_manager.py` (353 lines)

### Modified Files
- `p2p/node/p2p_service_legacy.py` (added deprecation comment)

## Design Principles

1. **Single Responsibility**: HandshakeManager handles handshake orchestration, TipManager handles tip tracking
2. **Clean Separation**: State storage in PeerRegistry, behavior in managers
3. **Testability**: Pure functions, time injection for deterministic tests
4. **Observability**: Structured logging at INFO level for operations
5. **Backward Compatibility**: Incremental migration, no breaking changes

## Performance Characteristics

### HandshakeManager
- O(n) timeout checks where n = active handshakes
- O(1) state transitions
- Minimal memory overhead (small HandshakeSession structs)

### TipManager
- O(n) poll checks where n = connected peers
- O(1) tip updates
- O(n) best tip computation (scans all connected peers)
- Minimal memory overhead (just poll timestamps)

## Logging Output Examples

### Handshake Success
```
INFO Handshake started remote=tcp://peer:8000 direction=outbound state=DIALING
INFO Hello received, peer identified remote=tcp://peer:8000 peer_id=abc... state=HANDSHAKING duration_s=0.123
INFO Identity validated, handshake complete remote=tcp://peer:8000 state=CONNECTED duration_s=0.456
```

### Handshake Failure
```
WARN Identity validation failed: chain_id mismatch remote=tcp://peer:8000 local_chain_id=1337 peer_chain_id=9999 state=FAILED
WARN Handshake failed: dial timeout remote=tcp://peer:8000 elapsed_s=8.123 timeout_s=8.0 state=FAILED
```

### Tip Updates
```
INFO Peer tip updated remote=tcp://peer:8000 peer_id=abc... height=12345 age_s=0.1
```

