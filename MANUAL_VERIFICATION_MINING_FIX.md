# Manual Verification Guide: Mining Rewards Fix

## Overview

This guide provides steps to manually verify that the mining rewards fix correctly credits rewards to miners' balances.

## What Was Fixed

**Problem**: The `_mine_once()` function in `rpc/methods/miner.py` was bypassing the block importer and directly calling `append_canonical_block()`, which only stored the block but never applied state changes (including mining rewards).

**Fix**: Changed `_mine_once()` to use the proper `importer.import_block(block)` call, which:
1. Validates the block
2. Stores it in the database  
3. Applies state changes including rewards via `_apply_block_state()`
4. Credits mining rewards to the miner's balance

## Verification Steps

### Step 1: Setup Test Environment

```bash
# Start a clean devnet node
cd /home/runner/work/all/all
source .venv/bin/activate

# Initialize fresh chain data (optional environment variables)
rm -rf /tmp/animica-test-mining
export ANIMICA_DATA_DIR=/tmp/animica-test-mining
# Note: ANIMICA_CHAIN_ID and ANIMICA_NETWORK may vary based on your setup
# Adjust these values according to your node configuration

# Start the node (use the actual command for your setup)
# The exact command may vary - check README.md for details
animica node start --data-dir /tmp/animica-test-mining &
NODE_PID=$!
sleep 5  # Wait for node to start
```

### Step 2: Check Initial Balance

```bash
# Check premine wallet balance (should be 81M ANM)
animica wallet show premine

# Expected output:
# Address: anim1...
# Balance: 81000000.000000000 ANM
# ...
```

### Step 3: Mine Blocks

```bash
# Mine 5 blocks to premine wallet
animica miner mine-blocks --address premine --count 5 --verbose

# With the fix, you should see output like:
#   FOUND: Block 1/5 PoW (height: 1, nonce: 12345, hash: 0xabc...)
#   Block imported successfully via block importer at height 1
#   ACCEPTED: Block mined and reward credited | height=1 | ...
#
# Without the fix, you would see:
#   Block persisted via append_canonical_block at height 1
#   (no state application, no reward)
```

### Step 4: Verify Balance Increased

```bash
# Check balance again
animica wallet show premine

# Expected balance: 81000000 + (5 blocks × 300 ANM) = 81000000 + 1500 = 81001500 ANM
# Balance: 81001500.000000000 ANM

# Calculate the increase
# Initial: 81000000 ANM
# Expected after 5 blocks: 81001500 ANM (assuming 300 ANM per block)
# Increase: 1500 ANM ✅
```

### Step 5: Check Mining Audit Trail

```bash
# Query mining credits (if implemented)
animica miner credits --address premine --last 10

# Should show all 5 mined blocks with:
# - Expected reward: 300 ANM (300000000000 nANM)
# - Balance after: increasing by 300 ANM each block
# - No warnings about zero balance
```

### Step 6: Check Logs

```bash
# Check node logs for success messages
grep "Block imported successfully via block importer" /tmp/animica-test-mining/node.log

# Should show 5 lines, one for each block:
# Block imported successfully via block importer at height 1
# Block imported successfully via block importer at height 2
# ...

# Check for invariant violations (should be NONE)
grep "INVARIANT VIOLATION\|ORPHANED?" /tmp/animica-test-mining/node.log

# Should return empty (no violations)
```

### Step 7: Verify State Persistence

```bash
# Stop the node
kill $NODE_PID
wait $NODE_PID

# Restart the node (use same command as before)
animica node start --data-dir /tmp/animica-test-mining &
NODE_PID=$!
sleep 5

# Check balance is still correct after restart
animica wallet show premine

# Balance should still be 81001500 ANM
# This verifies state was properly persisted
```

### Step 8: Cleanup

```bash
# Stop node
kill $NODE_PID
wait $NODE_PID

# Remove test data
rm -rf /tmp/animica-test-mining
```

## Expected vs Buggy Behavior

### With Fix (Expected) ✅

```
1. Mine block → Block imported via block importer
2. Block importer calls _apply_block_state()
3. _apply_block_state() calls _apply_block_reward()
4. Reward credited to miner balance
5. Balance increases by expected amount
6. Invariant check passes (balance > 0)
7. Audit trail shows correct credited amount
```

### Without Fix (Buggy) ❌

```
1. Mine block → Block stored via append_canonical_block
2. State application SKIPPED
3. Reward NOT credited
4. Balance UNCHANGED
5. Invariant check fails (balance = 0 with reward > 0)
6. Audit trail would show discrepancy (if it ran)
```

## Key Log Messages to Look For

### Success Indicators ✅
- `"Block imported successfully via block importer at height X"`
- `"ACCEPTED: Block mined and reward credited"`
- `"state: block execution completed successfully"`
- Balance increases match expected rewards

### Failure Indicators ❌
- `"Block persisted via append_canonical_block"` (old behavior)
- `"INVARIANT VIOLATION: Block reward not credited"`
- `"⚠️ ORPHANED?: Block reward not credited"`
- Balance does not increase after mining

## Troubleshooting

### Balance Not Increasing

**Possible Causes**:
1. Node using old code (not updated)
2. Block import failing for other reasons
3. Genesis params incorrect (zero rewards)

**Debug Steps**:
```bash
# Check if fix is present
grep -n "Block imported successfully via block importer" rpc/methods/miner.py

# Should find the line in _mine_once around line 3520

# Check block import logs
grep "Block import" /tmp/animica-test-mining/node.log -A5

# Check consensus reward calculation
python -c "
from consensus.rewards import compute_block_reward
rewards = compute_block_reward(chain_id=1337, height=1, params={})
print(f'Reward at height 1: {rewards}')
"
```

### Invariant Violations

If you see `"INVARIANT VIOLATION"` messages:
1. This indicates state was applied but balance check failed
2. Could be due to:
   - State not flushed before check
   - Parallel mining conflicts
   - Reorg during mining
3. Check if balance actually increased despite warning

## Summary

The fix ensures that:
1. ✅ Block importer is used for all mined blocks
2. ✅ State changes (including rewards) are applied
3. ✅ Balances are correctly updated
4. ✅ Invariant checks can verify proper crediting
5. ✅ Audit trail reflects actual credited amounts

If all verification steps pass, the fix is working correctly!
