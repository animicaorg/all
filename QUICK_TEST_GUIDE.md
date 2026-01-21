# Quick Test Guide - P2P Phase 4 & 5

## Pre-Test Verification

```bash
# 1. Verify code compiles
python3 -m py_compile p2p/node/p2p_service_legacy.py

# 2. Check imports are present
grep -n "HandshakeManager\|TipManager" p2p/node/p2p_service_legacy.py

# 3. Verify managers initialized
grep -n "self._handshake_manager\|self._tip_manager" p2p/node/p2p_service_legacy.py
```

## Manual Test Cases

### Test 1: Node Startup
**Expected**: Node starts without errors, managers initialize

```bash
# Start node
animica node start

# Check logs for:
# - No import errors
# - Manager initialization (look in startup logs)
# - Genesis hash resolution
```

### Test 2: Handshake Flow
**Expected**: Connections complete handshake, identity validated

**Check logs for**:
```
INFO: HandshakeManager: identity validation complete
INFO: TipManager: handshake complete, will request initial tip
INFO: Peer handshake completed successfully
```

### Test 3: Tip Updates
**Expected**: HEAD_STATUS messages processed, tips recorded

**Check logs for**:
```
INFO: Received HEAD_STATUS update
INFO: TipManager: tip received and recorded
```

### Test 4: Periodic Polling
**Expected**: Tips polled every 30 seconds

**Check logs for** (after 30s):
```
INFO: TipManager: polling peer tips
```

### Test 5: Timeout Detection
**Expected**: Timed out handshakes detected and dropped

**Simulate timeout** (connect then don't complete handshake):
```
# Check logs for (after 15s):
INFO: HandshakeManager: detected timed out handshakes
```

### Test 6: Status Endpoint
**Expected**: Status includes status_version="2.0"

```bash
# Query status
animica sync status

# Verify JSON includes:
{
  "status_version": "2.0",
  "head_hash": "0x..." (not null),
  "best_remote_height": ...,
  "best_remote_hash": ...,
  "best_remote_peer": ...,
  "peer_tips_fresh": ...,
  ...
}
```

## Log Patterns to Monitor

### SUCCESS Patterns
```
✅ "HandshakeManager: identity validation complete"
✅ "TipManager: handshake complete, will request initial tip"
✅ "TipManager: tip received and recorded"
✅ "TipManager: polling peer tips"
```

### WARNING Patterns (Non-Critical)
```
⚠️  "HandshakeManager identity validation failed"
⚠️  "TipManager handshake notification failed"
⚠️  "TipManager tip recording failed"
```

### ERROR Patterns (Should Not Occur)
```
❌ Import errors
❌ Manager initialization failures
❌ Crashes in _head_watch_loop
```

## Quick Checks

### 1. Peer Count
```bash
animica peer list
# Should show CONNECTED peers
```

### 2. Sync Status
```bash
animica sync status
# Check status_version field present
# Check head_hash is never null
```

### 3. Node Status
```bash
animica node status
# Check peers_total matches connected count
```

## Rollback (If Needed)

```bash
# If issues occur, managers fail gracefully
# Legacy PeerTipTracker still works
# No action needed - warnings only

# To verify legacy still works:
grep -n "self._peer_tip_tracker" p2p/node/p2p_service_legacy.py
```

## Success Criteria

- ✅ Node starts without errors
- ✅ Handshakes complete with INFO logs
- ✅ Tips update with INFO logs
- ✅ Periodic polling triggers every 30s
- ✅ Status has status_version="2.0"
- ✅ head_hash never null
- ✅ No crashes or ERROR logs

## Common Issues & Solutions

### Issue: Import Error
**Solution**: Verify HandshakeManager and TipManager files exist in p2p/node/

### Issue: Manager Not Initialized
**Solution**: Check __init__ method for manager creation code

### Issue: No Tip Updates
**Solution**: Check _handle_head_status for TipManager integration

### Issue: No Periodic Polling
**Solution**: Check _head_watch_loop for poll_peer_tips call

## Test Duration

- **Startup**: < 1 minute
- **Handshake**: < 30 seconds per peer
- **Tip Update**: Immediate on HEAD_STATUS
- **Periodic Poll**: First poll at 30s
- **Timeout Check**: Every 1s in loop

## Contact

If issues persist:
1. Check logs for full error traces
2. Verify all integration points are present
3. Ensure no conflicting changes in codebase
