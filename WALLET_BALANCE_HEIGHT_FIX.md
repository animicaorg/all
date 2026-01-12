# Wallet Show Balance Height Fix - Summary

## Problem Statement

The `animica wallet show` command was displaying misleading height information when showing wallet balances. This caused confusion about the accurate balance at the current chain height.

### Specific Issue

1. The command always fetched balance at the "safe" (finalized) height using `tag="safe"`
2. However, the displayed head information could show the current tip height
3. This created a mismatch where users saw:
   - `balance_confirmed: 1000000000`
   - `head: { height: 150 }`
   - But the balance was actually at height 145 (safe/finalized)

Users were confused whether their balance was at height 145 or 150.

## Solution

### Changes Made

1. **Always fetch both safe and tip heads**
   - `chain.getSafeHead` is called to get the finalized head
   - `chain.getHead` is called to get the current tip head
   - Both are included in the output for clarity

2. **Add explicit height fields**
   - `balance_confirmed_height`: Shows the exact height where `balance_confirmed` was queried
   - `balance_tip_height`: Shows the exact height where `balance_tip` was queried (when --include-tip is used)

3. **Clear field naming**
   - `safe_head`: The finalized/safe head information
   - `head`: The current tip head information
   - Users can now clearly see the difference

4. **Backward compatibility**
   - If `chain.getSafeHead` is not available, the code falls back to using `chain.getHead` as the safe head
   - Existing functionality is preserved

## Example Output

### Before the Fix (Confusing)

```json
{
  "label": "my-wallet",
  "address": "anim1...",
  "balance_confirmed": 1000000000,
  "balance_source": "chain",
  "head": {
    "height": 150,
    "hash": "0xabc..."
  }
}
```

**Problem**: User doesn't know if balance is at height 150 or some earlier finalized height.

### After the Fix (Clear)

```json
{
  "label": "my-wallet",
  "address": "anim1...",
  "balance_confirmed": 1000000000,
  "balance_confirmed_height": 145,
  "balance_source": "chain",
  "safe_head": {
    "height": 145,
    "hash": "0xdef...",
    "rpc_url": "http://127.0.0.1:8545"
  },
  "head": {
    "height": 150,
    "hash": "0xabc...",
    "rpc_url": "http://127.0.0.1:8545"
  },
  "queried_at": "2026-01-12T21:30:00Z"
}
```

**Solution**: User clearly sees:
- `balance_confirmed` is at height **145** (from `balance_confirmed_height`)
- `safe_head` shows height **145** - matches the balance!
- `head` shows height **150** - current tip for context
- No confusion about which height the balance corresponds to

### With --include-tip Flag

```json
{
  "label": "my-wallet",
  "address": "anim1...",
  "balance_confirmed": 1000000000,
  "balance_confirmed_height": 145,
  "balance_tip": 1100000000,
  "balance_tip_height": 150,
  "balance_source": "chain",
  "safe_head": {
    "height": 145,
    "hash": "0xdef..."
  },
  "head": {
    "height": 150,
    "hash": "0xabc..."
  }
}
```

Users can now see:
- Safe balance (1,000,000,000) at height 145
- Tip balance (1,100,000,000) at height 150
- Both heights are explicitly labeled

## Technical Details

### Files Modified

1. **python/animica/cli/wallet.py**
   - Updated `show()` command to fetch both safe and tip heads
   - Added `balance_confirmed_height` and `balance_tip_height` fields to output
   - Ensured backward compatibility when getSafeHead is unavailable

2. **python/animica/cli/tests/test_wallet_cli.py**
   - Updated existing tests to accommodate new RPC call patterns (3 calls instead of 2)
   - Fixed JSON parsing to handle warning messages

3. **python/animica/cli/tests/test_wallet_show_output.py**
   - Fixed JSON parsing to handle warning messages in output

4. **python/animica/cli/tests/test_wallet_balance_height_fix.py** (New)
   - Comprehensive test suite validating the fix
   - Tests safe/tip head distinction
   - Tests fallback behavior when getSafeHead is unavailable
   - Tests height field accuracy

### RPC Call Changes

**Before**: 2 RPC calls
1. `chain.getSafeHead` (or fallback to `chain.getHead`)
2. `state.getBalance` with tag="safe"

**After**: 3 RPC calls
1. `chain.getSafeHead`
2. `chain.getHead`
3. `state.getBalance` with tag="safe"
4. (Optional) `state.getBalance` with tag="latest" if --include-tip is used

The additional RPC call ensures users always have complete information about both the finalized and current chain state.

## Testing

All existing tests pass, plus 4 new comprehensive tests:

1. `test_wallet_show_displays_balance_height_with_safe_and_tip`
   - Verifies both safe and tip heads are fetched and displayed correctly
   - Confirms balance heights match their respective heads

2. `test_wallet_show_without_include_tip_shows_safe_only`
   - Verifies safe balance is shown without --include-tip flag
   - Confirms both heads are still fetched for context

3. `test_wallet_show_fallback_when_safe_head_unavailable`
   - Tests backward compatibility when getSafeHead RPC is not available
   - Ensures safe_head falls back to tip head

4. `test_wallet_show_balance_height_matches_safe_head`
   - Validates that balance_confirmed_height always matches safe_head height
   - Confirms the fix addresses the original issue

## Usage

No changes to the CLI interface - existing commands work the same way:

```bash
# Show wallet balance with clear height information
animica wallet show my-wallet

# Include tip balance for comparison
animica wallet show my-wallet --include-tip

# Show balance from cached data (no RPC calls)
animica wallet show my-wallet --source cached
```

## Impact

- **User Experience**: Users now have clear, unambiguous information about wallet balances and heights
- **Backward Compatibility**: Existing scripts and integrations continue to work
- **Performance**: One additional RPC call per wallet show (negligible impact)
- **Accuracy**: Eliminates confusion about balance heights

## Future Enhancements

Potential future improvements:
1. Add a `--no-tip-head` flag to skip fetching tip head if not needed
2. Cache head information for a few seconds to reduce RPC calls
3. Add visual indicators in formatted output (not just JSON) to highlight differences between safe and tip

## Related Issues

This fix addresses the problem described in the issue:
> "Wallet show cli command consistently shows the wallet balance for not the current height leading to confusion about accurate balance"

The fix ensures users always know exactly which height their balance corresponds to, eliminating confusion.
