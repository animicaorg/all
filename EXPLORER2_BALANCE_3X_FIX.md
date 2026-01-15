# Explorer2 Showing 3x Balance - Issue and Fix

## Problem Statement

Users report that explorer2 is showing exactly 3 times as much balance as what they see in their wallets.

## Root Causes

There are TWO separate issues that can cause balance discrepancies:

### Issue #1: State Database Inflation (Primary Issue)

The state database contains **inflated balances** due to the state rebuild bug, causing balances to be 2x, 3x, 4x, or more of the correct value.

#### State Rebuild Bug

When the node experiences certain conditions that require rebuilding state from genesis:
1. Node mines blocks and credits rewards to addresses → balances are correct ✅
2. A reorg or other event triggers `_rebuild_state_from_canonical()`
3. The rebuild function re-executes ALL blocks from genesis
4. **Each block's coinbase transactions execute AGAIN** → rewards are re-applied ❌
5. Balance = original + (number_of_rebuilds × rewards)

If a user's node has rebuilt state 2 times:
- Original mining: 1x rewards ✅
- Rebuild #1: +1x rewards (total 2x) ❌
- Rebuild #2: +1x rewards (total 3x) ❌

### Issue #2: Wallet Extension Decimal Misconfiguration

The wallet extension was configured to use **18 decimals** (like Ethereum) instead of **9 decimals** (actual ANM).

- **ANM uses**: 1 ANM = 1,000,000,000 nANM (10^9 = 9 decimals)
- **Wallet was using**: 10^18 decimals (Ethereum standard)
- **Effect**: If wallet JS code uses this decimal config, it would display balances 10^9 times smaller than correct

**Note**: This has been fixed in this PR by changing `currencyDecimals` from 18 to 9 in `wallet-extension/src/background/network/networks.ts`.

### Why Explorer2 Shows Higher Values

The most likely scenario for the "3x" issue:

- **State DB**: Contains 3x inflated balances (due to 2 rebuilds)
- **Explorer2**: Queries `state.getBalance` RPC → displays 3x (correct read of wrong data)
- **Wallets**: May be:
  - Connected to a different node with correct balances
  - Using cached values from before the inflation
  - Recently restarted and showing updated values

## Solution

The fix involves TWO steps:

### Step 0: Fix Wallet Decimal Configuration (Done in this PR)

Changed `currencyDecimals` from 18 to 9 in:
- `wallet-extension/src/background/network/networks.ts`

This ensures the wallet extension displays balances correctly. Users will need to:
1. Reload the extension
2. Refresh their balance

### Step 1: Diagnose the State DB Inflation

Run the diagnostic script to confirm the 3x inflation:

```bash
cd /home/runner/work/all/all

# Check a specific address
python tools/diagnose_balance_3x_issue.py \
  --rpc http://localhost:8545/rpc \
  --address anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4

# Or check via RPC directly
python -c "
import requests
result = requests.post('http://localhost:8545/rpc', json={
    'jsonrpc': '2.0',
    'id': 1,
    'method': 'state.detectBalanceInflation',
    'params': [100]  # Check top 100 accounts
}).json()
print(result)
"
```

**Expected output** (if inflated):
```
⚠ INFLATION DETECTED: 3x inflated (actually ~10000 blocks)
  → Current balance:   150,000.000000000 ANM
  → Corrected balance:  50,000.000000000 ANM
```

### Step 2: Backup State Database

**CRITICAL: Always backup before correction!**

```bash
# Find your state DB
ls ~/.animica/chain-*/

# Backup the state database
cp ~/.animica/chain-1/state.db ~/.animica/chain-1/state.db.backup.$(date +%Y%m%d_%H%M%S)
cp ~/.animica/chain-1/animica.db ~/.animica/chain-1/animica.db.backup.$(date +%Y%m%d_%H%M%S)
```

### Step 3: Correct the Balances

#### Option A: Via RPC (Recommended)

Stop the node and run the correction via RPC:

```bash
# 1. Stop the node
pkill -f "animica.*node"

# 2. Start node with RPC enabled
cd /home/runner/work/all/all
python -m python.animica.node --rpc-port 8545 &

# Wait for node to start
sleep 5

# 3. Run correction (DRY RUN first)
python -c "
import requests
result = requests.post('http://localhost:8545/rpc', json={
    'jsonrpc': '2.0',
    'id': 1,
    'method': 'state.correctBalanceInflation',
    'params': [True]  # dry_run=True
}).json()
print('Dry run result:', result)
"

# 4. Apply corrections
python -c "
import requests
result = requests.post('http://localhost:8545/rpc', json={
    'jsonrpc': '2.0',
    'id': 1,
    'method': 'state.correctBalanceInflation',
    'params': [False]  # dry_run=False - APPLY CHANGES
}).json()
print('Correction result:', result)
"

# 5. Restart node
pkill -f "animica.*node"
python -m python.animica.node --rpc-port 8545 &
```

#### Option B: Via Python Script

```bash
cd /home/runner/work/all/all

# Dry run first
python tools/correct_balance_inflation.py \
  --rpc http://localhost:8545/rpc \
  --db-path ~/.animica/chain-1/state.db \
  --dry-run

# Review the output, then apply
python tools/correct_balance_inflation.py \
  --rpc http://localhost:8545/rpc \
  --db-path ~/.animica/chain-1/state.db \
  --apply
```

### Step 4: Verify the Fix

After correction, verify that explorer2 shows the correct balances:

```bash
# Check the same address again
python tools/diagnose_balance_3x_issue.py \
  --rpc http://localhost:8545/rpc \
  --address anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4
```

**Expected output** (after fix):
```
✓ No inflation detected: Normal (~10000 blocks mined)

CONCLUSION:
  The balance appears normal.
```

### Step 5: Verify Explorer2

1. Open explorer2 in browser: `http://localhost:3001`
2. Navigate to the address page
3. Verify the balance matches the corrected value

## Prevention

The state rebuild inflation bug has been fixed in recent versions by:
1. Adding state height tracking in `core/db/state_db.py`
2. Preventing unnecessary rebuilds in `core/chain/block_import.py`
3. Adding `get_state_height()` and `set_state_height()` methods

**To prevent future inflation:**
- Keep your node updated to the latest version
- Ensure the state rebuild prevention fix is applied
- Monitor for inflation using `state.detectBalanceInflation` RPC method

## Technical Details

### Balance Storage

- **Unit**: nANM (nano-ANM)
- **Conversion**: 1 ANM = 1,000,000,000 nANM (10^9)
- **Block Reward**: 5 ANM = 5,000,000,000 nANM

### Inflation Detection Logic

The `_detect_inflation_factor()` function in `rpc/methods/state.py` detects inflation by:

1. Checking if balance is a multiple of block reward (5 ANM)
2. Calculating number of blocks: `blocks = balance / 5_000_000_000`
3. If blocks > 10,000 and divisible by 2-10, flagging as potentially inflated
4. The smallest factor that makes the balance "reasonable" is the inflation factor

Example:
- Current: 150,000 ANM = 30,000 blocks
- 30,000 / 2 = 15,000 blocks ✓ (still high)
- 30,000 / 3 = 10,000 blocks ✓ (reasonable) → **3x inflation detected**

### RPC Methods Involved

- `state.getBalance` - Query balance (all aliases return same value)
- `animica_getBalance` - Alias used by wallet extension
- `eth_getBalance` - Ethereum compatibility alias
- `state.detectBalanceInflation` - Scan for inflated accounts
- `state.correctBalanceInflation` - Apply corrections

## References

- `BALANCE_INFLATION_FIX_COMPLETE.md` - Original bug fix documentation
- `rpc/methods/state.py` - RPC implementation with inflation detection
- `tools/check_balance_inflation.py` - Detection tool
- `tools/correct_balance_inflation.py` - Correction tool
- `tools/diagnose_balance_3x_issue.py` - New diagnostic tool (this PR)

## Summary

**The problem**: State DB contains 3x inflated balances due to state rebuilds
**The fix**: Run balance correction tool to divide inflated balances by 3
**Explorer2**: No code changes needed - it's displaying data correctly
