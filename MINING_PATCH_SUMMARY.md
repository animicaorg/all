# Mining Backwards Compatible Patch - Summary

## Problem Statement
Make a backwards compatible patch to mining to ensure mining continues to the amount set, skipping blocks that are already found, with only 1 retry.

## Solution Overview
The patch modifies the mining loop in `python/animica/cli/mining.py` to:
1. Continue mining until the requested count of **non-duplicate** blocks are successfully mined
2. Skip blocks that are marked as duplicates (already found by another miner)
3. Reduce the retry count for stale templates from 3 to 1
4. Add safety limits to prevent infinite loops

## Key Changes

### 1. Loop Structure (Line ~1220)
**Before:**
```python
for i in range(count):
    # Mine exactly 'count' attempts
```

**After:**
```python
MAX_TOTAL_ATTEMPTS = count * 10
while total_mined < count and blocks_attempted < MAX_TOTAL_ATTEMPTS:
    # Continue until 'count' successful non-duplicate blocks are mined
```

**Impact:** 
- Ensures we mine exactly the requested number of blocks that advance the chain
- Prevents infinite loops with a 10x safety limit
- Handles race conditions where other miners find blocks first

### 2. Duplicate Detection (Line ~1631)
**Added:**
```python
is_duplicate = submit_result.get("duplicate", False)

if is_duplicate:
    typer.secho(
        f"  DUPLICATE: Block already found by another miner (skipping, progress: {total_mined}/{count})",
        fg=typer.colors.YELLOW,
    )
    blocks_attempted += 1
    stale_attempts = 0
    break  # Skip to next block without counting in total_mined
```

**Impact:**
- Detects when a block was already mined by another miner
- Skips the duplicate and continues mining
- Provides clear feedback to the user

### 3. Retry Count Reduction (Multiple locations)
**Before:**
```python
if stale_attempts < 3:
    stale_attempts += 1
    typer.secho(f"  Retrying with fresh template (stale attempt {stale_attempts}/3)", ...)
```

**After:**
```python
if stale_attempts < 1:
    stale_attempts += 1
    typer.secho(f"  Retrying with fresh template (stale attempt {stale_attempts}/1)", ...)
```

**Impact:**
- Reduces time spent on stale templates
- Moves to next block faster
- More responsive to blockchain state changes

### 4. Progress Display (Multiple locations)
**Before:**
```python
f"Block {i + 1}/{count}"  # Could show "Block 5/3" with duplicates/failures
```

**After:**
```python
f"Block {total_mined + 1}/{count}"  # Always shows accurate progress
```

**Impact:**
- Clear and accurate progress display
- Shows actual progress toward goal, not total attempts

## Backwards Compatibility

### RPC Response Format
The patch uses the existing `duplicate` field in the `miner.submitBlock` response:
```python
{
    "accepted": True,
    "duplicate": False,  # <-- Already returned by node
    "new_head": 101,
    "credited_amount": 1000
}
```

### Graceful Degradation
- If `duplicate` field is missing: defaults to `False` (no duplicate)
- Works with older nodes that don't return the field
- No breaking changes to existing behavior when field is absent

### Client-Side Only Changes
- No server/node changes required
- No RPC protocol changes
- Fully compatible with existing infrastructure

## Testing

### New Tests (`test_mining_skip_duplicate.py`)
1. **test_mine_blocks_skips_duplicates_and_continues**
   - Requests 3 blocks
   - Block 2 is a duplicate
   - Verifies 4 blocks are attempted to get 3 non-duplicates

2. **test_mine_blocks_single_retry_for_stale**
   - Requests 2 blocks
   - First block is stale on first attempt
   - Verifies only 1 retry happens (not 3)

### Updated Existing Tests
1. **test_mining_cli.py::test_mine_blocks_continues_after_consecutive_rejections**
   - Updated to expect 1 retry instead of 3
   - Fixed logic gap in mock implementation

2. **test_mining_stale_cooldown.py::test_cooldown_after_stale_template_exhaustion**
   - Updated to expect 1 retry instead of 3

## Usage Examples

### Scenario 1: Normal Mining (No Duplicates)
```bash
$ animica miner mine-blocks --address premine --count 3
Mining 3 block(s) with local P2P validation...
  FOUND: Block 1/3 PoW (height: 100, nonce: 12345, hash: 0xabc...)
  ACCEPTED: Block 1/3 (height: 100, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
  FOUND: Block 2/3 PoW (height: 101, nonce: 67890, hash: 0xdef...)
  ACCEPTED: Block 2/3 (height: 101, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
  FOUND: Block 3/3 PoW (height: 102, nonce: 54321, hash: 0x123...)
  ACCEPTED: Block 3/3 (height: 102, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
✓ Successfully mined 3 block(s). New chain height: 102. Total reward: 0.900000000 ANM
```

### Scenario 2: Mining with Duplicates
```bash
$ animica miner mine-blocks --address premine --count 3
Mining 3 block(s) with local P2P validation...
  FOUND: Block 1/3 PoW (height: 100, nonce: 12345, hash: 0xabc...)
  ACCEPTED: Block 1/3 (height: 100, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
  FOUND: Block 2/3 PoW (height: 101, nonce: 67890, hash: 0xdef...)
  DUPLICATE: Block already found by another miner (skipping, progress: 1/3)
  FOUND: Block 2/3 PoW (height: 101, nonce: 11111, hash: 0x456...)
  ACCEPTED: Block 2/3 (height: 101, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
  FOUND: Block 3/3 PoW (height: 102, nonce: 54321, hash: 0x123...)
  ACCEPTED: Block 3/3 (height: 102, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
✓ Successfully mined 3 block(s). New chain height: 102. Total reward: 0.900000000 ANM
```

### Scenario 3: Mining with Stale Templates (1 Retry)
```bash
$ animica miner mine-blocks --address premine --count 2
Mining 2 block(s) with local P2P validation...
  FOUND: Block 1/2 PoW (height: 100, nonce: 12345, hash: 0xabc...)
  REJECTED: Block 1/2 (reason: stale_template)
  Retrying with fresh template (stale attempt 1/1)
  FOUND: Block 1/2 PoW (height: 101, nonce: 67890, hash: 0xdef...)
  ACCEPTED: Block 1/2 (height: 101, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
  FOUND: Block 2/2 PoW (height: 102, nonce: 54321, hash: 0x123...)
  ACCEPTED: Block 2/2 (height: 102, reward: 0.300000000 ANM = 300000000 nANM, credited: 300000000 nANM)
✓ Successfully mined 2 block(s). New chain height: 102. Total reward: 0.600000000 ANM
```

## Implementation Details

### Counters
- `total_mined`: Count of successfully mined non-duplicate blocks (the goal)
- `blocks_attempted`: Total number of mining attempts (includes failures and duplicates)
- `stale_attempts`: Retry counter for current block (max 1)

### Loop Flow
```
while total_mined < count and blocks_attempted < MAX_TOTAL_ATTEMPTS:
    1. Get block template
    2. Mine (find PoW)
    3. Submit block
    4. Check result:
       - If duplicate: skip (don't count in total_mined)
       - If accepted: count in total_mined
       - If rejected stale: retry once, then move on
       - If other error: move on
    5. Increment blocks_attempted
    6. Sleep before next attempt
```

### Safety Mechanisms
1. **Maximum Attempt Limit:** `MAX_TOTAL_ATTEMPTS = count * 10`
   - Prevents infinite loops if blocks consistently fail
   - Warning message when limit is reached

2. **Stale Template Cooldown:** 4 seconds (2x MIN_BLOCK_INTERVAL_SECONDS)
   - Applied after exhausting retry for stale template
   - Gives blockchain time to stabilize

3. **Inter-Block Sleep:** 2 seconds (MIN_BLOCK_INTERVAL_SECONDS)
   - Prevents overwhelming the node
   - Applied between all mining attempts

## Performance Considerations

### Time to Mine N Blocks
- **Best case:** N blocks × (PoW time + 2s sleep)
- **With duplicates:** More attempts but same N successful blocks
- **With stale templates:** +1 retry × (PoW time + possible cooldown)

### Resource Usage
- **Network:** Same as before (RPC calls per block)
- **CPU:** Same as before (PoW computation)
- **Memory:** Negligible additional overhead (few extra variables)

## Migration Guide

### For Operators
No changes required! The patch is fully backwards compatible and works with existing nodes.

### For Developers
If you have custom mining scripts, they will continue to work. The only visible change is:
- More accurate progress messages
- Duplicate blocks are now skipped automatically
- Only 1 retry for stale templates (previously 3)

## Verification

### Manual Testing
1. Start a local node
2. Run mining command with `--count 3`
3. Verify 3 blocks are mined (not more, not less)
4. Check that duplicates are skipped if they occur
5. Verify only 1 retry for stale templates

### Automated Testing
```bash
# Run new tests
pytest python/animica/cli/tests/test_mining_skip_duplicate.py -v

# Run existing tests
pytest python/animica/cli/tests/test_mining_cli.py::test_mine_blocks_continues_after_consecutive_rejections -v
pytest python/animica/cli/tests/test_mining_stale_cooldown.py -v
```

## Security Analysis
- ✅ CodeQL scan: No security issues detected
- ✅ No external dependencies added
- ✅ No credentials or sensitive data handling changes
- ✅ Input validation unchanged
- ✅ Error handling improved (max attempt limit)

## Conclusion
This patch successfully implements the requested functionality while maintaining full backwards compatibility. The changes are minimal, focused, and well-tested.
