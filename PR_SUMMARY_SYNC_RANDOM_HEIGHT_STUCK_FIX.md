# Fix Summary: Node Syncing Stuck at Random Heights

## Problem Statement
Blockchain nodes were getting stuck at random heights during synchronization, requiring manual intervention or long timeouts (15+ seconds) to recover.

## Root Cause Analysis

### The Bug
Located in `p2p/node/p2p_service.py`, function `_queue_block_requests()` (lines 8775-8802):

1. **Premature Inflight Marking**: Blocks were marked as "inflight" (lines 8764-8766) **before** the network send operation
2. **Silent Failure Suppression**: The `_send()` call was wrapped in `contextlib.suppress(Exception)`, hiding all errors
3. **Stuck State**: When send failed (peer disconnect, network error), blocks remained in inflight state
4. **Sync Blocking**: Blocks in inflight state were skipped during scheduling (line 8989), preventing retry
5. **Timeout Dependency**: Recovery only occurred after 15-second timeout via `_expire_inflight_blocks()`

### Why Random Heights?
The issue manifested at "random" heights because:
- It depended on which peer happened to disconnect during a send operation
- Network errors occur unpredictably
- Different peers serve different height ranges

### Code Flow Before Fix
```
1. Mark blocks as inflight (h1, h2, h3)
2. Try to send request to peer
3. Send fails (peer disconnected) → exception suppressed silently
4. Blocks remain in inflight state
5. Scheduler sees blocks inflight, skips them
6. Sync stuck waiting for blocks that were never sent
7. After 15s: timeout expires, blocks re-queued
8. Sync resumes (but 15s delay per failure)
```

## The Fix

### Changes Made
**File**: `p2p/node/p2p_service.py`
**Function**: `_queue_block_requests()`
**Lines**: 8775-8833

### Key Modifications

1. **Track Success Explicitly**
   ```python
   successfully_sent: List[bytes] = []
   ```
   - Only blocks that were actually sent are counted

2. **Proper Exception Handling**
   ```python
   try:
       await self._send(peer, MsgID.GET_BLOCKS, GetBlocks(...))
       successfully_sent.extend(chunk)
   except Exception as e:
       # Handle failure...
   ```
   - Replaced `contextlib.suppress(Exception)` with explicit try/except
   - Added to `successfully_sent` only on success

3. **Immediate Cleanup on Failure**
   ```python
   for h in chunk:
       self._sync_inflight_blocks.pop(h, None)
       self._sync_inflight_peers.pop(h, None)
       self._sync_inflight_block_requests.pop(h, None)
   ```
   - Remove failed blocks from inflight tracking immediately
   - Prevents blocks from being stuck in limbo

4. **Immediate Re-Queue**
   ```python
   if h not in self._sync_block_queue_set and not self._has_block(h):
       self._sync_block_queue.appendleft(h)
       self._sync_block_queue_set.add(h)
   ```
   - Failed blocks added back to front of queue for immediate retry
   - Race condition check: don't re-queue if block arrived from another peer

5. **Error Visibility**
   ```python
   log.warning(
       "Failed to send block request - re-queuing for retry",
       extra={"remote": peer.remote, "chunk_size": len(chunk), "error": str(e)}
   )
   ```
   - Failed sends now logged with context
   - Helps diagnose network/peer issues

6. **Accurate Statistics**
   ```python
   sent_count = len(successfully_sent)
   self._stats["blocks_requested"] += sent_count
   self._stats["blocks_req_sent"] += sent_count
   ```
   - Stats only count actually sent blocks
   - Provides accurate metrics for monitoring

### Code Flow After Fix
```
1. Mark blocks as inflight (h1, h2, h3)
2. Try to send request to peer
3. Send fails (peer disconnected) → exception caught
4. Remove h1, h2, h3 from inflight immediately
5. Re-queue h1, h2, h3 at front of queue
6. Log warning with error details
7. Next scheduler iteration picks up h1, h2, h3
8. Try with different peer or same peer (immediate retry)
9. Sync continues without 15s delay
```

## Testing

### Unit Tests
**File**: `p2p/tests/test_block_request_send_failure.py`

Two focused tests:
1. `test_block_request_send_failure_handling()` - Validates full cleanup and re-queue flow
2. `test_partial_send_failure()` - Validates mixed success/failure scenarios

**Results**: ✅ All tests pass

### Validation
- ✅ Python syntax check passes
- ✅ Module imports successfully
- ✅ Code review completed with feedback addressed
- ✅ CodeQL security scan (no issues found)

## Impact Analysis

### Performance
- **Before**: 15-second delay per send failure
- **After**: Immediate retry (milliseconds)
- **Improvement**: ~99.9% faster recovery

### Reliability
- **Before**: Random height stalls requiring manual intervention
- **After**: Automatic recovery from send failures
- **Result**: Eliminates class of sync stuck issues

### Observability
- **Before**: Silent failures, no visibility
- **After**: Warning logs with error context
- **Result**: Can diagnose peer/network issues

### Pattern Consistency
- Headers already had proper error handling (lines 8127-8155)
- Blocks now follow the same pattern
- Result: Consistent error handling across sync code

## Code Review Feedback Addressed

1. **Removed unused imports**: asyncio, AsyncMock, MagicMock, patch, typing.List
2. **Performance improvement**: Changed `list.insert(0)` to `deque.appendleft()` for O(1) operation
3. **Clarifying comment**: Added explanation for `_has_block()` race condition check

## Backwards Compatibility

- ✅ No protocol changes
- ✅ No RPC/API changes
- ✅ No configuration changes required
- ✅ No database schema changes
- ✅ Only affects error handling path
- ✅ Normal sync behavior unchanged

## Deployment

### Risk Assessment
- **Risk Level**: Low
- **Change Scope**: Single function error handling
- **Rollback**: Simple (revert commits)
- **Dependencies**: None

### Monitoring Points
After deployment, watch for:
- Reduction in "sync stuck" reports
- Warning logs for "Failed to send block request"
- Faster sync-to-tip times
- Fewer 15-second pauses during sync

### Success Metrics
- Number of nodes stuck at random heights → Should approach zero
- Average sync completion time → Should decrease
- Block request retry rate → Should remain low (most sends succeed)

## Related Fixes

This fix complements existing sync improvements:
- `PR_SUMMARY_SYNC_BLOCK_EXPIRY_FIX.md` - Added `_expire_inflight_blocks()` to sync loop
- `PR_SUMMARY_SYNC_SKIP_STUCK_BLOCKS.md` - Skip blocks after repeated failures
- `PR_SUMMARY_SYNC_STUCK_FIX.md` - Multi-peer retry for headers

Together, these create a robust sync system that handles:
- Peer failures (this fix)
- Network timeouts (expiry fix)
- Invalid blocks (skip fix)
- Stale peer state (stuck fix)

## Conclusion

This minimal, surgical fix eliminates a critical class of sync failures by properly handling network send errors. The fix:
- Addresses the root cause directly
- Follows established patterns
- Has comprehensive test coverage
- Provides better observability
- Is fully backwards compatible
- Ready for immediate deployment

**Status**: ✅ Ready for merge and deployment
