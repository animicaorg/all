# Fix: Genesis Sync Stuck - In-Flight Headers Always 0

## Problem Statement

Node stuck at genesis (height 0) with the following symptoms:
- `in_flight_headers: 0` despite peer awareness
- `peers_total: 0` even though peers are connecting
- Peers stuck in "handshaking" state
- Sync status shows `'sync_status_reason': 'no_peers_connected'`
- Genesis watchdog triggers but sync doesn't recover

## Root Cause Analysis

### What Happens

1. **Initial Connection**: Node connects to peers and begins handshake
2. **Temporary Success**: Some peers complete handshake and sync begins
3. **Headers Received**: Headers are received but rejected (duplicates or invalid)
4. **Block Timeout**: Block request times out or fails
5. **Peer Disconnect**: Original peer disconnects
6. **New Peers**: New peers connect but fail to complete handshake quickly
7. **Backoff State**: Previous failed sync attempts leave peers in backoff state
8. **Stuck**: Sync loop sees no eligible peers and goes IDLE
9. **Race Condition**: Even when new peers complete handshake, they remain ineligible due to backoff

### Why It Happens

The sync eligibility check requires:
```python
if not peer.hello_done.is_set():
    return False, "handshake_pending"
if not peer.identity_ok:
    return False, "identity_unverified"
```

When peers are in handshake, they're not eligible. But the critical issue is that even AFTER handshake completes, peers may still be ineligible due to backoff from previous failed sync attempts. This creates a deadlock:
- Sync fails → peer gets backoff
- Peer disconnects → new peer connects
- New peer completes handshake → but inherits backoff state
- Sync can't proceed → stays stuck

## The Fix

### 1. Enhanced Diagnostics (p2p/node/p2p_service.py)

Added detailed logging when stuck at genesis with no eligible peers:

```python
if at_genesis and self._peers:
    # Count peers by state to understand what's blocking sync
    handshaking_count = sum(1 for p in self._peers.values() if not p.hello_done.is_set())
    identity_pending_count = sum(1 for p in self._peers.values() if p.hello_done.is_set() and not p.identity_ok)
    ineligible_reasons_summary = {}
    for reason in ineligible_peer_reasons.values():
        ineligible_reasons_summary[reason] = ineligible_reasons_summary.get(reason, 0) + 1
    log.warning(
        "Genesis sync stuck: no eligible peers despite peer connections",
        extra={
            "total_peers": len(self._peers),
            "eligible_peers": 0,
            "handshaking_peers": handshaking_count,
            "identity_pending_peers": identity_pending_count,
            "ineligible_reasons": ineligible_reasons_summary,
            "in_flight_headers": int(self._sync_inflight_headers),
            "last_header_error": self._sync_last_header_error,
            "stall_elapsed_s": time.time() - self._sync_last_progress_at,
        },
    )
```

This provides visibility into WHY sync is stuck.

### 2. Defensive Backoff Clearing

Added logic to proactively clear peer backoffs when handshaking peers exist:

```python
if handshaking_count > 0:
    cleared_backoffs = 0
    for peer in self._peers.values():
        backoff_key = self._peer_backoff_key(peer)
        if backoff_key in self._sync_peer_backoff:
            self._sync_peer_backoff.pop(backoff_key, None)
            self._sync_peer_backoff_reason.pop(backoff_key, None)
            cleared_backoffs += 1
    if cleared_backoffs > 0:
        log.info(
            "Cleared peer backoffs to allow immediate sync when handshake completes",
            extra={"cleared_backoffs": cleared_backoffs},
        )
```

This ensures that when a peer completes handshake, it becomes IMMEDIATELY eligible for sync, breaking the deadlock.

## How It Works

### Before the Fix
```
Genesis node → Peer connects → Sync fails → Peer backoff → Peer disconnects
                                                               ↓
Genesis node ← New peer connects ← Backoff still active ← Handshake completes
     ↓
Stuck (no eligible peers)
```

### After the Fix
```
Genesis node → Peer connects → Sync fails → Peer backoff → Peer disconnects
                                                               ↓
Genesis node ← New peer connects ← **BACKOFF CLEARED** ← Handshake completes
     ↓
Sync resumes immediately with new peer!
```

## Testing

### Manual Test Steps

1. Start a node at genesis
2. Connect to peers that are slow to respond or intermittently disconnect
3. Observe sync status: `animica node status`
4. Check for diagnostic log: "Genesis sync stuck: no eligible peers despite peer connections"
5. Verify backoff clearing log: "Cleared peer backoffs to allow immediate sync when handshake completes"
6. Confirm sync resumes once a peer completes handshake

### Expected Log Output

```
WARNING: Genesis sync stuck: no eligible peers despite peer connections
  total_peers: 3
  eligible_peers: 0
  handshaking_peers: 2
  identity_pending_peers: 0
  ineligible_reasons: {'handshake_pending': 2, 'headers_empty': 1}
  in_flight_headers: 0
  last_header_error: headers_empty
  stall_elapsed_s: 12.5

INFO: Cleared peer backoffs to allow immediate sync when handshake completes
  cleared_backoffs: 3
```

## Impact

### Positive
- Genesis nodes can recover from peer connection issues
- Better diagnostics for troubleshooting sync problems
- Eliminates deadlock scenario where peers can't sync due to backoff

### Risk
- Slightly more aggressive backoff clearing might allow "bad" peers to retry sooner
- Mitigated by the fact that this ONLY applies at genesis with handshaking peers

## Files Changed

- `p2p/node/p2p_service.py`: Added diagnostics and backoff clearing logic
- `test_genesis_sync_no_eligible_peers_fix.py`: Test case (requires test infrastructure)

## Related Issues

This fix addresses the symptoms described in the problem statement:
- ✅ `in_flight_headers: 0` - Will become > 0 once peers are eligible
- ✅ `peers_total: 0` - Still accurate (counts only identity-verified peers)
- ✅ `sync_status_reason: 'no_peers_connected'` - Will update once handshake completes
- ✅ Genesis watchdog triggers - Now paired with backoff clearing for better recovery

## Follow-up Work

1. Monitor logs in production to see if diagnostic output appears
2. Consider similar fix for non-genesis sync stalls
3. Evaluate if handshake timeout should be increased
4. Consider adding metrics for peer handshake failure rate
