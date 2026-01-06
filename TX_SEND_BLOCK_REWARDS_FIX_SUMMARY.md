# TX Send Block Rewards Fix - Implementation Summary

## Problem Statement

When `animica tx send` is called, it mines a block to persist the transaction immediately. However, this block was incorrectly receiving full mining rewards (5 ANM at genesis) instead of 0 ANM.

## Root Cause

The `_ensure_tx_persisted_to_chain()` function in `rpc/methods/tx.py` calls `miner_mine()` to force a block to be mined, but there was no mechanism to indicate that this block should have zero rewards. The block was treated as a normal mined block and received full rewards.

## Solution

Added an `instant_block` parameter throughout the mining and reward calculation stack:

1. **Reward Calculation Layer** (`consensus/rewards.py`)
   - Added `instant_block: bool = False` parameter to `compute_block_reward()`
   - When `instant_block=True`, return empty reward list `[]`
   - This ensures no tokens are issued for instant blocks

2. **Mining Layer** (`rpc/methods/miner.py`)
   - Added `instant_block` parameter to:
     - `miner_mine()` - Main mining RPC method
     - `_mine_once()` - Single block mining function
     - `_apply_block_reward()` - Reward application function
   - Parameter is propagated through the entire call stack
   - Fixed warning logic to not warn about zero rewards for instant blocks

3. **Transaction Submission** (`rpc/methods/tx.py`)
   - Modified `_ensure_tx_persisted_to_chain()` to call `miner_mine()` with `instant_block=True`
   - This ensures tx send blocks have zero rewards

## Implementation Details

### Key Changes

#### consensus/rewards.py
```python
def compute_block_reward(
    chain_id: int,
    height: int,
    params: Mapping[str, Any] | None = None,
    instant_block: bool = False,  # NEW PARAMETER
) -> List[Tuple[str, int]]:
    # Instant blocks always have zero rewards
    if instant_block:
        return []
    # ... rest of reward calculation
```

#### rpc/methods/miner.py
```python
def miner_mine(..., instant_block: bool | None = None):
    instant_block_flag = bool(instant_block)
    # ...
    mine_result = _mine_once(..., instant_block=instant_block_flag)

def _mine_once(..., instant_block: bool = False):
    # ...
    reward_amount = _apply_block_reward(ctx, header.height, payout_address, instant_block=instant_block)

def _apply_block_reward(ctx, height, payout_address=None, instant_block: bool = False):
    rewards = compute_block_reward(chain_id=chain_id, height=height, params=params, instant_block=instant_block)
    # Only warn about zero rewards if NOT an instant block
    if not rewards and height >= 1 and not instant_block:
        log.warning("Block reward is empty...")
```

#### rpc/methods/tx.py
```python
def _ensure_tx_persisted_to_chain(tx_hash_hex: str) -> tuple[bool, str | None]:
    # Mine a block with instant_block=True for zero rewards
    try:
        miner_methods.miner_mine(
            count=1,
            include_mempool=True,
            allow_offline_mining=True,
            allow_unsynced_mining=True,
            instant_block=True,  # FORCE ZERO REWARDS
        )
```

## Testing

### Unit Tests

Added comprehensive tests in `consensus/tests/test_rewards.py`:

1. `test_instant_block_always_returns_zero_rewards()` - Verifies zero rewards at various heights
2. `test_instant_block_mainnet_genesis_returns_zero()` - Verifies no premine for instant genesis
3. `test_instant_block_vs_normal_block()` - Compares instant vs normal block rewards

### Integration Tests

Created test files to verify the complete flow:

1. `test_instant_block_zero_reward.py` - Tests `compute_block_reward()` behavior
2. `test_tx_send_instant_block_integration.py` - Tests parameter propagation

### Manual Verification

Verified the complete flow:
```
tx send → _ensure_tx_persisted_to_chain() 
        → miner_mine(instant_block=True)
        → _mine_once(instant_block=True)
        → _apply_block_reward(instant_block=True)
        → compute_block_reward(instant_block=True)
        → returns []
        → zero rewards applied
```

## Results

### Before Fix
- TX send mines a block with 5 ANM reward
- Unintended token inflation
- Incorrect economic model

### After Fix
- TX send mines a block with 0 ANM reward
- No unintended token inflation
- Correct economic model maintained

## Verification

All verification steps passed:
- ✅ `compute_block_reward(instant_block=True)` returns `[]`
- ✅ All mining functions accept `instant_block` parameter
- ✅ `_ensure_tx_persisted_to_chain()` uses `instant_block=True`
- ✅ Warning logic correctly suppressed for instant blocks
- ✅ Normal blocks still receive correct rewards
- ✅ Mainnet premine unaffected for normal genesis
- ✅ Unit tests pass
- ✅ Integration tests pass

## Backward Compatibility

This change is backward compatible:
- Default value for `instant_block` is `False` in all functions
- Normal mining operations unchanged
- Only affects blocks mined via `tx send`
- No breaking changes to existing APIs

## Security Considerations

- ✅ No tokens are minted for instant blocks
- ✅ Mainnet premine protection preserved
- ✅ Normal reward schedule unaffected
- ✅ No new attack vectors introduced
- ✅ Deterministic behavior (parameter-driven)

## Future Enhancements

Potential improvements for future consideration:

1. Add canonical height tracking to ensure instant blocks don't affect halving schedule (if needed)
2. Add metrics/monitoring for instant block frequency
3. Consider rate limiting instant blocks if needed
4. Add instant block flag to block header for on-chain visibility (if needed)

## Files Changed

1. `consensus/rewards.py` - Add instant_block parameter
2. `rpc/methods/miner.py` - Add instant_block throughout mining stack
3. `rpc/methods/tx.py` - Use instant_block=True for tx send
4. `consensus/tests/test_rewards.py` - Add comprehensive tests
5. `test_instant_block_zero_reward.py` - New test file
6. `test_tx_send_instant_block_integration.py` - New test file

## Conclusion

The fix successfully ensures that blocks mined during `tx send` operations have zero rewards, preventing unintended token inflation while maintaining all existing functionality. The implementation is clean, well-tested, and backward compatible.
