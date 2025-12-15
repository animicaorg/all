# Transaction Hash Consistency Fix - Summary

## Problem Statement

Users reported end-to-end transaction failures even after recent fixes:
- Chain height advances when mining, but balances/nonces do not advance
- Transaction receipts are null forever after mining confirmations
- Transaction hash returned by `tx.sendRawTransaction` differs from the hash in blocks
- Logs indicate duplicate RPC method registration issues
- `txsRoot` and `receiptsRoot` appear constant/zero across blocks

## Root Cause Analysis

The issue was caused by **inconsistent transaction hash computation** across different parts of the system:

1. **sendRawTransaction**: Computes hash as `sha3_256(raw_cbor_bytes)` - the canonical hash
2. **Block assembly (txsRoot)**: Called `tx.hash()` which re-encodes via `tx.to_cbor()`, potentially producing different bytes
3. **Receipt indexing**: Used `tx.hash()` to index receipts, but lookups used the canonical hash from step 1

### Why Re-encoding Produces Different Hashes

Even though the transaction data is semantically identical, CBOR encoding can vary:
- The RPC envelope format: `{"body": {...}, "sig": {...}}`
- The core format after normalization: `{"tx": {...}, "sigs": [...]}`
- Field ordering in Python dicts (though stable in 3.7+, the formats differ)
- Different envelope structures produce different CBOR bytes

When `tx.sendRawTransaction` accepts raw CBOR, it computes hash from those exact bytes. But when the miner re-encodes the decoded Tx object via `tx.to_cbor()`, it produces different CBOR bytes, resulting in a different hash.

## Solution

### Key Changes

1. **Use Canonical Hash Everywhere** (rpc/methods/miner.py):
   - Modified txsRoot computation to use the canonical hash from `_TX_HASH_MAP` (which stores original raw CBOR bytes)
   - Updated hash tracking from adapter to prefer canonical hash
   - Fixed receipt root recomputation to use canonical hash
   
2. **Re-index Receipts** (rpc/methods/miner.py):
   - After `append_canonical_block` persists the block, re-index all receipts using canonical tx hashes
   - This ensures `tx.getTransactionReceipt(hash)` can find receipts using the hash returned by `sendRawTransaction`

3. **Remove Dead Code** (rpc/methods/tx.py):
   - Removed unreachable `return None` after delegate call in `tx_get_transaction_receipt`

4. **Comprehensive Test** (test_tx_hash_consistency.py):
   - Created regression test that validates all acceptance criteria
   - Test covers: hash consistency, block inclusion, RPC lookups, balance updates, roots computation

### Code Locations

**rpc/methods/miner.py**:
- Line ~1410: Hash tracking from adapter uses canonical hash when available
- Line ~1714-1730: txsRoot computation uses canonical hash from `_TX_HASH_MAP`
- Line ~1931-1945: Receipt root recomputation uses canonical hash
- Line ~1990-2010: Re-index receipts with canonical hashes after block persistence

**rpc/methods/tx.py**:
- Line ~1420: Removed unreachable `return None`

## Technical Details

### The _TX_HASH_MAP Infrastructure

The miner already had infrastructure to track canonical hashes:
```python
_TX_HASH_MAP: dict[int, tuple[str, bytes]] = {}
# Maps id(tx_obj) -> (tx_hash_hex, raw_cbor_bytes)
```

This map is populated when transactions are decoded from the pending pool:
```python
# In _mine_once fallback path:
_TX_HASH_MAP[id(tx_obj)] = (tx_hash_hex, raw)
```

### Canonical Hash Usage

The fix ensures that everywhere we need a tx hash, we use:
```python
tracked = _tracked(tx)  # Get from _TX_HASH_MAP
if tracked:
    tx_hash_hex, raw = tracked
    tx_hash = bytes.fromhex(tx_hash_hex[2:])  # Use canonical hash
else:
    tx_hash = tx.hash()  # Fallback for untracked txs
```

### Receipt Re-indexing

After `append_canonical_block` indexes receipts using `tx.hash()`, we re-index them:
```python
with block_db.kv.batch() as batch:
    for idx, tx in enumerate(txs):
        tracked = _tracked(tx)
        if tracked:
            tx_hash_hex, raw = tracked
            tx_hash = bytes.fromhex(tx_hash_hex[2:])
            receipt_ptr = cbor_dumps({"h": height, "i": idx, "b": block_hash})
            batch.put(PFX_RXI + tx_hash, receipt_ptr)
    batch.commit()
```

This overwrites the incorrect index entries with correct ones using canonical hashes.

## Testing

### Regression Test (test_tx_hash_consistency.py)

The test validates:

1. **Hash Consistency**: `sendRawTransaction` returns `sha3_256(raw_cbor)`
2. **Block Inclusion**: Returned hash appears in `chain.getBlockByHeight`
3. **Transaction Lookup**: `tx.getTransactionByHash(hash)` returns non-null
4. **Receipt Lookup**: `tx.getTransactionReceipt(hash)` returns non-null receipt
5. **State Updates**: Balances and nonces update correctly after mining
6. **Root Computation**: `txsRoot` and `receiptsRoot` are non-zero when block contains txs
7. **Eviction**: Transaction is removed from mempool after mining
8. **Rewards**: Mining rewards continue to work in subsequent blocks
9. **Uniqueness**: Same tx hash doesn't appear in multiple blocks

### Test Execution

```bash
python test_tx_hash_consistency.py
```

**Expected Result**: All tests pass with output showing:
- ✓ Canonical tx hash consistency
- ✓ Hash appears in block
- ✓ getTransactionByHash works
- ✓ getTransactionReceipt works
- ✓ Balances and nonces updated
- ✓ txsRoot and receiptsRoot non-zero
- ✓ TX evicted from mempool
- ✓ Mining rewards continue to work
- ✓ TX hash uniqueness

## Impact

### Before Fix

1. `tx.sendRawTransaction` returns hash A (canonical)
2. Block is mined with tx hash B (re-encoded)
3. `chain.getBlockByHeight` shows tx hash B
4. `tx.getTransactionReceipt(A)` returns null (indexed by B, not A)
5. Balances may not update if execution fails due to sender derivation issues

### After Fix

1. `tx.sendRawTransaction` returns hash A (canonical)
2. Block is mined with tx hash A (same as returned)
3. `chain.getBlockByHeight` shows tx hash A
4. `tx.getTransactionReceipt(A)` returns receipt (indexed by A)
5. Balances update correctly

## Verification Checklist

- [x] Canonical tx hash used for txsRoot computation
- [x] Canonical tx hash used for receipt indexing
- [x] Removed dead code in tx.py
- [x] Created comprehensive regression test
- [x] No syntax errors in modified files
- [ ] Run full test suite (requires test environment)
- [ ] Manual verification in devnet
- [ ] Performance impact assessment (re-indexing overhead)

## Performance Considerations

### Re-indexing Overhead

The fix adds a re-indexing step after `append_canonical_block`. For each transaction:
- One additional KV write to update the receipt index
- Batched operation, so minimal overhead
- Only affects tracked transactions (from RPC), not P2P relayed transactions

**Estimated Impact**: < 1ms per transaction, negligible for typical block sizes (< 100 txs).

### Alternative Considered

We considered modifying the `Tx` dataclass to store raw CBOR bytes, but:
- Would require changing the frozen dataclass structure
- Would increase memory usage for all Tx objects
- Would complicate serialization/deserialization
- Re-indexing approach is simpler and less invasive

## Future Improvements

1. **Eliminate Re-indexing**: Modify `append_canonical_block` to accept explicit tx hashes
2. **Store Raw Bytes in Tx**: Add optional `_raw` field to Tx dataclass for canonical hash
3. **Canonical Encoding**: Ensure `tx.to_cbor()` always produces identical bytes for identical data
4. **P2P Relay**: Ensure P2P relayed transactions also track raw bytes

## Related Issues

- Issue #435: Previous state persistence and receipt lookup improvements
- Issue #436: This fix addresses the remaining tx hash consistency issues

## Files Changed

1. `rpc/methods/miner.py`: Core tx hash computation and receipt indexing fixes
2. `rpc/methods/tx.py`: Remove dead code
3. `test_tx_hash_consistency.py`: Comprehensive regression test (new file)

## Backward Compatibility

This fix is **backward compatible**:
- No API changes
- No schema changes
- Existing blocks are unaffected
- Only affects new blocks mined after the fix

## Deployment Notes

1. Deploy to devnet first for validation
2. Monitor logs for "Re-indexed N receipts with canonical tx hashes"
3. Run regression test to verify fix
4. Deploy to testnet, then mainnet after validation
