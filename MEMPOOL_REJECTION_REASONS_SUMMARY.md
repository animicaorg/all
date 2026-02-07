# Mempool List Rejection Reasons - Before & After

## Problem Statement

When running `animica mempool list`, if transactions from peers fail to import into the local mempool, the command only shows a generic note about possible reasons without specific details about what actually happened to each transaction.

### Before (Generic Output)

```
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 2.0s)
  Note: Transactions may have been:
    • Rejected during validation (hash mismatch, invalid signature)
    • Failed mempool admission (insufficient balance, nonce conflict, low fee)
    • Not available on peers (responded with TX_NOTFOUND)
  Check node logs for: TX_DATA_ADMIT_RESULT, TX_REJECTED, TX_NOTFOUND
Mempool is empty (no pending transactions)
```

**Issues:**
- Users must check logs to understand why transactions failed
- No visibility into which specific transactions failed
- No per-transaction rejection reasons
- Difficult to debug transaction propagation issues

### After (Specific Output)

```
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 2.0s)
  Rejection details:
    0xabc123def4 state=received_invalid reason=invalid_signature peer=0xpeer123456 attempts=1
    0xdef4567890 state=dropped_evicted reason=insufficient_balance peer=0xpeer987654 attempts=2
Mempool is empty (no pending transactions)
```

**Benefits:**
- Shows exact rejection reason for each transaction
- Displays which peer provided the transaction
- Shows how many attempts were made
- Easy to identify issues without checking logs
- Better debugging experience

## Implementation Details

### 1. Enhanced RPC Response

**File:** `/home/runner/work/all/all/rpc/methods/p2p.py`

The `p2p.importPeerKnownTxs` RPC method now includes transaction state details in its response:

```python
return {
    "success": True,
    "requested": requested,
    "limit": lim,
    "tx_state_sample": tx_state_sample,  # NEW: Transaction state details
}
```

The `tx_state_sample` contains up to 100 transaction states from the TxRelayService, including:
- `txid`: Transaction hash
- `state`: Current state (e.g., "received_invalid", "dropped_evicted", "requested")
- `last_reason`: Specific rejection reason (e.g., "invalid_signature", "insufficient_balance")
- `last_peer`: Peer that provided the transaction
- `attempts`: Number of request attempts

### 2. Enhanced CLI Display

**File:** `/home/runner/work/all/all/python/animica/cli/mempool.py`

The CLI now parses and displays rejection details:

```python
if tx_state_sample:
    rejected_txs = []
    for tx_state in tx_state_sample:
        state = tx_state.get("state", "")
        if state not in {"accepted_in_mempool"}:
            rejected_txs.append(tx_state)
    
    if rejected_txs:
        print("  Rejection details:")
        for tx_state in rejected_txs[:20]:  # Limit to 20 for readability
            txid = tx_state.get("txid")
            state = tx_state.get("state")
            reason = tx_state.get("last_reason")
            peer = tx_state.get("last_peer")
            attempts = tx_state.get("attempts")
            
            reason_text = f" reason={reason}" if reason else ""
            print(f"    {txid} state={state}{reason_text} peer={peer} attempts={attempts}")
```

### 3. Backwards Compatibility

The implementation maintains backwards compatibility:
- If `tx_state_sample` is empty or unavailable, falls back to generic note
- If all transactions are accepted, no rejection section is shown
- Existing functionality remains unchanged

## Transaction States

The following states may appear in rejection details:

| State | Description |
|-------|-------------|
| `requested` | Transaction requested from peer, awaiting response |
| `received_invalid` | Transaction received but failed validation |
| `dropped_evicted` | Transaction was dropped from mempool (e.g., insufficient balance) |
| `announced_only` | Transaction was announced but not yet requested |
| `received_valid_pending` | Transaction received and valid, pending admission |
| `accepted_in_mempool` | Transaction successfully added to mempool |

## Common Rejection Reasons

| Reason | Description |
|--------|-------------|
| `invalid_signature` | Transaction signature is invalid |
| `insufficient_balance` | Sender doesn't have enough balance |
| `nonce_conflict` | Nonce is already used or out of order |
| `hash_mismatch` | Transaction hash doesn't match content |
| `in_chain` | Transaction is already in the blockchain |

## Testing

### Manual Test Script

A comprehensive test script was created at `/home/runner/work/all/all/test_mempool_rejection_reasons.py` that verifies:
1. Rejection details are properly formatted and displayed
2. Generic fallback works when no state info available
3. No rejection section shown when all transactions accepted

Run the test:
```bash
python3 test_mempool_rejection_reasons.py
```

### Updated CLI Tests

The existing CLI test at `/home/runner/work/all/all/python/animica/cli/tests/test_mempool_cli.py` was updated to include the new response format and a new test was added specifically for rejection reasons display.

## User Experience Improvements

1. **Immediate Visibility**: Users can see why transactions failed without checking logs
2. **Per-Transaction Details**: Each failed transaction shows its specific rejection reason
3. **Debugging Aid**: Easier to identify network, validation, or balance issues
4. **Peer Attribution**: Shows which peer provided each transaction
5. **Retry Information**: Displays number of attempts for each transaction

## Future Enhancements

Potential improvements for future iterations:
1. Add color coding for different rejection reasons
2. Provide suggested actions for common rejection reasons
3. Allow filtering by specific rejection reasons
4. Export rejection details to JSON for automated analysis
5. Add summary statistics (e.g., "2 invalid signatures, 1 insufficient balance")
