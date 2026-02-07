# Fix Summary: Transaction Fetching from Peers

## Problem Statement

Transactions were not being mined even though peers reported knowing about them. The mempool remained empty despite automatic fetch attempts from peers.

**Specific Issue from Logs:**
```
peer=0xb11a50ed93 conn_id=0x17be5add-d known_txids=1 sample=[0xe4d5b3e10009b29b75f6ca9cb53244145b37005efddb772816b125fba44792ef]
peer=0xb11a50ed93 conn_id=0x7e65d4f3-a known_txids=1 sample=[0xe4d5b3e10009b29b75f6ca9cb53244145b37005efddb772816b125fba44792ef]

💡 Tip: Peers know about 2 transaction(s). Fetching them automatically...
✓ Requested 2 transaction(s) from peers. Run 'animica mempool list' again in a few seconds to see them.

[After waiting]
Mempool is empty (no pending transactions)
```

## Root Cause

The `on_tx_notfound` handler in `/home/runner/work/all/all/p2p/txrelay.py` was clearing the transaction ID from **ALL** peers' `known_txids` sets when any single peer responded with TX_NOTFOUND. This prevented retry attempts to other peers who actually had the transaction.

**Problematic Code (lines 1267-1283):**
```python
# Clear from ALL peers' known_txids since the responding peer doesn't have it.
# This prevents infinite loops where multiple peers report knowing about a
# transaction that none of them actually have (e.g., after it was mined or evicted).
removed_from = []
for peer_id, peer_state in self._peer_state.items():
    if txid in peer_state.known_txids:
        peer_state.known_txids.remove(txid)
        removed_from.append(peer_id)
```

**Why This Was Too Aggressive:**
1. One peer/connection might not have the transaction while another does
2. Transaction could be temporarily unavailable or in transit
3. Same peer with multiple connections could have it on one connection but not another
4. Clearing from all peers prevents any retry attempts

## Solution

Modified `on_tx_notfound` to be more targeted:

1. **Only clear from responding peer**: Remove txid only from the peer that responded with NOTFOUND
2. **Check for other sources**: Look for other peers who still advertise having the transaction
3. **Automatic retry**: If other eligible peers exist, send TX_GET to them
4. **Graceful fallback**: Only mark as permanently rejected if no other peers have it

**Key Changes:**
- Clear txid only from the responding peer's `known_txids`
- Search for alternative peers using `_tx_sources` tracking
- Retry with another eligible peer if available
- Track retry attempts to prevent infinite loops
- Log detailed information about retry decisions

## Test Results

### New Tests (All Passing)
- `test_notfound_retries_other_peers`: Validates retry to alternate peer after NOTFOUND
- `test_notfound_from_all_peers_gives_up`: Confirms graceful termination when all peers respond NOTFOUND
- `test_notfound_only_clears_responding_peer`: Verifies selective clearing behavior

### Existing Tests (All Passing)
- `test_txrelay_timeout_recovery.py`: 2/2 tests pass
- `test_tx_relay.py`: 4/4 tests pass  
- `test_txrelay_metrics.py`: 1/1 tests pass
- `test_txrelay_stale_state_fix.py`: 2/2 tests pass
- `test_txrelay_watchdog.py`: 4/4 tests pass

### Manual Integration Tests
- `test_notfound_retry_manual.py`: Demonstrates multi-peer retry behavior
- `test_bug_report_scenario.py`: Replicates exact bug report scenario and validates fix

**Note on test_inflight_timeout_retries**: This test was already failing before our changes and is unrelated to the NOTFOUND handling. It appears to be a pre-existing test issue.

## Impact

**Before Fix:**
1. First peer responds NOTFOUND → all peers lose the txid
2. No retry attempts to other peers
3. Transaction never fetched
4. Mempool remains empty

**After Fix:**
1. First peer responds NOTFOUND → only that peer loses the txid
2. Automatic retry to other peers who still have it
3. Transaction successfully fetched from alternate peer
4. Transaction admitted to mempool and available for mining

## Files Changed

- `p2p/txrelay.py`: Modified `on_tx_notfound` method (lines 1247-1370)
- `p2p/tests/test_txrelay_notfound_retry.py`: New comprehensive test suite
- `test_notfound_retry_manual.py`: Manual integration test
- `test_bug_report_scenario.py`: Bug report scenario validation

## Security Considerations

- No new security vulnerabilities introduced
- Maintains protection against infinite retry loops via:
  - Max retry attempts tracking
  - Reject cache for permanently failed transactions
  - Cooldown periods between retry attempts
  - Eligibility checks before each retry
- Actually improves robustness by ensuring legitimate transactions can be recovered

## Deployment Notes

- No configuration changes required
- No database migrations needed
- Backward compatible with existing peers
- Should improve transaction propagation reliability immediately upon deployment
