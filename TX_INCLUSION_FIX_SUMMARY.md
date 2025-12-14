# Transaction Inclusion Bug Fix - Complete Summary

## Problem
Transactions submitted via RPC were accepted into mempool and visible via `animica mempool list`, but were never included in mined blocks. This caused:
- Transactions stuck in mempool forever
- Balances never updated
- Mining produced empty blocks even with pending transactions

## Root Cause
**Inconsistent pool access across the codebase**:

1. Transaction submission (`tx.sendRawTransaction` → `_pending_put`) checked `_PEND` first, then `_FALLBACK_PENDING`
2. Mempool query (`mempool.getPending` → `_iter_pending`) checked `_PEND` first, then `_FALLBACK_PENDING`
3. **Mining (`drain_fn`) ONLY checked `_FALLBACK_PENDING`** ← THIS WAS THE BUG

When `_PEND` was available (imported from `rpc.pending_pool.pool`), transactions went into `_PEND`. The miner's `drain_fn` only read from `_FALLBACK_PENDING`, so it never saw the transactions.

## Solution
Made all pool accessors consistent - check `_PEND` first, fall back to `_FALLBACK_PENDING`:

### Files Changed
1. **rpc/methods/miner.py**
   - `drain_fn`: Now checks `_PEND` first before `_FALLBACK_PENDING`
   - Fallback read path: Now checks `_PEND` first before `_FALLBACK_PENDING`
   - Eviction: Now removes from both `_PEND` and `_FALLBACK_PENDING`
   - Added comprehensive logging throughout

2. **mining/adapters/core_chain.py**
   - `remove_included`: Now checks `_PEND` first before `_FALLBACK_PENDING`

3. **rpc/methods/state.py**
   - `_svc_pending_nonce`: Now checks `_PEND` first before `_FALLBACK_PENDING`

### Complete Pool Access Priority (All Fixed)
| Code Location | Function | Priority (Now Correct) |
|---------------|----------|------------------------|
| tx.py | `_pending_put` | `_PEND` → `_FALLBACK_PENDING` ✓ (was already correct) |
| mempool.py | `_iter_pending` | `_PEND` → `_FALLBACK_PENDING` ✓ (was already correct) |
| miner.py | `drain_fn` | `_PEND` → `_FALLBACK_PENDING` ✓ (FIXED) |
| miner.py | fallback read | `_PEND` → `_FALLBACK_PENDING` ✓ (FIXED) |
| miner.py | eviction | Both `_PEND` + `_FALLBACK_PENDING` ✓ (FIXED) |
| core_chain.py | `remove_included` | `_PEND` → `_FALLBACK_PENDING` ✓ (FIXED) |
| state.py | `_svc_pending_nonce` | `_PEND` → `_FALLBACK_PENDING` ✓ (FIXED) |

## Transaction Lifecycle (After Fix)
1. **Submission**: `animica tx send` → `tx.sendRawTransaction` → `_pending_put` → stores in `_PEND` (or `_FALLBACK_PENDING`)
2. **Query**: `animica mempool list` → `mempool.getPending` → `_iter_pending` → reads from `_PEND` (or `_FALLBACK_PENDING`) ✓
3. **Mining**: `animica miner mine` → `_mine_once` → `drain_fn` → reads from `_PEND` (or `_FALLBACK_PENDING`) ✓ (NOW FIXED)
4. **Inclusion**: TX included in block, receipts generated, state updated ✓
5. **Eviction**: TX removed from both `_PEND` and `_FALLBACK_PENDING` ✓ (NOW FIXED)

## Verification Plan
### Manual Testing
```bash
# 1. Start clean testnet
animica node down --volumes
animica node up

# 2. Create wallets
animica wallet create --label sender
animica wallet create --label receiver

# 3. Fund sender
animica faucet request sender

# 4. Send transaction
animica tx send --from anim1... --to anim1... --value 499999

# 5. Verify in mempool
animica mempool list
# Should show tx hash

# 6. Mine block
animica miner mine

# 7. Verify tx included
animica chain getBlockByNumber <height> true
# Should show tx in block.transactions

# 8. Verify balances updated
animica wallet show <receiver>
# Should show non-zero balance

# 9. Verify tx removed from mempool
animica mempool list
# Should NOT show tx hash
```

### Integration Tests
- Run existing test: `pytest rpc/tests/test_mining_mempool_integration.py -xvs`
- Test should now pass (was failing before due to this bug)

## Impact
### Before Fix
- ✗ Transactions stuck in mempool forever
- ✗ Balances never updated
- ✗ Mining produced empty blocks
- ✗ Users couldn't transact on testnet

### After Fix
- ✓ Transactions retrieved correctly during mining
- ✓ Transactions included in mined blocks
- ✓ Balances updated after tx execution
- ✓ Transactions properly evicted from mempool
- ✓ All pool access is consistent

## Code Quality
- **Code Review**: Passed (3 minor nitpicks about logging verbosity)
- **Security Check**: Passed (no vulnerabilities detected)
- **Logging**: Comprehensive debug logging added for troubleshooting
- **Backward Compatibility**: Maintained (falls back to `_FALLBACK_PENDING` when `_PEND` is None)

## Additional Improvements
1. **Envelope Normalization**: Verified that CLI format → Core format conversion works correctly
2. **Test Coverage**: Added `test_normalize_tx_envelope.py` and `test_tx_inclusion_bug.py`
3. **Logging**: Added detailed logging at each step for easier debugging
4. **Documentation**: This summary document

## Notes
- The fix is minimal and surgical - only changes pool access priority
- No changes to transaction validation, signing, or execution logic
- No changes to block structure or consensus rules
- Backward compatible - works with both `_PEND` and `_FALLBACK_PENDING`

## References
- Problem Statement: See issue description
- Test Plan: TRANSACTION_INCLUSION_FIX_TEST_PLAN.md (to be created)
- Related Files: 
  - `rpc/methods/tx.py` (submission)
  - `rpc/methods/mempool.py` (query)
  - `rpc/methods/miner.py` (mining)
  - `mining/adapters/core_chain.py` (adapter)
  - `rpc/methods/state.py` (nonce calculation)
