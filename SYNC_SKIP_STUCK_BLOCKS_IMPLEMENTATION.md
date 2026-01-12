# Sync Skip Stuck Blocks Feature

## Overview

This feature prevents nodes from getting permanently stuck during sync when a specific block repeatedly fails to import. Instead of retrying the same block indefinitely, the node will skip it after a threshold number of failures and continue syncing the next blocks. The skipped block is added to a separate queue and retried later with different peers.

## Problem Statement

Previously, if a node encountered a block that failed to import for non-orphan, non-PoW reasons (e.g., validation errors, corrupted data, temporary state inconsistencies), the block would not be automatically retried. This could cause sync to stall indefinitely waiting for that single block, even when subsequent blocks were available.

## Solution

### Key Components

1. **Failure Tracking**: Track the number of import failures per block hash
2. **Skip Threshold**: After N failures (default: 3), skip the block
3. **Skipped Queue**: Move skipped blocks to a separate queue for later retry
4. **Periodic Retry**: Periodically retry skipped blocks when main queue is small
5. **Success Cleanup**: Clear failure counters on successful import

### Implementation Details

#### Data Structures

```python
self._block_import_failures: Dict[bytes, int]  # block_hash -> failure_count
self._block_import_failure_threshold: int  # Default: 3 (configurable)
self._skipped_blocks_queue: Deque[bytes]  # Blocks to retry later
self._skipped_blocks_set: Set[bytes]  # Fast lookup for skipped blocks
```

#### Configuration

Environment variable to configure skip threshold:
```bash
export ANIMICA_P2P_BLOCK_FAILURE_SKIP_THRESHOLD=3
```

#### Flow Diagram

```
Block Import Failed (non-orphan, non-PoW)
    |
    v
Increment failure counter
    |
    v
failure_count >= threshold? 
    |
    +-- YES --> Skip block
    |           |
    |           +-- Add to skipped_blocks_queue
    |           +-- Log warning with details
    |           +-- Continue to next block
    |
    +-- NO --> Re-queue for retry
                |
                +-- Add back to sync_block_queue
                +-- Log debug message
```

#### Retry Logic

Skipped blocks are periodically retried when:
- Main block queue has < 10 blocks
- Retries up to 5 blocks per cycle
- Failure counters are reset to give fresh chance
- Uses potentially different peers than original attempt

## Benefits

1. **Prevents Stalls**: Node won't get permanently stuck on a single bad block
2. **Automatic Recovery**: Skipped blocks retry automatically with different peers
3. **Progress Continues**: Sync can continue with subsequent blocks
4. **Resource Efficient**: Limits memory usage with queue size caps
5. **Configurable**: Threshold can be adjusted via environment variable

## Logging

### Warning Level
When a block is skipped:
```
Skipping stuck block after repeated failures - will retry later with different peer
  remote: 192.168.1.100:30333
  block_hash: abc123...
  failure_count: 3
  reason: validation_failed
  height_hint: 12345
```

### Debug Level
When a block is re-queued below threshold:
```
Re-queuing failed block for retry
  remote: 192.168.1.100:30333
  block_hash: abc123...
  failure_count: 1
  threshold: 3
  reason: validation_failed
```

### Info Level
When skipped blocks are retried:
```
Retrying previously skipped blocks with fresh peers
  retry_count: 3
  remaining_skipped: 2
```

## Edge Cases Handled

1. **Memory Bounds**: Both failure tracking and skipped queue are capped
   - Failure tracking: max 1000 entries
   - Skipped queue: max 100 entries

2. **Successful Import**: Clears failure counters and removes from skipped set

3. **Empty Queue**: Only retries skipped blocks when main queue is small

4. **Already Imported**: Skips retry if block was imported elsewhere

5. **Duplicate Prevention**: Uses set to prevent duplicate entries in skipped queue

## Testing

### Unit Tests
See `test_sync_skip_stuck_blocks.py` for comprehensive unit tests covering:
- Failure tracking
- Skip threshold triggering
- Queue size limits
- Retry logic
- Counter cleanup

### Integration Tests
See `test_sync_skip_stuck_blocks_integration.py` for integration tests.

## Monitoring

To monitor this feature in production:

1. Watch for warning logs with "Skipping stuck block"
2. Check info logs for "Retrying previously skipped blocks"
3. Monitor if blocks eventually succeed on retry
4. Track if specific peers consistently cause failures

## Future Improvements

Potential enhancements:
- Per-peer failure tracking to identify problematic peers
- Exponential backoff for retry intervals
- Metrics/stats for skipped blocks
- Different thresholds for different rejection reasons
