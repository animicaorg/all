# Explorer2 Block Display Fix - Quick Reference

## Issue
Explorer2 showed only block 0 despite the chain having progressed beyond genesis.

## Fix Summary
Fixed RPC head recovery to scan for the actual chain tip instead of always returning block 0 when the head pointer is missing.

## Testing Instructions

### Prerequisites
- Running Animica node with RPC enabled
- Chain database with multiple blocks (not just genesis)
- Node configured with accessible RPC endpoint

### Test Scenario 1: Normal Operation (Verify No Regression)
```bash
# Query the head
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'

# Expected: Returns current chain tip (e.g., height 1000+)
# Log: No scanner messages (normal operation)
```

### Test Scenario 2: Missing Head Pointer (Main Fix)
**Note**: This requires access to the node's database. DO NOT run on production.

```bash
# 1. Stop the node
systemctl stop animica  # or equivalent

# 2. Manually corrupt/remove the head pointer in the database
# (specific method depends on database implementation)

# 3. Restart the node
systemctl start animica

# 4. Query the head
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'

# Expected: Returns actual chain tip (e.g., height 1000+), NOT block 0
# Log should show: "Recovered head at height XXXX via index scan"
```

### Test Scenario 3: Explorer2 Web UI
```bash
# 1. Open Explorer2 in browser
open http://localhost:3001  # or your explorer URL

# 2. Check home page
# Expected: "Current Block" shows actual height (not #0)

# 3. Navigate to Blocks page
# Expected: Shows all recent blocks, not just genesis

# 4. Try pagination
# Expected: Can page through blocks beyond block 0
```

### Test Scenario 4: Performance Check
```bash
# Monitor logs during scanner invocation
tail -f /path/to/animica/logs/*.log | grep "animica.rpc"

# Look for timing indicators in scanner messages
# Expected log sequence:
#   "read_head() failed: ..., will try fallback methods"
#   "Recovered head at height XXXX via [method]"
#
# Methods (in order of preference):
#   - "index scan" (fastest, O(k) or O(1) with reverse)
#   - "exponential search" (fallback, O(log n))
#   - If neither works, falls back to block 0
```

## Expected Results

### Success Indicators ✅
- [ ] `chain.getHead` returns actual chain tip (not 0)
- [ ] Explorer2 home page shows correct current block
- [ ] Explorer2 blocks page lists all blocks
- [ ] Pagination works correctly
- [ ] Logs show successful head recovery method
- [ ] Performance is acceptable (<1 second for recovery)

### Failure Indicators ❌
- [ ] Still shows only block 0
- [ ] Explorer2 shows "No blocks found" (except for empty chain)
- [ ] Errors in logs about scanner failures
- [ ] Extremely slow response times

## Troubleshooting

### If still showing only block 0:
1. Check logs for "read_head() failed" message
2. Check logs for scanner invocation
3. Verify blocks exist in database:
   ```bash
   # Query specific block
   curl -X POST http://127.0.0.1:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"chain.getBlockByNumber","params":[10, false, false]}'
   ```
4. If blocks exist but scanner fails, check database integrity

### If performance is slow:
1. Check which scanner method was used (logs)
2. If "exponential search" is slow, database may have issues
3. If "index scan" is slow, check if reverse iteration is supported
4. Consider adjusting `MAX_LINEAR_SCAN_HEIGHT` if needed

### If getting errors:
1. Check Python logs for exception details
2. Verify database permissions
3. Check that `core.db.block_db` module is available
4. Ensure `PFX_HIX` constant is correct for your DB version

## Rollback Instructions

If issues occur after deployment:

```bash
# 1. Revert the changes
git revert <commit-hash>

# 2. Rebuild/restart
# (deployment-specific commands)

# 3. Known limitation after rollback
# - Explorer2 will again show only block 0 when head pointer is missing
# - This is the previous known issue, but it's safe
```

## Configuration

### Adjusting Scan Limits
If your chain exceeds 10,000 blocks, you can increase the limit:

```python
# In rpc/methods/chain.py
MAX_LINEAR_SCAN_HEIGHT = 50000  # Increase as needed
```

### Disabling Specific Scan Methods
For debugging, you can disable specific methods by modifying `_scan_for_highest_block()`:

```python
# Disable reverse iteration
# Comment out: if iter_method and callable(iter_method):

# Disable forward iteration
# Set: if kv is not None and False:

# Disable exponential search
# Comment out the exponential search section
```

## Monitoring

### Key Log Messages

**Success Messages:**
```
Recovered head at height 1234 via index scan
Recovered head at height 1234 via exponential search
```

**Warning Messages:**
```
read_head() failed: ..., will try fallback methods
Index scan failed: ..., trying exponential search
```

**Error Messages:**
```
Failed to scan for highest block: ...
block_db not available, cannot scan for highest block
```

### Metrics to Monitor
- RPC response time for `chain.getHead`
- Frequency of scanner invocations
- Success rate of each scanner method
- Time to complete each scan method

## Related Documentation
- Full technical analysis: `EXPLORER2_BLOCK_DISPLAY_FIX.md`
- RPC methods: `rpc/methods/chain.py`
- Head accessor: `rpc/deps.py`
- Core head logic: `core/chain/head.py`

## Support
If you encounter issues:
1. Check logs for detailed error messages
2. Verify the test scenarios above
3. Review the technical documentation
4. Contact the development team with:
   - Log excerpts (especially scanner messages)
   - Chain database stats (block count, size)
   - RPC query responses
   - Explorer2 behavior description
