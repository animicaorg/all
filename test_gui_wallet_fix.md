# GUI Miner Wallet Transaction Fix - Verification Guide

## Problem Statement
The GUI miner wallet was failing when trying to send transactions because the payout address was not in the local wallets.json file, resulting in:
```
RuntimeError: Address not found in /Users/admin/.animica/wallets.json: anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
```

## Solution Overview

### 1. CLI Enhancement (python/animica/cli/tx.py)
Added support for external keys so transactions can be sent without requiring the address in wallets.json:

**New Parameters:**
- `--secret-key-hex`: Secret key in hex format
- `--public-key-hex`: Public key in hex format  
- `--alg-id`: Signature algorithm ID (e.g., 4098 for Dilithium3, 0xFFFF for Ed25519)

**Improved Error Messages:**
- When wallet file not found: Suggests creating a wallet or using external keys
- When address not in wallet: Provides tip about using external keys as alternative

### 2. GUI Validation (apps/miner-gui/animica_miner_gui/ui/tabs/wallet.py)
Added pre-flight validation before sending transactions:

**Validation Logic:**
1. Checks if payout address exists in `~/.animica/wallets.json`
2. If not found, shows clear error dialog with actionable instructions
3. Prevents transaction attempt that would fail

**User-Friendly Error Dialog:**
```
Address Not in Wallet

The payout address:
anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz

is not found in your wallet file (/Users/admin/.animica/wallets.json).

You cannot send transactions from addresses that aren't in your wallet file.

To fix this:
1. Go to Configuration and import/create a wallet with this address, OR
2. Change your payout address to one that exists in your wallet file
```

## Usage Examples

### CLI with External Keys
```bash
animica tx send \
  --from anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz \
  --to anim1zqp2pg8s9mjhyfkmkdwfxzyaw6tzn3afqt2jj4kd2un3uz89e7n2rggxgsw3p \
  --value 1.0 \
  --secret-key-hex 0011223344556677889900112233445566778899001122334455667788990011 \
  --public-key-hex a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 \
  --alg-id 4098 \
  --rpc-url https://rpc.mainnet.animica.org/rpc
```

### CLI with Wallet File (Original Behavior)
```bash
animica tx send \
  --from anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz \
  --to anim1zqp2pg8s9mjhyfkmkdwfxzyaw6tzn3afqt2jj4kd2un3uz89e7n2rggxgsw3p \
  --value 1.0 \
  --rpc-url https://rpc.mainnet.animica.org/rpc
```
*Note: This requires the address to exist in ~/.animica/wallets.json*

## Code Changes Summary

### python/animica/cli/tx.py
1. Added 3 new optional parameters to `send()` function
2. Modified wallet loading logic to support two paths:
   - External keys: Use provided `--secret-key-hex`, `--public-key-hex`, `--alg-id`
   - Wallet file: Load from `~/.animica/wallets.json` (original behavior)
3. Added validation to ensure all external key parameters provided together
4. Updated error messages to suggest using external keys when address not found
5. Fixed variable naming: `alg_id` → `used_alg_id` for consistency in signing operations

### apps/miner-gui/animica_miner_gui/ui/tabs/wallet.py
1. Added imports: `json`, `os`
2. Added `check if address in wallet file` logic in `send_transaction()`
3. Added user-friendly error dialog with clear instructions
4. Prevents failed transaction attempts

## Testing Performed

✓ Function signature verification: New parameters present
✓ Python syntax validation: Both files compile without errors
✓ Import verification: Required modules imported correctly
✓ Logic flow verification: Validation checks in place
✓ Error message verification: Helpful messages with actionable tips

## Benefits

1. **Flexibility**: CLI now supports sending from any address with external keys
2. **User Experience**: GUI shows clear, actionable error messages
3. **Backward Compatible**: Original wallet file behavior still works
4. **Early Validation**: GUI prevents failed transactions before attempting
5. **Clear Instructions**: Users know exactly how to fix the issue

## Migration Path for Users

If you're a GUI miner user encountering this error:

### Option 1: Import Your Wallet
1. Open the GUI miner
2. Go to Configuration tab
3. Click "Import from Wallets" 
4. Select your wallets.json file containing the payout address

### Option 2: Create New Wallet
1. Open the GUI miner
2. Go to Configuration tab
3. Click "Create New Wallet"
4. Set this as your payout address

### Option 3: Use Different Address
1. Open the GUI miner
2. Go to Configuration tab
3. Change payout address to one that exists in your wallet file

## Future Enhancements

Potential improvements for future versions:
- GUI could store encrypted keys in config (with user consent)
- GUI could detect wallet file location from environment
- Support for multiple wallet file locations
- Auto-import wallet when setting payout address
