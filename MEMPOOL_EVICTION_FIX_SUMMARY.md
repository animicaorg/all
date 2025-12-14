# Mempool Transaction Eviction Fix

## Problem

Transactions sent via `animica tx send` remained in the mempool after being mined via `animica miner mine-blocks`, causing them to be re-mined in subsequent blocks.

### Root Cause

The mining code in `rpc/methods/miner.py` (`_mine_once()` function) had the following behavior:

1. ✅ Fetched pending transactions from adapter (`adapter.get_mempool_snapshot()`)
2. ✅ Included transactions in mined blocks
3. ✅ Executed transactions to update state (balances, nonces)
4. ✅ Evicted transactions from fallback cache (`_FALLBACK_PENDING`)
5. ❌ **Did NOT evict transactions from the actual mempool pool**

**Result**: Transactions remained in mempool and were re-mined repeatedly.

## Solution

Added mempool eviction to the mining pipeline by updating both the adapter and the mining code.

### Changes

#### 1. mining/adapters/core_chain.py

Added `remove_included(hashes)` method to `CoreChainAdapter`:

```python
def remove_included(self, hashes: Sequence[bytes]) -> None:
    """
    Evict transactions that were included in a mined block from the mempool.
    """
    # Try multiple strategies to access the mempool pool:
    # 1. Global registry/accessor
    # 2. Drain method's bound instance
    # 3. Module-level pool variable
    
    if pool is not None and hasattr(pool, "remove_included"):
        pool.remove_included(hashes)
```

**Key Features**:
- Multiple strategies to access pool instance
- Graceful degradation if pool not accessible
- Specific exception handling (ImportError, AttributeError, etc.)
- Comprehensive logging for debugging

#### 2. rpc/methods/miner.py

Updated `_mine_once()` to evict from BOTH sources:

```python
if accepted and included_hashes:
    # 1. Evict from adapter mempool (NEW)
    hashes_bytes = [_hex_to_bytes(h) for h in included_hashes]
    if hasattr(adapter, "remove_included"):
        adapter.remove_included(hashes_bytes)
    
    # 2. Evict from fallback cache (EXISTING)
    for h in included_hashes:
        cache.pop(h, None)
```

**Code Quality Improvements**:
- Added `_hex_to_bytes()` helper function for hex conversion
- Refactored `_parse_nonce()` to use helper (DRY principle)
- Specific exception handling throughout
- Detailed logging for eviction operations

#### 3. tests/integration/test_mining_mempool_consumption.py

New integration test to verify:
- Mining works correctly
- Mempool doesn't accumulate stuck transactions
- Can be extended for full E2E testing

## Testing

### Verification Completed

- ✅ All files compile without syntax errors
- ✅ All imports present and correct
- ✅ Hex conversion helper verified with test cases
- ✅ _parse_nonce refactored and tested
- ✅ Code review clean (no issues)
- ✅ CLI positional address support verified

### Manual Testing (To Be Performed)

```bash
# 1. Start node
animica node up

# 2. Mine initial blocks to fund address
animica miner mine-blocks --count 5 --address premine

# 3. Send a transaction
animica tx send --from premine --to receiver --value 1

# 4. Check mempool (should show 1 pending tx)
animica mempool list

# 5. Mine a block
animica miner mine-blocks --count 1 --address premine

# 6. Check mempool again (should be empty)
animica mempool list

# 7. Verify receiver balance
animica wallet show receiver
```

**Expected Results**:
- Transaction appears in mempool after step 3
- Transaction is absent from mempool after step 5
- Receiver balance shows +1 ANM (minus fees)
- Sender nonce is incremented
- Transaction is not re-mined in subsequent blocks

## Backward Compatibility

- ✅ Maintains fallback cache eviction for older code paths
- ✅ Gracefully degrades if mempool pool is not accessible
- ✅ No breaking changes to existing APIs
- ✅ CLI positional address support already present

## Files Modified

1. `mining/adapters/core_chain.py` - Added `remove_included()` method
2. `rpc/methods/miner.py` - Updated `_mine_once()` + added helpers
3. `tests/integration/test_mining_mempool_consumption.py` - New test

## Implementation Notes

### Hex Conversion

The implementation handles both hex formats:
- `"0x6e23d5d2..."` (with prefix)
- `"6e23d5d2..."` (without prefix)

The `_hex_to_bytes()` helper automatically strips the prefix if present.

### Pool Access Strategies

The adapter tries multiple strategies to find the pool:

1. **Global registry**: `mempool.adapters.get_pool()` or `mempool.adapters._POOL`
2. **Drain method**: Extract pool from `miner_feed._drain.__self__`
3. **Module variable**: `sys.modules["mempool"]._POOL`

This approach ensures compatibility with different mempool implementations.

### Exception Handling

All exception handling uses specific exception types:
- `ImportError`, `AttributeError` - Import/access failures
- `TypeError`, `ValueError`, `KeyError` - Operation failures
- `urllib.error.URLError`, `ConnectionError`, `TimeoutError` - Network failures

No bare `Exception` catches remain in the codebase.

## Known Limitations

1. **Pool Access**: If none of the pool access strategies succeed, eviction is skipped
   - Transactions will still be included in blocks
   - May be re-mined in subsequent blocks
   - Logged as warning for debugging

2. **Integration Test**: Current test is basic
   - Verifies mining works
   - Can be extended with full tx send/mine/verify flow
   - Requires running node for full E2E testing

## Future Enhancements

1. **Global Pool Registry**: Add explicit pool registration mechanism
   - `mempool.register_global_pool(pool)` on startup
   - `mempool.get_global_pool()` for access
   - Eliminates need for multiple strategies

2. **Enhanced Integration Test**: Add full E2E scenario
   - Fund sender from mining
   - Send transaction via RPC
   - Mine block
   - Verify state updates and mempool eviction

3. **Metrics**: Add prometheus metrics for eviction
   - `mempool_evictions_total` counter
   - `mempool_eviction_failures_total` counter
   - `mempool_size` gauge

## References

- Issue: Devnet mining via `animica miner mine-blocks` not consuming mempool transactions
- PR: copilot/fix-mempool-transactions-handling
- Files: See "Files Modified" section above
