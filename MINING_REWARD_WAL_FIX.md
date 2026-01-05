# Mining Reward Balance Fix - WAL Checkpoint Issue

## Problem Statement

Users reported that after successfully mining blocks (with rewards appearing in mining output), the `animica wallet show` command displayed a balance of 0.

**Example:**
```bash
$ animica miner mine-blocks --address anim1zqp2pg8s9... --count 5
# Output: 5 blocks mined, 25 ANM total reward

$ animica wallet show temple
# Result: balance = 0 (incorrect!)
```

## Root Cause

The issue was caused by **SQLite Write-Ahead Logging (WAL) buffering**:

1. StateDB uses SQLite with these pragmas:
   - `journal_mode=WAL`  (Write-Ahead Logging for better concurrency)
   - `synchronous=NORMAL` (Balance between durability and performance)

2. With these settings, writes are buffered in the WAL file and may not be immediately visible to other readers until a checkpoint occurs.

3. When mining a block:
   - Block rewards are credited → written to WAL
   - Balance query immediately after → reads from main database (stale)
   - Result: Balance appears as 0 even though reward was credited

## Solution

Force a WAL checkpoint immediately after each mined block is accepted:

```python
# In rpc/methods/miner.py, after block acceptance:
if hasattr(ctx.state_db, "kv") and hasattr(ctx.state_db.kv, "_conn"):
    ctx.state_db.kv._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
```

### Why PASSIVE mode?

- `PASSIVE`: Checkpoint as much as possible without blocking other connections
- Does not block readers or writers
- Suitable for ensuring immediate visibility without impacting performance
- Alternative modes (FULL, RESTART, TRUNCATE) would block and hurt concurrency

## Implementation Details

### Modified Files

**rpc/methods/miner.py:**
- Added WAL checkpoint after block acceptance (line ~3256)
- Includes error handling in case checkpoint fails
- Only executes if StateDB uses SQLite backend

**rpc/tests/test_mining_wal_checkpoint.py:**
- New test: `test_mining_rewards_immediately_visible`
- New test: `test_multiple_rapid_mining_sessions`
- Verifies rewards are visible without delays

### Code Changes

```python
if accepted:
    # ... existing code ...
    
    # Force WAL checkpoint to ensure writes are visible
    try:
        if hasattr(ctx.state_db, "kv") and hasattr(ctx.state_db.kv, "_conn"):
            ctx.state_db.kv._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            log.debug(f"WAL checkpoint executed after block {header.height}")
    except Exception as e:
        log.debug(f"WAL checkpoint failed (may not be needed): {e}")
```

## Testing

### Automated Tests

Run the new test suite:
```bash
pytest rpc/tests/test_mining_wal_checkpoint.py -v
```

### Manual Testing

1. Start node:
   ```bash
   animica node start
   ```

2. Create a test wallet:
   ```bash
   animica wallet create --label test
   ```

3. Mine blocks to the wallet:
   ```bash
   animica miner mine-blocks --address <address> --count 5
   ```

4. Check balance immediately:
   ```bash
   animica wallet show test
   ```

5. Expected: Balance should show the total mined rewards (e.g., 25 ANM)

## Performance Impact

- **Minimal**: PASSIVE checkpoint is non-blocking
- Checkpoint only affects the mining thread, not balance queries
- WAL checkpoints would happen eventually anyway (automatic checkpointing)
- This just ensures they happen at a predictable time (after mining)

## Alternative Solutions Considered

1. **Use synchronous=FULL**: Would ensure immediate durability but hurt performance significantly
2. **Disable WAL mode**: Would lose concurrency benefits
3. **Add delays before queries**: User-facing workaround, not a real fix
4. **Shared memory for state**: Complex architectural change

The WAL checkpoint solution is the best balance of correctness, performance, and simplicity.

## Related Issues

- Original fix for address parsing: #[previous PR]
- Mining reward crediting flow: `rpc/methods/miner.py:_apply_block_reward()`
- State persistence: `core/db/state_db.py`

## Verification

To verify this fix resolved the issue:

1. The mining output should show rewards credited
2. `animica wallet show` should immediately reflect the new balance
3. No need to restart the node or wait for delays
4. Balance queries from any connection should see the latest state

## Notes

- This fix applies to all mining methods (CLI, RPC, pool)
- Also fixes similar issues with transaction execution state not being immediately visible
- The checkpoint is idempotent and safe to call multiple times
- Future work: Consider batching checkpoints for high-frequency mining
