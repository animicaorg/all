# Fix: Mempool Not Adding Known Transactions from Peers

## Problem Statement

The mempool was not adding transactions that were announced by peers, despite those transactions being tracked in the peer's `known_txids`. This manifested as:

```
animica mempool list
...
Peer-known txids (sample):
  peer=0x75cd939ef1 conn_id=0xf2c27689-6 known_txids=1 sample=[0xaefd5e4f9a...]
  peer=0x75cd939ef1 conn_id=0xd276c0a0-a known_txids=1 sample=[0xaefd5e4f9a...]
Mempool is empty (no pending transactions)
```

Peers had transactions in their `known_txids`, but the local mempool remained empty.

## Root Cause

The issue was in the transaction request state management in `p2p/txrelay.py`. When a transaction was successfully admitted to the mempool, it would be marked as `"accepted_in_mempool"` in the request manager. However, if:

1. The transaction was later evicted from the mempool (e.g., due to size limits or fee requirements)
2. OR the state became inconsistent for any reason

And then a peer announced the same transaction again via INV message, the following would happen:

1. The txid would be added to the peer's `known_txids` set (line 464)
2. The `mark_announced()` method would be called (line 466), but it would return early without updating the state because it was already `"accepted_in_mempool"` (lines 119-120)
3. When checking if the transaction should be requested, `can_request()` would return False because the state was `"accepted_in_mempool"` (lines 148-151)
4. The transaction would never be requested from the peer

This created a situation where:
- Peers knew about the transaction (`known_txids` populated)
- But the transaction was never fetched or added to the local mempool

## The Fix

The fix adds logic in the `on_tx_inv` method to detect and clear stale `"accepted_in_mempool"` state before checking if the transaction can be requested:

```python
# Clear stale "accepted_in_mempool" state if transaction is not actually in mempool.
# We only reach this point after confirming the transaction is neither in the mempool
# nor in the chain (via has_tx and has_chain_tx checks above). So if the state is
# "accepted_in_mempool" here, it means the state is stale/inconsistent.
req_state = self._request_mgr.get_state(txid)
if req_state is not None and req_state.state == "accepted_in_mempool":
    # Transaction announced by peer but marked as accepted while not in mempool
    self._request_mgr.clear_state(txid)
    log.info("TX_STATE_CLEARED", ...)
```

This logic:
1. Only runs after confirming the transaction is NOT in the mempool (via `has_tx` check)
2. Only runs after confirming the transaction is NOT in the chain (via `has_chain_tx` check)
3. Clears the stale state, allowing the transaction to be re-requested
4. Logs the state clearing for debugging purposes

## Why This Is Safe

The state clearing is safe because:

1. **Verified not in mempool**: The code only reaches this point if `has_tx(txid)` returned False, meaning the transaction is definitely not in the current mempool.

2. **Verified not in chain**: The code also checks `has_chain_tx(txid)`, so we know the transaction isn't already mined.

3. **Peer announcement**: A peer is actively announcing this transaction, indicating they have it and it's still relevant.

4. **State inconsistency**: If the state says "accepted_in_mempool" but the transaction isn't actually in the mempool, the state is by definition stale/inconsistent.

5. **Preserves valid state**: The check at the beginning of the flow (`has_tx` at line 501) ensures that if a transaction IS in the mempool, we don't reach the state clearing code at all.

## Test Coverage

Two comprehensive tests were added in `p2p/tests/test_txrelay_stale_state_fix.py`:

### Test 1: `test_stale_accepted_state_cleared_on_new_announcement`

Reproduces the bug scenario:
1. Transaction is announced by peer A and admitted to mempool
2. Transaction is evicted/removed from mempool (simulated by `has_tx` returning False)
3. Peer B announces the same transaction
4. **Expected**: System should clear the stale state and request the transaction
5. **Verified**: Transaction is successfully requested and admitted

### Test 2: `test_valid_accepted_state_not_cleared`

Ensures the fix doesn't break valid state:
1. Transaction is announced by peer A and admitted to mempool
2. Transaction remains in mempool (`has_tx` returns True)
3. Peer B announces the same transaction
4. **Expected**: System should NOT request the transaction (it already has it)
5. **Verified**: Transaction is not re-requested, state remains "accepted_in_mempool"

Both tests pass successfully.

## Impact

This fix ensures that transactions announced by peers are properly requested and added to the mempool, even when state tracking becomes inconsistent due to:
- Mempool evictions
- State resets
- Other transient conditions

The mempool will now correctly reflect transactions available from the network, improving transaction propagation and block mining efficiency.

## Files Modified

1. **p2p/txrelay.py**: Added state cleanup logic in `on_tx_inv` method (15 lines added)
2. **p2p/tests/test_txrelay_stale_state_fix.py**: Added comprehensive test coverage (194 lines, 2 tests)

## Related Code Patterns

Similar state cleanup logic already exists in the mempool watchdog (`request_missing_known` method, lines 1200-1211), which handles the same type of stale state issue. This fix applies the same principle to the INV message handling path.
