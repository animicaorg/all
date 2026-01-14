# Balance Disparity Fix - Verification Guide

## Problem Summary

**Issue**: Wallet extension showed different balance than the explorer for the same address.

**Root Cause**: 
- Wallet extension calls `animica_getBalance` RPC method
- Explorer calls `state.getBalance` RPC method  
- RPC server only had `state.getBalance` registered
- No alias existed, so `animica_getBalance` would fail or return default value

**Fix**: Added method aliases so all these names point to the same function:
- `state.getBalance` (original, used by explorer)
- `animica_getBalance` (used by wallet extension)
- `eth_getBalance` (Ethereum compatibility)
- `state_getBalance` (snake_case variant)

## Automated Verification

### Method Registration Check

```bash
cd /home/runner/work/all/all

python3 -c "
from rpc.methods import ensure_loaded, get_methods

ensure_loaded()
methods = get_methods()

# Check if our aliases are registered
required = ['state.getBalance', 'animica_getBalance', 'eth_getBalance', 'state_getBalance']
for method in required:
    if method in methods:
        print(f'✓ {method} is registered')
    else:
        print(f'✗ {method} is NOT registered')

# Verify all point to same function
functions = {name: methods[name].func for name in required if name in methods}
unique_funcs = set(functions.values())

if len(unique_funcs) == 1:
    print('\n✓ All balance methods are aliases to the same function')
else:
    print('\n✗ Balance methods point to different functions!')
"
```

**Expected output:**
```
✓ state.getBalance is registered
✓ animica_getBalance is registered
✓ eth_getBalance is registered
✓ state_getBalance is registered

✓ All balance methods are aliases to the same function
```

### Unit Test

Run the dedicated test suite:

```bash
cd /home/runner/work/all/all

# If pytest is available
pytest rpc/tests/test_balance_method_aliases.py -v

# Manual run if pytest not available
python3 rpc/tests/test_balance_method_aliases.py
```

## Manual End-to-End Verification

### Prerequisites

1. **Start a local node**:
   ```bash
   cd /home/runner/work/all/all
   # Start node with RPC on port 8545
   python -m python.animica.node --rpc-port 8545
   ```

2. **Create a test account** (if needed):
   ```bash
   animica wallet create test-wallet
   ```

3. **Fund the account** (mining or faucet):
   ```bash
   # Mine some blocks to the wallet address
   animica mine --address <wallet-address> --blocks 10
   ```

### Step 1: Check Balance via Direct RPC

Test all method aliases return the same value:

```bash
TEST_ADDR="anim1..." # Replace with your test address

# Method 1: state.getBalance (explorer method)
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "state.getBalance",
    "params": ["'$TEST_ADDR'", "latest"],
    "id": 1
  }' | jq

# Method 2: animica_getBalance (wallet method)  
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "animica_getBalance",
    "params": ["'$TEST_ADDR'", "latest"],
    "id": 2
  }' | jq

# Method 3: eth_getBalance (Ethereum compatibility)
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "eth_getBalance",
    "params": ["'$TEST_ADDR'", "latest"],
    "id": 3
  }' | jq
```

**Expected**: All three calls return the **exact same result**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "0x..." 
}
```

The hex value should be identical across all methods.

### Step 2: Check Balance in Explorer

1. Open the explorer web UI (usually http://localhost:3000 or similar)
2. Navigate to the address page for your test address
3. Note the displayed balance

**Expected**: Explorer shows the balance in ANM (e.g., "50 ANM")

### Step 3: Check Balance in Wallet Extension

1. Open the wallet extension in your browser
2. Ensure it's connected to the same network (localhost:8545)
3. View the balance for the same test address

**Expected**: Wallet shows the **same balance** as the explorer (e.g., "50 ANM")

### Step 4: Cross-Check

Convert the hex balance from RPC to ANM and verify it matches:

```bash
# Example: If RPC returned "0xb1a2bc2ec50000" (hex)
# Convert to decimal:
python3 -c "print(int('0xb1a2bc2ec50000', 16))"
# Output: 50000000000000000 (50 ANM in nANM, where 1 ANM = 10^9 nANM)

# Convert to ANM:
python3 -c "print(int('0xb1a2bc2ec50000', 16) / 1_000_000_000)"
# Output: 50.0
```

**Verification Checklist**:
- [ ] `state.getBalance` returns a value (not error)
- [ ] `animica_getBalance` returns the **same** value
- [ ] `eth_getBalance` returns the **same** value  
- [ ] Explorer displays the correct balance
- [ ] Wallet extension displays the **same** balance as explorer
- [ ] Manual hex-to-ANM conversion matches displayed values

## Troubleshooting

### RPC Method Not Found Error

If you see errors like `Method not found: animica_getBalance`:

1. Ensure the node is running the latest code with the fix
2. Restart the RPC server after updating
3. Verify method registration (see Automated Verification above)

### Wallet Shows "0" Balance

If wallet shows 0 but explorer shows correct balance:

1. Check browser console for errors
2. Verify wallet is connected to correct network
3. Check wallet extension background logs
4. Ensure account is unlocked in wallet

### Different Balances Persist

If explorer and wallet still show different balances after the fix:

1. Hard refresh the explorer UI (Ctrl+Shift+R)
2. Restart the wallet extension
3. Clear any cached balances
4. Verify both are querying the same node/network
5. Check if there are pending transactions affecting the balance

## Testing Scenarios

### Scenario 1: Fresh Account

1. Create new wallet address
2. Check balance (should be 0 in both explorer and wallet)
3. Send some coins to the address
4. Verify both show the updated balance immediately (or after block confirmation)

### Scenario 2: Mining Rewards

1. Mine blocks to a wallet address
2. Check balance updates in real-time
3. Verify explorer and wallet update consistently

### Scenario 3: Transfers

1. Send coins from one address to another
2. Verify sender balance decreases in both explorer and wallet
3. Verify recipient balance increases in both explorer and wallet
4. Check balances at different block tags (latest, pending)

## Success Criteria

✓ All RPC method aliases registered correctly  
✓ All aliases return identical values  
✓ Explorer displays correct balance  
✓ Wallet extension displays correct balance  
✓ Explorer balance = Wallet balance (no disparity)  
✓ No errors in browser console  
✓ No errors in node logs  

## Rollback Plan

If the fix causes issues:

1. Revert the commit:
   ```bash
   git revert 2db51856
   ```

2. Or temporarily remove aliases:
   ```python
   # In rpc/methods/state.py, change:
   @method("state.getBalance", desc="...", aliases=(...))
   # Back to:
   @method("state.getBalance", desc="...")
   ```

3. Restart the node

Note: Reverting will restore the original disparity issue.

## Related Files

- `rpc/methods/state.py` - Balance method implementation with aliases
- `rpc/tests/test_balance_method_aliases.py` - Automated tests
- `wallet-extension/src/ui/shared/hooks/useBalance.ts` - Wallet balance hook
- `wallet-extension/src/background/index.ts` - Wallet RPC routing
- `explorer2/api/src/rpcChainClient.ts` - Explorer RPC client

## Additional Notes

- The fix is backward compatible - existing code using `state.getBalance` continues to work
- Wallet extension code does not need changes
- Explorer code does not need changes
- Only the RPC server method registration was updated
- All methods return hex-encoded balance strings (e.g., "0x2a" for 42)
- Frontend code handles hex-to-decimal conversion for display
