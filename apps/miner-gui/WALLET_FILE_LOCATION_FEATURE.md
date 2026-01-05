# Wallet File Location Selection Feature

## Summary

This feature allows users to choose where to save their `wallets.json` file when creating a new wallet through the Animica miner GUI. Previously, the wallet file was hardcoded to `~/.animica/wallets.json`. Now users can select a custom location (e.g., USB drive, cloud storage directory, etc.).

## User-Facing Changes

### Create Wallet Dialog

When users click "Create New Wallet" in the miner GUI wizard, they now see:

1. **Wallet Label**: Text input for wallet name (unchanged)
2. **Wallet File Location**: New text input with path to wallets.json file
3. **Browse Button**: Opens file browser to select custom location
4. **Dynamic Info Text**: Updates to show where the wallet will be saved

### Default Behavior

- The wallet file location defaults to `~/.animica/wallets.json` (standard location)
- Users can manually edit the path or use the Browse button
- The info text dynamically updates to show the selected path

### Validation

- File path must end with `.json` extension
- If validation fails, a clear error message is shown
- All existing validation for wallet labels remains unchanged

## Technical Implementation

### Modified Files

1. **`apps/miner-gui/animica_miner_gui/ui/wizard.py`**
   - Enhanced `CreateWalletDialog.__init__()` to include wallet path input field
   - Added `_browse_wallet_file()` method for file selection
   - Added `_update_info_text()` method for dynamic text updates
   - Modified `create_wallet()` to pass `--wallet-file` parameter to CLI

2. **`apps/miner-gui/WALLET_CREATION_FEATURE.md`**
   - Updated documentation with new feature details
   - Added visual layout showing new field
   - Updated process flow with file selection step

3. **`apps/miner-gui/verify_wallet_features.py`**
   - Enhanced verification to check new components
   - Added tests for wallet_path_input field
   - Added tests for new methods

4. **`apps/miner-gui/test_wallet_path_logic.py`** (NEW)
   - Tests wallet path logic without requiring GUI
   - Validates default paths, custom paths, CLI commands, etc.

### CLI Integration

The wallet creation now uses:
```bash
python -m animica wallet --wallet-file <custom-path> create --label <label> --allow-insecure-fallback
```

Instead of the previous command that always used the default location:
```bash
python -m animica wallet create --label <label> --allow-insecure-fallback
```

### UI Components

- **wallet_path_input** (QLineEdit): Editable path to wallet file
- **browse_button** (QPushButton): Opens QFileDialog for file selection
- **info_text** (QLabel): Dynamically updates based on selected path

## Use Cases

### 1. Default Behavior (No Change for Existing Users)
User creates wallet without changing the path → wallet saved to `~/.animica/wallets.json`

### 2. Custom Location on External Drive
User clicks Browse, selects `/media/usb/my_wallets.json` → wallet saved to USB drive

### 3. Cloud Storage Integration
User enters path like `~/Dropbox/animica_wallets.json` → wallet backed up to cloud

### 4. Multiple Wallet Files
User can organize different wallet files for different purposes:
- `~/.animica/mining_wallets.json` - Mining wallets
- `~/.animica/test_wallets.json` - Test wallets
- `~/secure/cold_wallets.json` - Cold storage wallets

## Testing

### Automated Tests

Run the logic tests:
```bash
cd apps/miner-gui
python3 test_wallet_path_logic.py
```

All tests should pass:
- ✓ Default wallet path
- ✓ Custom wallet path validation
- ✓ CLI command construction
- ✓ Info text generation
- ✓ Browse dialog logic

### Manual GUI Testing (Requires Display)

1. Install dependencies:
   ```bash
   cd apps/miner-gui
   pip install -e ".[dev]"
   ```

2. Run verification script:
   ```bash
   python3 verify_wallet_features.py
   ```

3. Run the GUI:
   ```bash
   animica-miner-gui
   ```

4. Test the workflow:
   - Click through wizard to wallet creation page
   - Click "Create New Wallet"
   - Verify wallet path field shows default
   - Click "Browse..." and select custom location
   - Verify info text updates with new path
   - Enter wallet label and click OK
   - Verify wallet is created at selected location

## Security Considerations

- File path validation prevents command injection
- Path must end with `.json` extension
- Standard file permissions apply (0600 for wallet files)
- Browse dialog uses Qt's native file selector (secure)
- Same security guarantees as CLI wallet creation

## Backward Compatibility

- ✓ Default behavior unchanged (uses `~/.animica/wallets.json`)
- ✓ Existing wallets in default location work as before
- ✓ Import from wallets.json still works with default location
- ✓ CLI wallet creation unchanged
- ✓ No breaking changes to wallet format or structure

## Future Enhancements

Potential improvements for future versions:

1. **Recent Locations**: Remember recently used wallet file locations
2. **Multiple Wallet Files**: Support importing from multiple wallet files
3. **Wallet File Migration**: Tool to move wallets between files
4. **Backup Reminders**: Notify users to backup custom wallet locations

## Related Documentation

- `WALLET_CREATION_FEATURE.md` - Complete visual documentation
- `verify_wallet_features.py` - GUI verification script
- `test_wallet_path_logic.py` - Logic tests
- `README.md` - Main GUI miner documentation
