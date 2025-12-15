# Transaction Receipt Persistence Fix

## Problem Statement

Issue #433 reported that transactions would enter the mempool but never confirm. Investigation revealed the core problem was **not mempool eviction** (which was working correctly), but rather **missing receipt persistence and RPC lookup**.

Specifically:
1. Blocks were being mined with transactions ✓
2. Transactions were being evicted from mempool ✓  
3. **But receipts were not indexed for lookup** ✗
4. **RPC methods tx.getReceipt / tx.getTransactionReceipt were stubs** ✗

This made it impossible for users to verify transaction status, leading to the perception that transactions were "lost".

## Root Cause

The `BlockDB` class in `core/db/block_db.py` persisted blocks with their receipts, but did not create an index mapping `tx_hash → receipt`. This meant:

- Receipts existed in blocks but were not queryable by tx_hash
- RPC methods couldn't efficiently look up receipts
- Applications had no way to check transaction status

## Solution

### 1. Receipt Indexing in block_db

**File**: `core/db/block_db.py`

Added receipt indexing to `append_canonical_block`:

```python
# New key prefix for receipt index
PFX_RXI = b"\x22"  # tx_hash → (height, index, block_hash)

def k_rxi(tx_hash: bytes) -> bytes:
    return PFX_RXI + tx_hash

def append_canonical_block(self, height: int, block: Block) -> bytes:
    """
    Atomically store a block, mark it canonical, and index receipts by tx_hash.
    """
    hh = header_hash(block.header)
    with self.kv.batch() as b:
        self.put_block(block, batch=b)
        self.set_canonical(height, hh, batch=b)
        
        # Index transactions and receipts by tx_hash
        if block.txs:
            for idx, tx in enumerate(block.txs):
                tx_hash = tx.hash() if hasattr(tx, 'hash') and callable(tx.hash) else sha3_256(_to_cbor(tx))
                receipt_ptr = cbor_dumps({"h": height, "i": idx, "b": hh})
                b.put(k_rxi(tx_hash), receipt_ptr)
        
        # Update head
        cur = self.get_head()
        if cur is None or height >= cur[0]:
            self.set_head(height, hh, batch=b)
        b.commit()
    return hh
```

Added receipt lookup method:

```python
def get_receipt_by_tx_hash(self, tx_hash: bytes) -> Optional[Tuple[int, int, bytes, Any]]:
    """
    Look up a receipt by transaction hash.
    
    Returns:
        Tuple of (height, tx_index, block_hash, receipt_obj) if found, None otherwise.
    """
    ptr_data = self.kv.get(k_rxi(tx_hash))
    if ptr_data is None:
        return None
    
    ptr = cbor_loads(ptr_data)
    height = int(ptr["h"])
    idx = int(ptr["i"])
    block_hash = bytes(ptr["b"])
    
    block = self.get_block_by_hash(block_hash)
    if block is None or block.receipts is None:
        return None
    
    if idx >= len(block.receipts):
        return None
    
    receipt = block.receipts[idx]
    return (height, idx, block_hash, receipt)
```

### 2. RPC Receipt Methods

**File**: `rpc/methods/receipt.py`

Updated `tx_get_transaction_receipt` to use the new block_db method:

```python
@method(
    "tx.getTransactionReceipt",
    desc="Return the transaction receipt for a mined transaction hash.",
    aliases=("tx_getTransactionReceipt",),
)
def tx_get_transaction_receipt(txHash: HexStr) -> t.Optional[dict]:
    tx_hash_hex, tx_hash_b = _parse_tx_hash(txHash)
    
    if _pending_contains(tx_hash_hex):
        return None  # Pending transactions have no receipt yet
    
    loc = _lookup_receipt_loc(tx_hash_b)
    if loc is None:
        # Try the new get_receipt_by_tx_hash method
        bdb = getattr(deps, "block_db", None)
        if bdb is not None and hasattr(bdb, "get_receipt_by_tx_hash"):
            result = bdb.get_receipt_by_tx_hash(tx_hash_b)
            if result is not None:
                height, idx, block_hash, receipt = result
                loc = _ReceiptLoc(height=height, index=idx, block_hash=block_hash)
                blk = bdb.get_block_by_hash(block_hash)
                return _normalize_receipt(tx_hash_hex, loc, blk, receipt)
        return None
    
    pair = _fetch_block_and_receipt(loc, tx_hash_b)
    if pair is None:
        return None
    
    blk, rec = pair
    return _normalize_receipt(tx_hash_hex, loc, blk, rec)
```

**File**: `rpc/methods/tx.py`

Simplified `tx_get_transaction_receipt` to delegate to the receipt.py implementation:

```python
@method(
    "tx.getTransactionReceipt",
    desc="Get transaction receipt by hash",
    aliases=("tx_getTransactionReceipt", "tx.getReceipt", "tx_getReceipt"),
)
def tx_get_transaction_receipt(txHash: str) -> t.Optional[dict]:
    """Retrieve the receipt for a transaction by its hash."""
    from rpc.methods.receipt import tx_get_transaction_receipt as _receipt_impl
    return _receipt_impl(txHash)
```

### 3. Testing

Created comprehensive tests to validate the fix:

**File**: `test_receipt_flow_simple.py`

End-to-end test validating:
1. Block with receipts is persisted
2. Receipt is indexed by tx_hash  
3. Receipt is retrievable via `get_receipt_by_tx_hash`
4. Receipt data (status, gas_used) is correct
5. Non-existent tx returns None

**File**: `rpc/tests/test_tx_receipt_persistence.py`

RPC-level integration tests:
1. Pending transactions return null receipt
2. Unknown transactions return null receipt
3. Receipts become available after mining
4. Receipt structure matches spec

All tests pass ✓

## Receipt Structure

The receipt returned by RPC methods contains:

```json
{
  "transactionHash": "0x...",
  "blockHash": "0x...",
  "blockNumber": 123,
  "transactionIndex": 0,
  "status": 1,  // 1 = SUCCESS, 0 = REVERT, 2 = OOG
  "gasUsed": 21000,
  "logs": [],
  "logsBloom": "0x..."
}
```

## Impact

This fix enables:

1. **Status Verification**: Applications can now check if transactions succeeded or reverted
2. **Gas Tracking**: Gas consumption is queryable per transaction
3. **Event Logs**: Transaction event logs are accessible via receipts
4. **User Experience**: Users can verify their transactions completed successfully
5. **Debugging**: Developers can investigate failed transactions

## Backward Compatibility

- ✅ No breaking changes to existing APIs
- ✅ Block storage format unchanged (receipts were already stored)
- ✅ Only adds new index for efficient lookups
- ✅ RPC methods now work as documented instead of returning stub responses

## Performance

- **Index size**: O(n) where n = total transactions across all blocks
- **Lookup time**: O(1) - direct key-value lookup by tx_hash
- **Write overhead**: Minimal - one additional KV write per transaction during block append
- **Batch operation**: Receipt indexing is part of the atomic block append transaction

## Future Enhancements

Potential improvements (not required for this fix):

1. Add receipt pruning for old blocks (if needed for storage management)
2. Add bloom filters for log searching
3. Add receipt root verification in light clients
4. Expose more receipt fields (contract address, cumulative gas, etc.)

## Verification

To verify the fix works:

```bash
# Run the simple test
python3 test_receipt_flow_simple.py

# Run RPC tests
pytest rpc/tests/test_tx_receipt_persistence.py -xvs

# Test with real transactions (requires full setup)
python3 test_tx_inclusion_bug.py
```

## Related Issues

- Fixes the "transactions never confirm" issue reported in #433
- Enables proper implementation of block explorers and wallet UIs
- Provides foundation for event subscription (future work)

## Files Changed

1. `core/db/block_db.py` - Added receipt indexing and lookup
2. `rpc/methods/receipt.py` - Implemented receipt retrieval
3. `rpc/methods/tx.py` - Delegated to receipt implementation
4. `rpc/tests/test_tx_receipt_persistence.py` - Added integration tests
5. `test_receipt_flow_simple.py` - Added end-to-end validation test
