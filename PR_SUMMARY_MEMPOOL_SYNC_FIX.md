# Mempool Sync Fix - Implementation Complete ✓

## Summary

Successfully fixed the issue where the mempool appears empty on a node even though peers report knowing about specific transaction IDs.

## Problem Description

**Original Issue**:
```
Mempool is empty (no pending transactions)

But peers report:
  peer=0x6c299a12c1 known_txids=1 sample=[0x3c255d68f3942d04c2af34a4a572565fdf61ee9650c21c6aab51350d418add92]
```

**Root Cause**: The `mempool_sync_loop()` was sending mempool sync requests but never calling `request_missing_known()` to fetch transactions that peers had advertised. If TX_GET responses were lost, transaction IDs would sit in `known_txids` forever.

## Solution

Added automatic periodic fetching of missing transactions in the mempool sync loop:

```python
# In mempool_sync_loop() - p2p/txrelay.py
if now - last_missing_fetch >= self.mempool_sync_interval_s:
    last_missing_fetch = now
    requested = await self.request_missing_known(limit=128, trigger="mempool_sync_loop")
    if requested > 0:
        log.info("TX_MISSING_FETCH", extra={"requested": requested, ...})
```

## Implementation Details

### Core Changes

**File**: `p2p/txrelay.py`
- Added `last_missing_fetch` timestamp tracking
- Calls `request_missing_known()` every 15 seconds (configurable)
- Added `trigger` parameter to `request_missing_known()` for logging context
- Requests up to 128 missing transactions per cycle

### Test Coverage

**File**: `p2p/tests/test_mempool_sync_missing_fetch.py`

Two comprehensive tests:
1. **test_mempool_sync_loop_requests_missing_known**: 
   - Verifies periodic automatic fetching
   - Confirms transactions are requested from peers
   
2. **test_request_missing_known_fetches_peer_txids**:
   - Tests transaction sampling and request logic
   - Verifies correct TX_GET messages are sent

### Verification

**File**: `verify_mempool_sync_fix.py`

Standalone script demonstrating:
- Peer reporting transactions
- Automatic fetch after 2-3 seconds
- TX_GET messages sent correctly

Run with: `python verify_mempool_sync_fix.py`

### Documentation

**File**: `MEMPOOL_SYNC_MISSING_FETCH_FIX.md`

Complete documentation including:
- Problem statement and root cause analysis
- Detailed solution explanation
- Performance impact assessment
- Configuration options
- Future improvement ideas

## Test Results

All tests passing ✓

```
p2p/tests/test_mempool_sync_missing_fetch.py::test_mempool_sync_loop_requests_missing_known PASSED
p2p/tests/test_mempool_sync_missing_fetch.py::test_request_missing_known_fetches_peer_txids PASSED
p2p/tests/test_txrelay_service_v2.py::test_txid_must_match_bytes_hash PASSED
p2p/tests/test_txrelay_service_v2.py::test_inflight_timeout_retries PASSED
p2p/tests/test_txrelay_timeout_recovery.py::test_timeout_clears_known_txids_for_retry PASSED
p2p/tests/test_txrelay_timeout_recovery.py::test_notfound_clears_known_txids PASSED
```

**Total**: 6/6 tests passed, 0 failures

## Quality Checks

All checks passing ✓

- ✅ New tests pass
- ✅ Existing tests pass (no regressions)
- ✅ Code linting (ruff) passes
- ✅ Verification script succeeds
- ✅ Code review feedback addressed
- ✅ Security check (CodeQL) passed
- ✅ No breaking changes

## Performance Impact

**Minimal overhead**:
- Frequency: Every 15 seconds (configurable via `mempool_sync_interval_s`)
- Batch size: Up to 128 transactions per cycle
- Network: Only requests genuinely missing transactions
- CPU: Negligible (simple sampling and filtering)

**No production impact**:
- Backward compatible
- Default parameters are conservative
- Can be tuned via existing configuration

## How It Works

### Flow Diagram

```
1. Peer sends INV for transaction
   ↓
2. TxID added to peer's known_txids set
   ↓
3. TX_GET sent to fetch transaction
   ↓
4a. Success: Transaction added to mempool ✓
   OR
4b. Timeout/Lost: Transaction stays in known_txids
   ↓
5. Mempool sync loop runs (every 15s)
   ↓
6. request_missing_known() samples from known_txids
   ↓
7. For each sampled txid:
   - Check if in flight → skip
   - Check if in mempool → skip
   - Check if in blockchain → skip
   - Check if recently rejected → skip
   - Otherwise → send TX_GET
   ↓
8. Transaction eventually fetched and added to mempool ✓
```

### Key Features

1. **Automatic Recovery**: No manual intervention needed
2. **Eventual Consistency**: Guarantees transactions are eventually fetched
3. **Smart Filtering**: Only requests truly missing transactions
4. **Rate Limiting**: Respects existing rate limits
5. **Logging**: Clear TX_MISSING_FETCH events for monitoring

## Files Changed

```
 MEMPOOL_SYNC_MISSING_FETCH_FIX.md            | 153 ++++++++++++++++++++
 p2p/tests/test_mempool_sync_missing_fetch.py | 177 ++++++++++++++++++++++
 p2p/txrelay.py                               |  18 ++-
 verify_mempool_sync_fix.py                   | 135 +++++++++++++++++
 4 files changed, 481 insertions(+), 2 deletions(-)
```

## Configuration

The fetch frequency can be adjusted:

```python
service = TxRelayService(
    mempool_sync_interval_s=30.0,  # Fetch every 30 seconds instead of 15
    # ... other params
)
```

Or via environment variable (if exposed by higher-level config).

## Monitoring

Look for these log events:

- **TX_MISSING_FETCH**: Indicates automatic fetch was triggered
- **TX_GET_SENT** with `trigger="mempool_sync_loop"`: Shows transactions being requested

Example:
```json
{
  "event": "TX_MISSING_FETCH",
  "requested": 5,
  "trigger": "mempool_sync_loop"
}
```

## Future Improvements

Potential enhancements (not in this PR):
1. Adaptive fetch frequency based on network conditions
2. Priority-based sampling (fetch high-fee transactions first)
3. Metrics for fetch success rate
4. Exponential backoff for repeatedly failing transactions

## Conclusion

This fix ensures robust transaction propagation even in unreliable network conditions. The mempool will now automatically fetch transactions that peers have advertised but haven't been delivered yet.

**Impact**: Eliminates the issue where `animica mempool list` shows an empty mempool despite peers having transactions.

**Status**: ✅ Ready for merge

---

## For Reviewers

### Key Points to Review

1. **Core logic**: Lines 802-836 in `p2p/txrelay.py`
2. **New tests**: `p2p/tests/test_mempool_sync_missing_fetch.py`
3. **Performance**: Check the 15-second interval is acceptable
4. **Logging**: Verify `TX_MISSING_FETCH` provides useful information

### Testing Instructions

1. Run new tests:
   ```bash
   pytest p2p/tests/test_mempool_sync_missing_fetch.py -v
   ```

2. Run verification script:
   ```bash
   python verify_mempool_sync_fix.py
   ```

3. Check no regressions:
   ```bash
   pytest p2p/tests/test_txrelay*.py -v
   ```

### Questions?

See `MEMPOOL_SYNC_MISSING_FETCH_FIX.md` for complete documentation.
