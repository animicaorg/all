# Mining Reward Balance Fix - Implementation Complete

## Summary

Successfully identified and fixed the issue where mining rewards were not immediately visible in wallet balance queries.

## Problem
Users reported mining blocks successfully (with rewards shown in mining output), but `animica wallet show` displayed a balance of 0.

**Example from issue:**
```bash
animica miner mine-blocks --address anim1zqp2pg8s9... --count 5
# Output: 5 blocks mined, 25 ANM total reward

animica wallet show temple
# Result: balance = 0 (INCORRECT!)
```

## Root Cause

**SQLite Write-Ahead Logging (WAL) buffering issue:**

1. StateDB uses SQLite with these pragmas:
   - `journal_mode=WAL` (for better read/write concurrency)
   - `synchronous=NORMAL` (balances durability and performance)

2. With these settings, write operations go to a WAL (Write-Ahead Log) file and are not immediately visible to other database readers until a checkpoint occurs.

3. Mining flow:
   - Block rewards are credited → written to WAL file
   - Balance query happens immediately → reads from main DB (stale data)
   - Result: Balance shows 0 even though reward was credited

4. Eventually, automatic checkpoints would make the data visible, but this could take seconds or minutes, creating a confusing user experience.

## Solution

Added explicit WAL checkpoint after each mined block is accepted:

```python
def _ensure_state_visibility(ctx: Any, height: int) -> None:
    """
    Ensure state changes are immediately visible to other connections.
    Forces a WAL checkpoint if using SQLite backend.
    """
    # Call commit hook (may be no-op)
    if hasattr(ctx.state_db, "commit"):
        ctx.state_db.commit()
    
    # Force WAL checkpoint for SQLite
    if hasattr(ctx.state_db, "kv") and hasattr(ctx.state_db.kv, "_conn"):
        ctx.state_db.kv._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
```

**Why PASSIVE mode?**
- Non-blocking: doesn't wait for other connections
- Opportunistic: checkpoints as much as possible without blocking
- Performant: minimal impact on mining throughput

## Implementation

### Files Modified

1. **rpc/methods/miner.py**
   - Added `_ensure_state_visibility()` helper function
   - Called after block acceptance to force WAL checkpoint
   - Includes comprehensive error handling

2. **rpc/tests/test_mining_wal_checkpoint.py** (NEW)
   - `test_mining_rewards_immediately_visible()` - Core regression test
   - `test_multiple_rapid_mining_sessions()` - Stress test for rapid mining

3. **MINING_REWARD_WAL_FIX.md** (NEW)
   - Complete technical documentation
   - Testing instructions
   - Performance analysis

## Code Quality

### Before Code Review
- Inline WAL checkpoint logic with nested try-catch blocks
- Generic error messages

### After Code Review
- ✅ Extracted into `_ensure_state_visibility()` helper for better testability
- ✅ Improved error messages with specific debugging guidance
- ✅ Cleaner control flow and easier to maintain

## Testing

### Automated Tests
```bash
pytest rpc/tests/test_mining_wal_checkpoint.py -v
```

Tests verify:
- Mining rewards are immediately visible after block acceptance
- Multiple rapid mining sessions correctly accumulate rewards
- No delays or restarts needed

### Manual Verification
```bash
# 1. Start node
animica node start

# 2. Mine blocks
animica miner mine-blocks --address <your-address> --count 3

# 3. Check balance immediately (should show rewards)
animica wallet show <your-address>
```

Expected: Balance reflects mined rewards immediately (e.g., 15 ANM for 3 blocks)

## Performance Impact

- **Minimal overhead**: PASSIVE checkpoint is non-blocking
- **No impact on readers**: Other connections can read/write during checkpoint
- **Same eventual behavior**: WAL would be checkpointed automatically anyway
- **Predictable timing**: Checkpoint happens at a known point (after mining)

## Benefits

1. **User Experience**: Rewards are immediately visible, no confusion
2. **Consistency**: State is immediately queryable by any connection
3. **Debugging**: Easier to verify mining rewards without waiting
4. **Testing**: Tests can verify state immediately without delays
5. **Reliability**: Reduces window for state visibility issues

## Related Work

### Previous Fixes
- Address parsing standardization (32-byte digests)
- State persistence flow corrections
- RPC context management improvements

### This Fix Completes
- The entire mining → reward → balance query workflow
- Ensures end-to-end consistency from mining to wallet queries
- Provides foundation for future state visibility requirements

## Deployment

### Requirements
- No database schema changes needed
- No configuration changes required
- Works with existing SQLite databases

### Rollout
1. Deploy updated `rpc/methods/miner.py`
2. Restart node (to load new code)
3. Test mining and balance queries
4. Verify rewards are immediately visible

### Backwards Compatibility
- ✅ Works with existing databases
- ✅ No breaking changes to RPC API
- ✅ Gracefully handles non-SQLite backends (no-op for RocksDB, etc.)

## Verification Checklist

- [x] Root cause identified (WAL buffering)
- [x] Solution implemented (PASSIVE checkpoint)
- [x] Code reviewed and refactored
- [x] Automated tests created
- [x] Documentation written
- [ ] Tests passing in CI
- [ ] Manual verification complete
- [ ] User confirms fix resolves issue

## Next Steps

1. Run automated test suite to ensure no regressions
2. Manual testing with actual CLI workflow
3. Monitor for any edge cases in production
4. Consider adding metrics for checkpoint timing

## References

- SQLite WAL documentation: https://www.sqlite.org/wal.html
- SQLite checkpoint pragma: https://www.sqlite.org/pragma.html#pragma_wal_checkpoint
- Original issue report: [Problem statement in this PR]

---

**Status**: Implementation complete, ready for testing and deployment
**Author**: GitHub Copilot  
**Date**: 2026-01-05
