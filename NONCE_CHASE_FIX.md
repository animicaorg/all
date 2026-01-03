# Nonce Chase Issue - Fix Implementation

## Issue Summary

Users reported the Animica CLI "chasing" the nonce by +1 when mempool returns `nonce_too_low`, causing repeated rejections (e.g., expected 64/got 63, then expected 65/got 64). The retry logic now deterministically submits the **correct** next nonce without overshooting.

## Root Causes

1. **`_next_retry_nonce` used stale expected nonce**: When the pending nonce advanced between the error and retry, the CLI would send a nonce that was still behind
2. **`_next_nonce` cache could be stale**: Cache wasn't invalidated on nonce mismatch, causing `cached+1` to be reused even when behind
3. **Result**: Repeated nonce_too_low errors and transactions not entering mempool

## Solution

### 1. Updated `_next_retry_nonce` Function

**Key Change**: Always fetch fresh pending nonce and use `max(expected, fresh_pending)`

```python
def _next_retry_nonce(rpc_url: str, addr: str, *, expected: int | None, got: int | None, verbose: bool = False) -> int:
    # Always fetch a fresh pending nonce to avoid stale values
    fresh_pending = _get_next_nonce(rpc_url, addr, verbose=verbose)
    
    if expected is not None:
        # Use the max of (expected, fresh_pending) to handle cases where
        # the mempool advanced between the error and the retry
        retry_nonce = max(int(expected), fresh_pending)
        return retry_nonce
    
    return fresh_pending
```

### 2. Cache Invalidation on Nonce Mismatch

Added cache invalidation in two locations where nonce mismatches are detected:

```python
if nonce is None and reason in {"nonce_too_low", "nonce_gap"} and attempt + 1 < max_attempts:
    # Invalidate cache on nonce mismatch to prevent stale cached+1 reuse
    cache_key = (rpc, from_addr)
    if cache_key in _NONCE_CACHE:
        del _NONCE_CACHE[cache_key]
    next_nonce_value = _next_retry_nonce(rpc, from_addr, expected=expected, got=got, verbose=verbose)
```

### 3. Cache Update on Success

Update cache after successful transaction submission:

```python
if tx_in_mempool:
    # Update cache with the successful nonce for future transactions
    cache_key = (rpc, from_addr)
    _NONCE_CACHE[cache_key] = attempt_nonce
```

## Test Coverage

### New Tests Added

1. **`test_send_retries_with_advancing_pending_nonce`**
   - Tests scenario where mempool advances between error and retry
   - Verifies CLI uses max(expected=64, fresh_pending=65) = 65
   - Result: Transaction accepted on retry with correct nonce

2. **`test_send_no_off_by_one_chase`**
   - Tests cache invalidation prevents stale cached+1 reuse
   - Verifies fresh nonce (64) is used instead of stale cached+1 (63)
   - Result: No retry needed, succeeds with correct nonce

### Regression Tests

All existing nonce-related tests continue to pass:
- `test_send_retries_on_nonce_too_low` (updated)
- `test_nonce_increment_produces_unique_txid`
- `test_nonce_chasing_scenario`
- `test_nonce_rapid_retry_loop`
- `test_nonce_toctou_fix.py` (12 tests)

**Total**: 17 tests passed ✅

## Behavior Comparison

### Before Fix
```
Attempt 1: nonce=63 → rejected (expected 64)
Retry:     Use expected=64 directly
Attempt 2: nonce=64 → rejected (expected 65, mempool advanced!)
Retry:     Use expected=65 directly  
Attempt 3: nonce=65 → accepted
Result:    3 attempts (chasing +1 each time)
```

### After Fix
```
Attempt 1: nonce=63 → rejected (expected 64)
Retry:     Fetch fresh=65, use max(64, 65)=65
Attempt 2: nonce=65 → accepted
Result:    2 attempts (no chasing!)
```

## Acceptance Criteria

✅ After a single `nonce_too_low`/`nonce_gap` rejection, the next attempt uses the correct nonce even if pending nonce advanced

✅ Transaction is accepted into mempool in tests (verified in `test_send_retries_with_advancing_pending_nonce`)

✅ Cache does not cause stale `cached+1` off-by-one retries after a mismatch (verified in `test_send_no_off_by_one_chase`)

✅ Tests cover the advancing-pending-nonce scenario and pass (2 new comprehensive tests)

## Files Modified

- `python/animica/cli/tx.py` - Core nonce retry logic (~45 lines changed)
- `python/animica/cli/tests/test_tx_send_nonce_retry.py` - Test coverage (~200 lines added)

## Backward Compatibility

- All existing tests pass
- No breaking changes to API
- No changes to external behavior (only fixes incorrect behavior)

## Future Considerations

1. Monitor nonce retry frequency in production
2. Consider adding metrics for nonce retry success/failure
3. Potential optimization if RPC calls become bottleneck
