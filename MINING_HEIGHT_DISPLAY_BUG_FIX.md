# Mining Height Display Bug Fix

## Problem Summary
When mining multiple blocks sequentially using the `animica miner mine` command, all blocks were displaying the same height in the console output instead of showing incrementing heights.

## Example Bug Output
```
FOUND: Block 1/99999 PoW (height: 1157, nonce: 0, hash: 0x6562dbfcf58ed3ff...)
ACCEPTED: Block 1/99999 (height: 1157, reward: 5.000000000 ANM = 5000000000 nANM, credited: 5000000000 nANM)
Template: mempool_total=0 included=0 rejected=0 (top reasons: none)
FOUND: Block 2/99999 PoW (height: 1157, nonce: 1, hash: 0xaec4ebd11c20a675...)
ACCEPTED: Block 2/99999 (height: 1157, reward: 5.000000000 ANM = 5000000000 nANM, credited: 5000000000 nANM)
Template: mempool_total=0 included=0 rejected=0 (top reasons: none)
FOUND: Block 3/99999 PoW (height: 1157, nonce: 0, hash: 0xa2c990d5f8d67c4b...)
ACCEPTED: Block 3/99999 (height: 1157, reward: 5.000000000 ANM = 5000000000 nANM, credited: 5000000000 nANM)
```

## Root Cause
The mining CLI was reading the block height from the block template (`template.get("header", {}).get("height", 0)`), which doesn't change between blocks until a new template is explicitly fetched. This resulted in displaying the same height for all sequentially mined blocks.

## Technical Details

### Code Location
File: `python/animica/cli/mining.py`
Line: 1442 (before fix), 1449 (after fix)

### Before Fix
```python
# Line 1442
final_height = int(template.get("header", {}).get("height", 0))
```

This reads from the template's header, which remains static until a new template is requested.

### After Fix
```python
# Line 1449
final_height = int(submit_result.get("new_head", 0))
```

This reads from the `submit_result` returned by the `miner.submitBlock` RPC call, which contains the actual height of the accepted block.

### RPC Response Structure
The `miner.submitBlock` RPC endpoint returns:
```python
{
    "accepted": True,
    "duplicate": False,
    "credited_amount": 5000000000,
    "new_head": 1158,  # Actual accepted block height
    "block_hash": "0xabc123..."
}
```

The `new_head` field contains the correct, incrementing height for each accepted block.

## Expected Output After Fix
```
FOUND: Block 1/99999 PoW (height: 1157, nonce: 0, hash: 0x6562dbfcf58ed3ff...)
ACCEPTED: Block 1/99999 (height: 1157, reward: 5.000000000 ANM = 5000000000 nANM, credited: 5000000000 nANM)
Template: mempool_total=0 included=0 rejected=0 (top reasons: none)
FOUND: Block 2/99999 PoW (height: 1158, nonce: 1, hash: 0xaec4ebd11c20a675...)
ACCEPTED: Block 2/99999 (height: 1158, reward: 5.000000000 ANM = 5000000000 nANM, credited: 5000000000 nANM)
Template: mempool_total=0 included=0 rejected=0 (top reasons: none)
FOUND: Block 3/99999 PoW (height: 1159, nonce: 0, hash: 0xa2c990d5f8d67c4b...)
ACCEPTED: Block 3/99999 (height: 1159, reward: 5.000000000 ANM = 5000000000 nANM, credited: 5000000000 nANM)
```

## Changes Made

### 1. Core Fix
**File**: `python/animica/cli/mining.py`

Changed line 1449 from:
```python
final_height = int(template.get("header", {}).get("height", 0))
```

To:
```python
final_height = int(submit_result.get("new_head", 0))
```

Also updated the comment to reflect that we're extracting both credited_amount AND height from submit_result.

### 2. Test Coverage
**File**: `python/animica/cli/tests/test_mining_height_increment.py` (new)

Added a comprehensive test that:
- Mocks the RPC client to return templates with static height (1000)
- Mocks submitBlock responses with incrementing heights (1001, 1002, 1003)
- Verifies that the CLI output shows the correct incrementing heights
- Ensures the template height (1000) is not displayed in ACCEPTED messages
- Confirms the final summary shows the correct chain height

## Testing
The fix has been validated through:
1. Code review confirming correct data source
2. Unit test added to verify behavior
3. Manual verification of the code flow

## Impact
- **User-facing**: Mining output now correctly shows incrementing block heights
- **Functional**: No change to actual block production or chain state
- **Display only**: This was purely a display bug; blocks were being created at correct heights, just not reported correctly in the CLI output

## Related Files
- `python/animica/cli/mining.py` - Main fix location
- `python/animica/cli/tests/test_mining_height_increment.py` - Test coverage
- `rpc/methods/miner.py` - Contains submitBlock RPC method that returns new_head

## Verification Steps
To verify the fix works:
1. Mine multiple blocks using: `animica miner mine --count 3`
2. Observe that each ACCEPTED message shows an incrementing height
3. Verify the final summary shows the correct "New chain height"
