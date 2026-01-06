# GUI Miner Wallet Fix - Flow Diagram

## Before Fix

```
User clicks "Send Transaction" in GUI
    ↓
GUI collects: from_addr, to_addr, amount
    ↓
GUI calls: animica tx send --from <addr> --to <addr> --value <amount>
    ↓
CLI tx.py: _load_wallet_entry(from_addr)
    ↓
Searches ~/.animica/wallets.json for address
    ↓
Address NOT FOUND! ❌
    ↓
RuntimeError: Address not found in wallets.json
    ↓
GUI shows cryptic error traceback to user 😞
```

## After Fix

### Path 1: GUI Pre-Validation (Primary Fix)

```
User clicks "Send Transaction" in GUI
    ↓
GUI collects: from_addr, to_addr, amount
    ↓
GUI checks: Is from_addr in ~/.animica/wallets.json? 
    ↓
    ├─ YES → Proceed with transaction ✅
    │   ↓
    │   GUI calls: animica tx send --from <addr> --to <addr> --value <amount>
    │   ↓
    │   Transaction succeeds 🎉
    │
    └─ NO → Show user-friendly error dialog ⚠️
        ↓
        ┌────────────────────────────────────────────────────┐
        │  Address Not in Wallet                             │
        │                                                     │
        │  Your payout address is not in wallet file.        │
        │                                                     │
        │  To fix this:                                      │
        │  1. Import/create wallet with this address         │
        │  2. Change payout address to existing wallet       │
        └────────────────────────────────────────────────────┘
        ↓
        User knows exactly what to do 😊
```

### Path 2: CLI with External Keys (Advanced Users)

```
Advanced user has keys but address not in wallets.json
    ↓
User runs CLI with external keys:
animica tx send \
  --from <addr> \
  --to <addr> \
  --value <amount> \
  --secret-key-hex <hex> \
  --public-key-hex <hex> \
  --alg-id <id>
    ↓
CLI tx.py: Check if external keys provided?
    ↓
    ├─ YES → Use external keys ✅
    │   ↓
    │   Validate all 3 params present
    │   ↓
    │   Sign transaction with provided keys
    │   ↓
    │   Transaction succeeds 🎉
    │
    └─ NO → Load from wallet file (original behavior)
        ↓
        _load_wallet_entry(from_addr)
        ↓
        If address not found → Show helpful error with tip about --secret-key-hex
```

## Key Improvements

### 1. Early Validation
- **Before**: Error happened deep in CLI after RPC checks
- **After**: Error caught immediately in GUI before CLI call

### 2. Clear Error Messages
- **Before**: `RuntimeError: Address not found in /Users/admin/.animica/wallets.json: anim1zqq...`
- **After**: Dialog with clear instructions on how to fix

### 3. Flexibility Added
- **Before**: Only way to send = address must be in wallets.json
- **After**: Two ways to send:
  1. Address in wallets.json (normal users)
  2. Provide keys via CLI (advanced users)

### 4. User Experience
- **Before**: Confusion and frustration
- **After**: Clear guidance and actionable steps

## Error Messages Comparison

### Before
```
RuntimeError: Address not found in /Users/admin/.animica/wallets.json:
anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
```

### After (GUI)
```
┌──────────────────────────────────────────────────┐
│  ⚠️  Address Not in Wallet                       │
│                                                   │
│  The payout address:                              │
│  anim1zqqjt3258...                               │
│                                                   │
│  is not found in your wallet file.               │
│                                                   │
│  You cannot send transactions from addresses      │
│  that aren't in your wallet file.                │
│                                                   │
│  To fix this:                                     │
│  1. Go to Configuration and import/create a       │
│     wallet with this address, OR                  │
│  2. Change your payout address to one that        │
│     exists in your wallet file                    │
└──────────────────────────────────────────────────┘
```

### After (CLI)
```
RuntimeError: Address not found in /Users/admin/.animica/wallets.json:
anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz

Tip: If this address is from an external wallet, provide signing keys 
using --secret-key-hex, --public-key-hex, and --alg-id options.
```

## Code Changes Summary

### File 1: python/animica/cli/tx.py
- **Lines changed**: ~40 lines
- **Key changes**:
  - Added 3 new CLI parameters
  - Modified wallet loading to support external keys
  - Enhanced error messages with helpful tips

### File 2: apps/miner-gui/animica_miner_gui/ui/tabs/wallet.py
- **Lines changed**: ~35 lines  
- **Key changes**:
  - Added imports (json, os)
  - Added pre-send validation check
  - Added user-friendly error dialog

**Total impact**: ~75 lines changed across 2 files
**Backward compatibility**: 100% preserved
**Breaking changes**: None
