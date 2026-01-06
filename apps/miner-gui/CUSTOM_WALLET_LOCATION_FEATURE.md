# Custom Wallet Location Feature - Implementation Guide

## Overview

This document describes the implementation of custom wallet location support in the Animica Miner GUI, addressing the issue where users could not mine with wallets stored in custom locations.

## Problem Statement

Users were experiencing the following issues:
1. Wallet was imported in a custom location (not the default `~/.animica/wallets.json`)
2. Mining failed with `-32602: Invalid params` RPC error
3. The payout address was not found in the wallet file
4. The GUI hardcoded the wallet path to `~/.animica/wallets.json`

### Error Example
```
The payout address:
anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz

is not found in your wallet file (/Users/admin/.animica/wallets.json).

[20:55:12] [INFO] miner: WARNING mining.share_submitter: submitShare retry in 0.26s (try 1/5): -32602:RPC error -32602: Invalid params
```

## Solution

### 1. Configuration Support (`config.py`)

Added `wallet_file` field to `MinerConfig`:

```python
class MinerConfig(BaseModel):
    """Core miner settings."""
    mining_mode: MiningMode = Field(default=MiningMode.SOLO, description="Mining mode")
    payout_address: str = Field(default="", description="Payout address for mining rewards")
    wallet_file: Optional[str] = Field(default=None, description="Custom wallet file location (default: ~/.animica/wallets.json)")
    auto_start: bool = Field(default=False, description="Auto-start mining on launch")
    auto_restart_on_crash: bool = Field(default=True, description="Auto-restart miner on crash")
```

**Behavior:**
- `wallet_file = None` (default): Use environment variable or default location
- `wallet_file = "/path/to/wallets.json"`: Use specified path

### 2. Wallet Tab Support (`wallet.py`)

Updated `WalletTab` to respect configuration and environment variables:

```python
# Check if the address exists in wallets.json
# Use configured wallet file path or default
if self.config.miner.wallet_file:
    wallet_path = os.path.expanduser(self.config.miner.wallet_file)
else:
    # Check environment variable, then default
    wallet_path = os.path.expanduser(
        os.environ.get("ANIMICA_WALLETS_FILE", "~/.animica/wallets.json")
    )
```

**Priority Order:**
1. Configured path (`config.miner.wallet_file`)
2. Environment variable (`ANIMICA_WALLETS_FILE`)
3. Default location (`~/.animica/wallets.json`)

### 3. Wizard Support (`wizard.py`)

Updated `WalletConfigPage` to save wallet file path:

- Added hidden field `wallet_file_path_input` to store the wallet file path
- Updated `create_new_wallet()` to capture wallet path from `CreateWalletDialog`
- Updated `import_from_wallets()` to save the imported wallet file path
- Updated `FirstRunWizard.accept()` to save wallet path to config

**User Flow:**
1. User clicks "Import from Wallets" in setup wizard
2. File browser opens to select custom wallet location
3. User selects wallet file (e.g., `/custom/path/wallets.json`)
4. Wizard saves both the address and the wallet file path to config
5. Mining uses the custom wallet location

### 4. Mining Process Support (`miner_runner.py`)

Updated miner process environment to include wallet file location:

```python
minimal_env = {
    'PATH': os.environ.get('PATH', ''),
    'HOME': os.environ.get('HOME', ''),
    'USER': os.environ.get('USER', ''),
    'PYTHONPATH': pythonpath,
    'ANIMICA_PAYOUT_ADDRESS': payout_address
}

# Set custom wallet file location if configured
wallet_file = config.get('miner', {}).get('wallet_file')
if wallet_file:
    minimal_env['ANIMICA_WALLETS_FILE'] = wallet_file
    logger.info(f"Using custom wallet file: {wallet_file}")
```

**Result:** The mining subprocess now receives the `ANIMICA_WALLETS_FILE` environment variable, which the CLI respects.

## Usage Examples

### Example 1: Import wallet from custom location during setup

1. Run the miner GUI setup wizard
2. On the "Payout Address" page, click "Import from Wallets"
3. Navigate to your custom wallet location (e.g., `/mnt/usb/animica-wallets.json`)
4. Select your wallet
5. Complete the wizard

The configuration will be saved with:
```json
{
  "miner": {
    "payout_address": "anim1...",
    "wallet_file": "/mnt/usb/animica-wallets.json"
  }
}
```

### Example 2: Manual configuration edit

Edit `~/.animica/gui-miner/config.json`:

```json
{
  "version": "1.0",
  "miner": {
    "payout_address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
    "wallet_file": "/Users/admin/Documents/my-wallets.json",
    "auto_start": false
  },
  "network": {
    "network_type": "mainnet",
    "rpc_url": "https://rpc.mainnet.animica.org/rpc"
  }
}
```

### Example 3: Using environment variable

Set environment variable before launching GUI:

```bash
export ANIMICA_WALLETS_FILE=/custom/location/wallets.json
./animica-miner-gui
```

## Compatibility

### Backward Compatibility

✅ **Fully backward compatible**

- Existing configurations without `wallet_file` field continue to work
- Default behavior unchanged: uses `~/.animica/wallets.json`
- Respects `ANIMICA_WALLETS_FILE` environment variable (existing feature)

### CLI Integration

The miner GUI now properly integrates with the CLI's wallet resolution:

```python
# From python/animica/cli/wallet.py
WALLET_FILE_ENV = "ANIMICA_WALLETS_FILE"

def _wallet_file_path(wallet_file: Optional[Path]) -> Path:
    if wallet_file is not None:
        return Path(wallet_file)
    env_path = os.environ.get(WALLET_FILE_ENV)
    if env_path:
        return Path(env_path)
    return _get_default_wallet_path()
```

## Testing

### Manual Testing Checklist

- [ ] Create wallet in custom location during wizard setup
- [ ] Import wallet from custom location during wizard setup
- [ ] Verify wallet file path is saved in config
- [ ] Start mining with custom wallet location
- [ ] Verify mining succeeds (no more "Invalid params" errors)
- [ ] Send transaction from custom wallet location
- [ ] Verify transaction succeeds
- [ ] Test with USB drive location
- [ ] Test with network drive location (if available)
- [ ] Test backward compatibility (empty wallet_file field)
- [ ] Test environment variable fallback

### Automated Testing

Basic configuration tests can be run with:

```python
from animica_miner_gui.backend.config import MinerConfig, MiningAppConfig

# Test 1: Config accepts wallet_file
config = MinerConfig(
    payout_address="anim1test",
    wallet_file="/custom/wallets.json"
)
assert config.wallet_file == "/custom/wallets.json"

# Test 2: Default is None
config2 = MinerConfig(payout_address="anim1test")
assert config2.wallet_file is None

# Test 3: Full config
full = MiningAppConfig()
full.miner.wallet_file = "/test.json"
assert full.miner.wallet_file == "/test.json"
```

## Troubleshooting

### Issue: Mining still fails with "Invalid params"

**Possible causes:**
1. Wallet file path not set correctly
2. Wallet file doesn't contain the payout address
3. Wallet file has incorrect permissions

**Solution:**
1. Check config: `cat ~/.animica/gui-miner/config.json`
2. Verify wallet file contains address: `cat /path/to/wallets.json`
3. Check permissions: `ls -la /path/to/wallets.json`

### Issue: Wallet not found after import

**Possible causes:**
1. File was moved after import
2. Relative path was used instead of absolute
3. Network drive disconnected

**Solution:**
1. Re-import wallet from current location
2. Use absolute paths for reliability
3. Consider copying wallet to local drive

## Security Considerations

### File Permissions

Wallet files should have restricted permissions:
```bash
chmod 600 /path/to/wallets.json
```

The GUI automatically sets `0600` permissions when creating wallets.

### Path Validation

The wizard validates wallet paths to prevent:
- Directory traversal attacks
- Invalid characters
- Non-existent directories (creates if needed)
- Non-JSON files

### Environment Variable Injection

The `ANIMICA_WALLETS_FILE` environment variable is:
- Only set in child process (mining subprocess)
- Not executed as shell code
- Properly quoted and escaped
- Validated before use

## Future Enhancements

Potential improvements for future releases:

1. **Wallet Browser UI**: Add visual wallet file browser in Configuration tab
2. **Multiple Wallets**: Support for switching between multiple wallet files
3. **Wallet Backup**: Automatic backup to prevent data loss
4. **Cloud Sync**: Optional sync with cloud storage
5. **Hardware Wallet**: Support for hardware wallet integration

## Related Files

Modified files in this implementation:

```
apps/miner-gui/animica_miner_gui/backend/config.py
apps/miner-gui/animica_miner_gui/backend/miner_runner.py
apps/miner-gui/animica_miner_gui/ui/tabs/wallet.py
apps/miner-gui/animica_miner_gui/ui/wizard.py
```

## References

- [CLI Wallet Documentation](../../python/animica/cli/README.md)
- [Wallet File Format](../../python/animica/security/KEY_FORMATS.md)
- [Miner GUI Architecture](../miner-gui/README.md)
- [Environment Variables](../../README.md#environment-variables)
