# Transaction Inclusion Fix - Test Plan

## Overview
This document describes how to test the fix for transactions not being included in mined blocks.

## Problem Fixed
- Transactions submitted to mempool were not being included in mined blocks
- Miner produced empty blocks with only mining rewards
- Pending transactions stayed in mempool indefinitely

## Root Causes Fixed
1. **Silent failures in Tx construction**: Added exception handling and logging
2. **Incorrect gas_limit access**: Fixed to use `tx.unsigned.gas_limit` instead of flat access
3. **Lack of visibility**: Added comprehensive logging throughout transaction retrieval flow

## Manual Testing

### Prerequisites
1. Start the Animica RPC server: `animica serve --dev`
2. Have the Animica CLI installed: `pip install -e .`

### Test Steps

1. **Check initial state**
   ```bash
   animica chain get-height
   animica mempool list
   ```
   
2. **Generate test keypair and fund it**
   ```bash
   # Mine 5 blocks to a test address to fund it
   animica miner mine-blocks --count 5 --address <test-address>
   
   # Verify balance
   animica state get-balance <test-address>
   ```

3. **Submit a transfer transaction**
   ```bash
   # Use the CLI to send a transaction
   animica tx send --from <funded-address> --to <recipient-address> --amount 1000000000 --gas-limit 21000
   ```

4. **Verify transaction is in mempool**
   ```bash
   animica mempool list
   # Should show 1 pending transaction
   ```

5. **Mine a block**
   ```bash
   animica miner mine-blocks --count 1 --address <payout-address>
   ```

6. **Verify transaction was included**
   ```bash
   # Check mempool is empty (transaction was included)
   animica mempool list
   # Should show 0 pending transactions
   
   # Check balances updated
   animica state get-balance <recipient-address>
   # Should show the transferred amount
   ```

7. **Check logs for debugging output**
   - Look for log messages like:
     - `"drain_fn called with max_gas=..., pending_count=..."`
     - `"MinerFeedAdapter.peek_ready called with limit=..."`
     - `"_mine_once: adapter.get_mempool_snapshot returned N transactions"`
     - `"Retrieved N transactions from mempool adapter for mining"`
   - If transactions are not being included, the logs will show exactly where the flow fails

## Automated Testing

### Run Existing Integration Test
```bash
pytest rpc/tests/test_mining_mempool_integration.py::test_mining_includes_tx_and_updates_balances -v -s
```

This test validates:
- Transaction submission via RPC
- Transaction appears in mempool
- Mining includes the transaction
- Balances are updated correctly
- Nonces are incremented
- Transaction is removed from mempool

### Run All Mining Tests
```bash
pytest rpc/tests/test_mining_*.py -v
```

## Expected Results

### Before Fix
- ❌ Transaction stays in mempool after mining
- ❌ Block contains 0 transactions (only mining reward)
- ❌ Recipient balance remains 0
- ❌ Sender nonce stays at 0
- ❌ Silent failures with no useful error messages

### After Fix
- ✅ Transaction is included in mined block
- ✅ Transaction is removed from mempool
- ✅ Recipient balance increases by transfer amount
- ✅ Sender balance decreases by transfer amount + fees, increases by mining reward
- ✅ Sender nonce increments to 1
- ✅ Detailed logging shows transaction flow through the system

## Debugging with Logs

If transactions are still not being included after the fix, check the logs for:

1. **drain_fn logs** (in `rpc/methods/miner.py`):
   - `"drain_fn called with max_gas=..., pending_count=X"` - How many txs in fallback cache?
   - `"drain_fn: Processing tx 0x..."` - Is each tx being processed?
   - `"drain_fn: Decoded tx 0x..., type=..."` - Did decoding succeed?
   - `"drain_fn: Failed to construct Tx from dict"` - Construction failure?
   - `"drain_fn returning N transactions"` - How many txs returned?

2. **MinerFeedAdapter logs**:
   - `"MinerFeedAdapter.peek_ready called"` - Is the adapter being invoked?
   - `"MinerFeedAdapter.peek_ready: next_batch returned N transactions"` - Did batch retrieval work?

3. **_mine_once logs**:
   - `"_mine_once: adapter.get_mempool_snapshot returned N transactions"` - Did adapter return txs?
   - `"Retrieved N transactions from mempool adapter"` - Were txs successfully retrieved?
   - `"Attempting to retrieve N transactions from fallback pending cache"` - Fallback path used?

4. **Exception logs**:
   - Look for any warnings or errors with stack traces
   - `"_construct_tx_from_dict: Tx.from_obj failed"` - Tx construction errors
   - `"drain_fn: Failed to decode tx"` - CBOR decoding errors

## Success Criteria

The fix is successful if:
1. All manual test steps pass
2. Integration test `test_mining_includes_tx_and_updates_balances` passes
3. Logs show transactions flowing through the system correctly
4. No silent failures (all errors are logged with stack traces)

## Rollback Plan

If the fix causes regressions:
1. Revert commits: `bba0563`, `31dddb2`, `c38dcc4`
2. The original issue will return, but no new issues should be introduced
3. The extensive logging can still be kept for debugging purposes

## Notes

- The fix is backward compatible - it doesn't change any RPC APIs or data formats
- The fix only affects internal transaction retrieval logic
- The logging can be reduced in production by adjusting log levels (set to INFO or WARNING)
- The gas_limit helper function is reusable for other parts of the codebase if needed
