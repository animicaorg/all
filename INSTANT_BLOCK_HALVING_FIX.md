# Mining Rewards and Instant Block Halving Fix

## Problem Statement
"Mining yields no rewards on recent fix because of instant block thing make sure mining yields the only rewards and tx instant blocks don't and also that tx instant blocks don't count towards halving"

## Analysis
The issue had three requirements:
1. ✅ Mining blocks should yield rewards (already working)
2. ✅ TX instant blocks should NOT yield rewards (already working via `instant_block=True` flag)
3. ❌ TX instant blocks should NOT count towards halving schedule (was broken - FIXED)

### Root Cause
The halving schedule was calculated based on absolute block height, which includes both mining blocks and instant blocks. This meant that instant blocks (created by `tx.sendRawTransaction` for immediate transaction persistence) were advancing the halving schedule even though they didn't distribute any rewards.

Example problem scenario:
- Halving configured at every 100 blocks
- 50 mining blocks + 50 instant blocks = 100 total blocks at height 100
- Halving triggers at height 101, but only 50 blocks actually paid rewards
- This effectively doubles the halving speed

## Solution
Implemented a "canonical height" tracking system that only counts mining blocks (non-instant blocks) for halving calculations:

### Changes Made

#### 1. Mark Instant Blocks in Block Header
**File: `rpc/methods/miner.py`**
- Modified `_build_child_header()` to accept `instant_block` parameter
- Encode `instant_block` flag in block's `extra` field as CBOR: `{coinbase: bytes, instant_block: bool}`
- Pass `instant_block` flag from `_mine_once()` to `_build_child_header()`

#### 2. Track Canonical Height (Mining Blocks Only)
**File: `core/chain/block_import.py`**
- Added `_is_instant_block()` helper to decode and check block's extra field
- Modified canonical_height increment to skip instant blocks:
  ```python
  if not _is_instant_block(header):
      canonical_height += 1
      self.block_db.set_canonical_height(canonical_height)
  ```

#### 3. Use Canonical Height for Halving
**File: `consensus/rewards.py`**
- Added `canonical_height` parameter to `compute_block_reward()`
- Use `canonical_height` (if provided) for halving calculation instead of absolute height
- Falls back to `height` for backward compatibility if `canonical_height` is None

**File: `rpc/methods/miner.py`**
- Modified `_apply_block_reward()` to:
  - Retrieve current canonical_height from block_db
  - Calculate next canonical_height (current + 1 for mining blocks)
  - Pass canonical_height to `compute_block_reward()`

### Data Flow
```
Mining Block (instant_block=False):
  1. _mine_once() creates block with instant_block=False
  2. _build_child_header() stores {instant_block: false} in extra field
  3. Block imported → _is_instant_block() returns False
  4. canonical_height incremented
  5. Next mining block uses canonical_height for reward calculation
  6. Halving based on canonical_height (counts this block)

Instant Block (instant_block=True):
  1. tx.sendRawTransaction() calls miner_mine(instant_block=True)
  2. _mine_once() creates block with instant_block=True
  3. _build_child_header() stores {instant_block: true} in extra field
  4. _apply_block_reward() returns 0 (instant_block=True)
  5. Block imported → _is_instant_block() returns True
  6. canonical_height NOT incremented
  7. Next mining block: canonical_height unchanged, halving unaffected
```

## Testing

### New Test: `test_instant_block_halving.py`
Comprehensive test covering three scenarios:

**Scenario 1: Mining blocks only**
- Verifies halving occurs at correct canonical_height
- epoch_length=10, halving at canonical_height=11

**Scenario 2: Mix of mining and instant blocks**
- 10 mining blocks + 10 instant blocks = 20 absolute height
- canonical_height = 10 (only mining blocks count)
- Halving still occurs at canonical_height=11 (not at absolute height 11)

**Scenario 3: Instant blocks always zero rewards**
- Confirms instant_block=True always returns empty rewards list

### Existing Tests (All Pass)
✅ `test_instant_block_zero_reward.py` - Instant blocks have zero rewards
✅ `test_100_percent_mining_rewards.py` - Mining blocks get 100% of rewards
✅ All reward split tests pass with canonical_height support

## Backward Compatibility
- `canonical_height` parameter in `compute_block_reward()` is optional
- Falls back to using `height` if `canonical_height=None`
- Existing code that doesn't pass canonical_height continues to work
- Only new mining operations benefit from proper halving tracking

## Security Considerations
- Instant block flag stored in block header extra field (part of block hash)
- Cannot be tampered with after block creation
- Block import validates and tracks canonical_height deterministically
- All nodes will agree on canonical_height given same blockchain

## Future Improvements
1. Consider storing canonical_height directly in block header for explicit tracking
2. Add metrics/monitoring for instant block ratio (instant blocks / total blocks)
3. Document canonical_height in block database schema documentation
4. Add RPC method to query canonical_height vs absolute height

## Summary
The fix ensures that instant blocks (used for immediate transaction persistence) do not accelerate the halving schedule. Only actual mining blocks that distribute rewards count towards halving. This maintains the intended tokenomics where halving is based on rewarded blocks, not total blocks.

**Result**: Mining yields rewards correctly, instant blocks have zero rewards, and halving schedule is based only on mining blocks.
