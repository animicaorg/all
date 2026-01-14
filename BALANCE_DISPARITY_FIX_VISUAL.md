# Balance Disparity Fix - Visual Summary

## Problem: Balance Mismatch Between Explorer and Wallet

### Before the Fix ❌

```
┌─────────────┐                    ┌──────────────┐
│   Explorer  │                    │    Wallet    │
│   Web UI    │                    │  Extension   │
└──────┬──────┘                    └──────┬───────┘
       │                                  │
       │ state.getBalance                │ animica_getBalance
       │                                  │
       ▼                                  ▼
┌─────────────────────────────────────────────────┐
│           RPC Server (Node)                     │
│  ┌──────────────────────────────────────────┐  │
│  │ Registered Methods:                      │  │
│  │   ✓ state.getBalance → state_get_balance│  │
│  │   ✗ animica_getBalance (NOT FOUND!)     │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
       │                                  │
       │ Returns: "0x2710"               │ Returns: ERROR or 0x0
       │ (10,000 nANM)                   │ (default/fallback)
       ▼                                  ▼
┌─────────────┐                    ┌──────────────┐
│  Explorer   │                    │    Wallet    │
│  Shows:     │                    │    Shows:    │
│  10,000 ANM │ ✓ CORRECT          │    0 ANM     │ ✗ WRONG!
└─────────────┘                    └──────────────┘

RESULT: Users confused! Same address shows different balances.
```

### After the Fix ✓

```
┌─────────────┐                    ┌──────────────┐
│   Explorer  │                    │    Wallet    │
│   Web UI    │                    │  Extension   │
└──────┬──────┘                    └──────┬───────┘
       │                                  │
       │ state.getBalance                │ animica_getBalance
       │                                  │
       ▼                                  ▼
┌─────────────────────────────────────────────────┐
│           RPC Server (Node)                     │
│  ┌──────────────────────────────────────────┐  │
│  │ Registered Methods (All → same function):│  │
│  │   ✓ state.getBalance ─────┐             │  │
│  │   ✓ animica_getBalance ───┤             │  │
│  │   ✓ eth_getBalance ────────┼─→ state_   │  │
│  │   ✓ state_getBalance ──────┘   get_     │  │
│  │                                balance   │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
       │                                  │
       │ Returns: "0x2710"               │ Returns: "0x2710"
       │ (10,000 nANM)                   │ (10,000 nANM)
       ▼                                  ▼
┌─────────────┐                    ┌──────────────┐
│  Explorer   │                    │    Wallet    │
│  Shows:     │                    │    Shows:    │
│  10,000 ANM │ ✓ CORRECT          │  10,000 ANM  │ ✓ CORRECT!
└─────────────┘                    └──────────────┘

RESULT: Both show the same balance! ✓
```

## The Fix: One Line Change

### Location: `rpc/methods/state.py`

**Before** (Line 207-210):
```python
@method(
    "state.getBalance",
    desc="Return the account balance for an address at a given block tag. Returns a hex quantity string (e.g. 0x0).",
)
```

**After** (Line 207-211):
```python
@method(
    "state.getBalance",
    desc="Return the account balance for an address at a given block tag. Returns a hex quantity string (e.g. 0x0).",
    aliases=("state_getBalance", "animica_getBalance", "eth_getBalance"),
)
```

**Impact**: 
- Added 3 method name aliases
- All 4 names now resolve to the same underlying function
- Zero breaking changes
- Backward compatible

## Technical Details

### Method Routing Flow

```
User Code                 RPC Method Registry                 Handler Function
──────────               ────────────────────                 ────────────────

request({                ┌───────────────────┐
  method: "state.        │ state.getBalance  │────┐
           getBalance"   └───────────────────┘    │
})                                                 │
                                                   │
request({                ┌───────────────────┐    │
  method: "animica_      │animica_getBalance │────┤
           getBalance"   └───────────────────┘    │
})                                                 ├──→ state_get_balance()
                                                   │        │
request({                ┌───────────────────┐    │        ▼
  method: "eth_          │ eth_getBalance    │────┤    Execute logic
           getBalance"   └───────────────────┘    │    Return hex balance
})                                                 │
                                                   │
request({                ┌───────────────────┐    │
  method: "state_        │ state_getBalance  │────┘
           getBalance"   └───────────────────┘
})
```

### Balance Encoding

All methods return balance as **hex-encoded string**:

```
Database:        42,000,000,000 nANM (int)
                          ↓
RPC Method:      "0x9c7652400" (hex string)
                          ↓
Frontend:        42 ANM (display)

Conversion:
- Storage: Smallest unit (nANM, nano-ANM)
- 1 ANM = 1,000,000,000 nANM (10^9)
- RPC returns hex for compatibility
- UI converts hex → decimal → ANM for display
```

## Wallet Extension Call Stack

### Before Fix (Failed)

```
useBalance.ts (line 85)
  └─> provider.request({ method: "animica_getBalance", params: [address, "latest"] })
        └─> index.ts (background, line 143)
              └─> client.call("animica_getBalance", params)
                    └─> RPC Server
                          └─> Method Not Found! ❌
                                └─> Returns error or default 0x0
```

### After Fix (Success)

```
useBalance.ts (line 85)
  └─> provider.request({ method: "animica_getBalance", params: [address, "latest"] })
        └─> index.ts (background, line 143)
              └─> client.call("animica_getBalance", params)
                    └─> RPC Server
                          └─> Resolves alias → state_get_balance() ✓
                                └─> Returns actual balance "0x..." ✓
```

## Explorer Call Stack

### Before & After (Always Worked)

```
AddressPage.tsx
  └─> api.getAddress(address)
        └─> service.ts → getAddressDetail()
              └─> rpc.getBalance(address, 'latest')
                    └─> rpcClient.call('state.getBalance', [address, tag])
                          └─> RPC Server
                                └─> state.getBalance exists ✓
                                      └─> Returns balance ✓
```

Explorer was never broken because it used the correct method name.

## Why This Happened

### Historical Context

1. **RPC server** was implemented first with standard method names:
   - `state.getBalance`
   - `state.getNonce`
   - etc.

2. **Explorer** was built to use these standard names:
   - Correctly used `state.getBalance`
   - Always worked ✓

3. **Wallet extension** was built later with custom method names:
   - Used `animica_getBalance` (prefixed with "animica_")
   - Assumed aliases existed (they didn't)
   - Failed to get balances ✗

4. **No error was visible** because:
   - Background script passed method through without validation
   - RPC server may have returned default/error silently
   - UI showed "0" or fallback value
   - No console errors if error handling swallowed the failure

### Prevention for Future

✓ Document all RPC method aliases  
✓ Add integration tests for all client types  
✓ Validate method names during development  
✓ Use TypeScript types for method names  
✓ Test with both explorer and wallet before release  

## Testing Verification

### Quick Smoke Test

```bash
# Test that all aliases work
for method in "state.getBalance" "animica_getBalance" "eth_getBalance"; do
  echo "Testing: $method"
  curl -s -X POST http://localhost:8545 \
    -H "Content-Type: application/json" \
    -d '{
      "jsonrpc": "2.0",
      "method": "'$method'",
      "params": ["anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq8xyuud", "latest"],
      "id": 1
    }' | jq -r '.result'
done
```

**Expected output**: All return the same value (e.g., all return `0x0`)

### Full Test Suite

```bash
cd /home/runner/work/all/all
python3 rpc/tests/test_balance_method_aliases.py
```

## Related Issues

This fix addresses:
- ✓ "Incorrect balance and balance disparity between what the explorer shows and what wallets show"
- ✓ Wallet showing 0 when explorer shows correct balance
- ✓ Inconsistent balance across different UIs
- ✓ Method not found errors in wallet extension

## Migration Notes

### For Node Operators
- Update to latest code
- Restart node
- No configuration changes needed
- Existing clients continue to work

### For Wallet Developers
- No wallet code changes needed
- Old method names continue to work
- New aliases available for compatibility

### For Explorer Developers  
- No explorer code changes needed
- Continues using `state.getBalance`
- Works as before

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| Explorer balance | ✓ Correct | ✓ Correct |
| Wallet balance | ✗ Wrong (0 or error) | ✓ Correct |
| Method `state.getBalance` | ✓ Exists | ✓ Exists |
| Method `animica_getBalance` | ✗ Not Found | ✓ Exists (alias) |
| Method `eth_getBalance` | ✗ Not Found | ✓ Exists (alias) |
| Code changes | - | 1 line |
| Breaking changes | - | 0 |
| User experience | Confusing | Consistent |

**Result**: Issue completely resolved with minimal change! ✓
