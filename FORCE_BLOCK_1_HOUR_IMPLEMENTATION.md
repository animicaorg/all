# Force Block When Previous Block is Older Than 1 Hour

## Summary

This patch adds logic to force the creation of a new block when the previous block is older than 1 hour (3600 seconds), ensuring the blockchain progresses even when no miners are active for extended periods.

## Implementation

### Changes Made

1. **rpc/methods/miner.py** (`_mine_once` function):
   - Added constant `_FORCED_BLOCK_MIN_THETA_MICRO = 100_000` (matches mining adjustment minimum)
   - Added check for `force_block_due_to_time` when parent block timestamp exceeds `max_block_time_s`
   - When triggered, sets mining difficulty (theta) to minimum (100,000 µ-nats ≈ 0.1 nats)
   - Improved error handling with fallback to state-based minimum theta if replacement fails
   - Logs warning message for visibility

2. **test_force_block_1_hour.py**:
   - Tests use deterministic timestamps (fixed values) to avoid race conditions
   - Added edge case test for exactly at threshold (3600s)
   - Validates forcing triggers correctly at 3601s but not at 3600s

### Configuration

Uses the existing `max_block_time_s` parameter from `spec/params.yaml`:

```yaml
issuance:
  max_block_time_s: 3600  # 3600 seconds = 1 hour
```

Environment variable override: `ANIMICA_MAX_BLOCK_TIME_S`

### Backwards Compatibility

✅ **Fully backwards compatible**:
- Default behavior unchanged (max_block_time_s is already set to 3600 in config)
- Can be disabled by setting `ANIMICA_MAX_BLOCK_TIME_S=0` or negative value
- Existing `max_block_time_s` parameter in consensus/difficulty.py already reduces difficulty on timeout
- This change adds **explicit forcing** with minimum theta to complement the existing mechanism

## Code Review Improvements

Addressed feedback from initial code review:

1. ✅ **Better error handling**: Added fallback to state-based theta if primary fails
2. ✅ **Removed unused import**: Removed `RetargetParams` import
3. ✅ **Constant for minimum theta**: Added `_FORCED_BLOCK_MIN_THETA_MICRO` constant
4. ✅ **Deterministic tests**: Tests now use fixed timestamps instead of `time.time()`
5. ✅ **Edge case coverage**: Added test for exactly at threshold

## Testing

All tests pass ✅
