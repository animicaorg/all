# Duplicate Block Counting Fix - Summary

## Problem
Blocks were being counted twice when two different miners found blocks at the same height. This led to inflated metrics and incorrect block statistics.

## Root Cause
Block counting occurred at multiple layers BEFORE duplicate detection:
1. ShareSubmitter incremented counters for all accepted blocks
2. RPC endpoints didn't check if block hash already existed
3. Stratum pool metrics recorded all blocks marked as `is_block=True`
4. ASIC pool didn't verify duplicate status before recording

## Solution
Implemented duplicate detection and filtering at all critical points in the mining pipeline:

### 1. ShareSubmitter (`mining/share_submitter.py`)
- Check `duplicate` flag in RPC responses
- Only increment `blocks_accepted` if `accepted=true` AND `duplicate=false`
- Applied to both sync and async submission methods

### 2. RPC miner.submitWork (`rpc/methods/miner.py`)
- Query `block_db.get_header_by_hash()` before recording block
- Return `duplicate: true` flag if block hash already exists
- Return `duplicate: false` for new blocks

### 3. Stratum Pool Core (`python/animica/stratum_pool/core.py`)
- Extract `duplicate` flag from RPC response
- Set `is_block=False` for duplicate blocks to prevent metrics recording

### 4. ASIC Pool (`python/animica/stratum_pool/asic.py`)
- Get result from block submission
- Only pass `is_block=True` to metrics hook if NOT duplicate

## Testing
- Created logic tests in `test_duplicate_block_fix.py` - all passing ✓
- Ran existing mining tests - 9 tests passing ✓
- Verified no regressions ✓

## Result
When two miners find blocks at the same height:
- **Before**: Both blocks counted (2 blocks for 1 canonical)
- **After**: Only canonical block counted (1 block for 1 canonical)

## Files Changed
```
mining/share_submitter.py              (+11 -8)
rpc/methods/miner.py                   (+20)
python/animica/stratum_pool/core.py    (+6)
python/animica/stratum_pool/asic.py    (+9 -2)
python/animica/stratum_pool/metrics.py (+21)
test_duplicate_block_fix.py            (+132)
```

Total: 6 files, 199 insertions, 8 deletions

## Verification
All critical code paths verified:
- ✓ ShareSubmitter checks duplicate flag
- ✓ RPC detects duplicate blocks  
- ✓ Stratum pool filters duplicates
- ✓ ASIC pool verifies before metrics
