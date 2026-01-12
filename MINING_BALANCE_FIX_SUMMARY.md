# Mining Balance Display Issue - Fix Summary

## Problem Statement
Miners reported seeing decreasing balance on higher heights despite never sending any ANM. This created confusion and concern that mining rewards were not being properly credited.

## Root Cause Analysis

### The Bug
The mining audit trail (`_MINING_AUDIT_TRAIL` in `rpc/methods/miner.py`) was recording `credited_reward` as the **total account balance** at the time of block acceptance, not the **incremental reward** for that specific block.

### Why This Caused Issues
1. When blocks get reorged or orphaned, the canonical chain changes
2. Later blocks query the current state (which reflects the canonical chain)
3. If earlier blocks were orphaned, the total balance in current state is lower
4. This made it appear that balance decreased at higher heights
5. Miners saw their "credited_reward" decrease despite mining new blocks

### Example of the Bug
```
Scenario: Miner mines 3 blocks with 5 ANM reward each

With the BUG (recording total balance):
  Height 1: credited_reward = 5 ANM    (total balance = 5 ANM)
  Height 2: credited_reward = 10 ANM   (total balance = 10 ANM)
  Height 3: credited_reward = 8 ANM    (total balance = 8 ANM after reorg!)
  
  ⚠️ Miners see: Balance DECREASED from 10 ANM to 8 ANM!
  ⚠️ But they never sent any ANM - this was a display bug
```

## The Fix

### Code Changes
**File:** `rpc/methods/miner.py`
**Line:** 3510

**Before:**
```python
credited_reward=final_balance,  # Note: this is total balance, not delta
```

**After:**
```python
credited_reward=reward_amount,  # Use reward_amount, not final_balance
```

### Why This Fixes It
1. `reward_amount` is the actual reward for THIS specific block (e.g., 5 ANM)
2. It's computed from consensus rules, not queried from state
3. It's stable and doesn't change due to reorgs
4. Each block correctly shows its incremental reward

### Result
```
With the FIX (recording incremental reward):
  Height 1: credited_reward = 5 ANM   (incremental reward for block 1)
  Height 2: credited_reward = 5 ANM   (incremental reward for block 2)
  Height 3: credited_reward = 5 ANM   (incremental reward for block 3)
  
  ✅ Miners see: Consistent 5 ANM reward at all heights
  ✅ No false balance decrease, even if blocks get reorged
```

## Files Changed

### Core Fix
- `rpc/methods/miner.py`
  - Line 3510: Changed `credited_reward=final_balance` to `credited_reward=reward_amount`
  - Line 136: Updated comment to clarify meaning
  - Line 140-158: Updated `_record_mining_audit` docstring
  - Line 5270-5296: Updated `mining.getCredits` RPC docstring

### Tests Added
- `test_mining_audit_reward_fix.py`: Unit test verifying incremental rewards
- `test_mining_balance_manual.py`: Manual verification with detailed explanation

### Tests Updated
- `python/animica/cli/tests/test_mining_audit_trail.py`: Updated expectations to match corrected behavior

## Testing & Validation

### New Tests
✅ `test_mining_audit_reward_fix.py`
- Verifies incremental rewards are recorded correctly
- Demonstrates old buggy behavior would show false decrease
- All tests pass

✅ `test_mining_balance_manual.py`
- Manual verification with detailed explanation
- Shows before/after comparison
- Verifies no false decrease on higher blocks

### Existing Tests (Still Pass)
✅ `test_100_percent_mining_rewards.py`
- Validates 100% of block rewards go to miners
- All 5 tests pass

### Code Review
✅ Code review completed
- Addressed feedback for comment clarity
- No blocking issues found

## Impact

### For Miners
- No more false balance decrease warnings
- Consistent reward display at all heights
- Clear understanding that rewards are being credited correctly
- `mining.getCredits` RPC shows accurate incremental rewards

### For Developers
- Audit trail now correctly tracks incremental rewards
- Better debugging of mining economics
- Clear separation between incremental rewards and total balance
- Documentation updated to prevent future confusion

## Deployment Notes

### Breaking Changes
None. This is a display-only fix that doesn't affect:
- Actual reward crediting (still works correctly)
- Blockchain consensus
- State transitions
- Backward compatibility

### Migration
No migration needed. The fix applies to new audit trail entries. Existing entries in memory will be cleared on next node restart.

### Monitoring
After deployment, verify:
1. `mining.getCredits` shows consistent reward values
2. No false balance decrease reports from miners
3. Audit trail entries show incremental rewards (not cumulative)

## References

### Code Locations
- Main fix: `rpc/methods/miner.py:3510`
- Audit recording: `rpc/methods/miner.py:140-180`
- RPC method: `rpc/methods/miner.py:5263-5333`

### Related Issues
- Original problem: "Sometimes for miners it shows less balance on higher heights than they had despite never sending any ANM"
- Related to: Block reorgs, orphaned blocks, canonical chain updates

### Test Files
- `test_mining_audit_reward_fix.py`
- `test_mining_balance_manual.py`
- `python/animica/cli/tests/test_mining_audit_trail.py`

## Conclusion

This fix resolves the confusing display issue where miners saw decreasing balance on higher heights. The root cause was recording total balance (which changes due to reorgs) instead of incremental rewards (which are stable). By recording the actual reward amount for each block, miners now see consistent reward tracking at all heights, even in the presence of reorgs and orphaned blocks.

**Status:** ✅ Complete and tested
**Review:** ✅ Passed
**Tests:** ✅ All passing
**Ready:** ✅ Ready for merge
