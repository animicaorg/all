# PTL Replication and Mining Fixes Implementation Summary

## What Changed

This implementation delivers reliable PTL (Pending Transaction Ledger) replication, enhanced CLI tooling, and mining stability fixes for mainnet deployment.

### 1. RPC Method Enhancements

**Canonical PTL Method Registration**
- Added `ptl.replicationStatus` as the canonical method for transaction replication status
- Maintained `tx.replicationStatus` as backward-compatible alias
- Registered `rpc.methods.ptl` module in builtin methods list

**Enhanced Response Schema**
The `ptl.replicationStatus` response now includes:
```json
{
  "tx_hash": "0x...",
  "local_status": "unknown|seen|eligible|mined|dropped",
  "peers": [
    {
      "peer_id": "peer_xxx",
      "conn_id": "optional_connection_id",
      "status": "seen|acked|missing|failed",
      "first_seen_ts": 1234567890.0,
      "last_update_ts": 1234567890.0,
      "reason": "optional rejection reason"
    }
  ],
  "quorum": {
    "required_acks": 2,
    "observed_acks": 3,
    "quorum_met": true
  },
  "persistence": {
    "stored_receipts_count": 3,
    "store_backend": "sqlite"
  },
  "mined": {
    "block_height": 12345,
    "finalized": true,
    "finalized_height": 12350
  },
  "received_at": 1234567890.0,
  "updated_at": 1234567891.0,
  "expire_at": 1234571490.0
}
```

### 2. Mining Crash Fix

**Root Cause**: `mining error: module 'rpc.deps' has no attribute 'get_state_db_adapter'`

**Fix**: Added missing functions to `rpc/deps.py`:
- `get_state_db_adapter()` - Returns state_db for transaction validation and mining
- `get_block_db()` - Returns block_db for block operations
- `register(key, obj)` - Global registry for PTL service
- `get(key, default)` - Retrieve registered components

These functions prevent AttributeError crashes in mining template builder when selecting transactions for blocks.

### 3. PTL Receipt Persistence Enhancements

**Deduplication**
- Added UNIQUE constraint on `(txid, peer_id, conn_id)` in ptl_receipts table
- Use `INSERT ... ON CONFLICT DO UPDATE` to update existing receipts instead of creating duplicates
- Handles peer reconnections and multiple connections gracefully

**Timestamps**
- `first_seen_ts` - When receipt was first recorded
- `last_update_ts` - Most recent update timestamp
- Original `timestamp` field preserved for backward compatibility

**Corruption Handling**
- New `ptl_quarantine` table for corrupted receipt data
- Safe logging and quarantine of bad receipts instead of crashing
- Operator can inspect quarantined data for debugging

**Compaction**
- `compact_receipts(older_than)` removes old receipts for finalized transactions
- Keeps most recent receipt per peer for audit trail
- Runs automatically in maintenance loop (24h+ old receipts)
- Prevents unbounded database growth

**Persistence Verified**
- Receipts survive node restart
- Loaded automatically when store initializes
- Integration tests validate restart survival

### 4. CLI Improvements

**`animica tx send --min-peers N`**
```bash
animica tx send \
  --from anim1... \
  --to anim1... \
  --value 1.0 \
  --min-peers 3 \
  --wait-timeout 30
```
- Broadcasts transaction
- Waits up to `--wait-timeout` seconds for N peer acknowledgments
- Uses canonical `ptl.replicationStatus` method
- Shows clear progress: `Acks: 2/3 (waiting...)`
- Success: `✓ Received 3 acknowledgments`
- Timeout: `Warning: Only 2/3 acks after 30.0s`

**PTL Not Enabled Error**
```
Warning: PTL not enabled on this node

To enable PTL replication:
  1. Ensure node is running with PTL service enabled
  2. Set ANIMICA_PTL_ENABLE=1 environment variable
  3. Check that the node has P2P connectivity
```

**`animica tx replicate <tx_hash> --json`**
```bash
# Human-readable output (default)
animica tx replicate 0xabc123...

# Machine-readable JSON (for scripts)
animica tx replicate 0xabc123... --json
```

JSON output is stable and suitable for parsing by monitoring tools, dashboards, and automation scripts.

**`animica tx troubleshoot <tx_hash>`**
- Enhanced diagnostic output
- Checks PTL availability first
- Shows actionable enable instructions when PTL missing
- Displays peer rejections with reasons
- Suggests specific fixes

### 5. Diagnostic Tool Updates

**`diagnose_tx_propagation.py [RPC_URL] [TX_HASH]`**
- Updated to use canonical `ptl.replicationStatus`
- Added PTL-specific health checks via `debug.ptlStats`
- New check: `check_ptl_replication(tx_hash)` for specific transaction
- Updated troubleshooting steps to include PTL enablement

Usage:
```bash
python3 diagnose_tx_propagation.py http://localhost:8545/rpc
python3 diagnose_tx_propagation.py http://localhost:8545/rpc 0xabc123...
```

### 6. Testing

**Unit Tests**
- `rpc/tests/test_ptl_rpc_registry.py` - Validates method registration
- Tests `ptl.replicationStatus` is registered
- Tests `tx.replicationStatus` backward-compat alias
- Regression test for `get_state_db_adapter` fix

**Integration Tests**
- `tests/integration/test_ptl_replication.py`
- `test_ptl_receipt_persistence_after_restart()` - Validates receipts survive restart
- `test_ptl_receipt_deduplication()` - Validates no duplicate receipts from same peer
- Existing replication tests still pass

## How to Verify

### 1. RPC Method Available
```bash
curl -X POST http://localhost:8545/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "method": "ptl.replicationStatus",
    "params": [{"txid": "0x..."}],
    "id": 1
  }'
```

Expected: Enhanced response with `quorum`, `persistence`, and `peers` fields.

### 2. Mining No Longer Crashes
Before:
```
mining error: module 'rpc.deps' has no attribute 'get_state_db_adapter'
```

After:
- Mining template builder successfully calls `deps.get_state_db_adapter()`
- Transactions selected and validated properly
- No AttributeError

### 3. Receipt Persistence
```bash
# Send transaction with min-peers
animica tx send --from anim1... --to anim1... --value 0.1 --min-peers 2

# Check replication status
animica tx replicate 0xTXHASH

# Restart node
systemctl restart animica-node

# Check replication status again - receipts should still be there
animica tx replicate 0xTXHASH
```

### 4. CLI --min-peers Works
```bash
# Send with peer acknowledgment requirement
animica tx send --from anim1alice --to anim1bob --value 1.0 --min-peers 2

# Should see:
# ✓ Transaction Sent
# Waiting for 2 peer acknowledgments...
# Acks: 1/2 (waiting...)
# Acks: 2/2 (waiting...)
# ✓ Received 2 acknowledgments
```

### 5. Run Tests
```bash
# RPC registry tests
pytest rpc/tests/test_ptl_rpc_registry.py -v

# PTL persistence tests
pytest tests/integration/test_ptl_replication.py -v

# Should see all tests pass
```

## Breaking Changes

**None** - All changes are backward compatible:
- `tx.replicationStatus` still works (delegates to `ptl.replicationStatus`)
- Existing receipt format preserved (new fields added)
- CLI commands unchanged (new flags are optional)
- Database schema upgraded automatically via `CREATE TABLE IF NOT EXISTS`

## Migration Guide

No manual migration needed. On first run with new code:
1. New database columns added automatically
2. Existing receipts preserved
3. New receipts use enhanced schema
4. Compaction runs in background

## Environment Variables

Enable PTL if not already:
```bash
export ANIMICA_PTL_ENABLE=1
export ANIMICA_P2P_ENABLE=1
export ANIMICA_P2P_TX_RELAY=true
```

## Performance Impact

- Receipt deduplication: **Reduces** database size (no duplicates)
- Receipt compaction: **Reduces** database size (old receipts pruned)
- Receipt persistence: **Negligible** (SQLite operations)
- `--min-peers` wait: **User-configurable** timeout (default 30s)

## Security Considerations

- PTL relay uses anti-spam rate limiting (existing)
- Receipt deduplication prevents peer flooding attacks
- Quarantine mechanism isolates corrupted data
- No new attack surface introduced

## Known Limitations

1. PTL must be enabled for replication features to work
2. Requires P2P connectivity for peer acknowledgments
3. Receipt compaction only runs every 30s (maintenance loop interval)
4. `--min-peers` timeout is fixed per command (not adaptive)

## Future Enhancements

- Adaptive timeout based on network conditions
- Receipt compression for long-term storage
- Multi-connection tracking per peer
- Receipt merkle proofs for light clients
