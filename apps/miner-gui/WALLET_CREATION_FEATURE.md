# GUI Wallet Creation and Skip Mining Feature - Visual Documentation

## Overview
This document describes the UI changes made to the Animica Miner GUI to support wallet creation and wallet-only setup mode.

## Changes Made

### 1. Wallet Configuration Page

**Location**: `WalletConfigPage` in `apps/miner-gui/animica_miner_gui/ui/wizard.py`

**Before**:
- Single "Import from Wallets" button
- Manual address entry field
- Basic subtitle: "Configure where mining rewards will be sent"

**After**:
- **Two buttons side-by-side**:
  - "Create New Wallet" button (NEW)
  - "Import from Wallets" button (existing)
- Manual address entry field (unchanged)
- Enhanced subtitle: "Configure your wallet address for receiving mining rewards"
- Status/validation label that shows success/error messages

**Visual Layout**:
```
┌─────────────────────────────────────────────────────┐
│ Payout Address                                      │
│ Configure your wallet address for receiving mining  │
│ rewards                                             │
├─────────────────────────────────────────────────────┤
│                                                     │
│ Enter payout address:                               │
│ ┌─────────────────────────────────────────────────┐ │
│ │ anim1...                                        │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ ┌──────────────────┐  ┌────────────────────┐      │
│ │ Create New Wallet│  │ Import from Wallets│      │
│ └──────────────────┘  └────────────────────┘      │
│                                                     │
│ ✓ New wallet created and loaded                    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 2. Create Wallet Dialog

**Location**: `CreateWalletDialog` in `apps/miner-gui/animica_miner_gui/ui/wizard.py` (NEW CLASS)

**Features**:
- Modal dialog that opens when "Create New Wallet" is clicked
- Input field for wallet label
- **Input field for wallet file location with browse button (NEW)**
- Informational text about wallet creation that updates based on selected path
- Real-time status updates during wallet creation
- Uses Dilithium3 post-quantum cryptography

**Visual Layout**:
```
┌─────────────────────────────────────────────────────┐
│ Create New Wallet                              [X]  │
├─────────────────────────────────────────────────────┤
│                                                     │
│ Wallet Label:                                       │
│ ┌─────────────────────────────────────────────────┐ │
│ │ My Wallet                                       │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ Wallet File Location:                               │
│ ┌──────────────────────────────────┐  ┌─────────┐  │
│ │ ~/.animica/wallets.json          │  │Browse...│  │
│ └──────────────────────────────────┘  └─────────┘  │
│                                                     │
│ A new wallet will be created and saved to           │
│ ~/.animica/wallets.json                            │
│ The wallet will use Dilithium3 post-quantum        │
│ cryptography.                                       │
│                                                     │
│ ✓ Wallet created successfully at                   │
│   ~/.animica/wallets.json!                         │
│                                                     │
│                             ┌────┐  ┌────────┐     │
│                             │ OK │  │ Cancel │     │
│                             └────┘  └────────┘     │
└─────────────────────────────────────────────────────┘
```

**Process Flow**:
1. User clicks "Create New Wallet" on WalletConfigPage
2. Dialog opens with label input field and wallet file location field (defaults to ~/.animica/wallets.json)
3. User can optionally click "Browse..." to select a custom location for the wallets.json file
4. Info text updates dynamically to show the selected path
5. User enters wallet label and clicks OK
6. Status shows "Creating wallet..." (blue text)
7. CLI command runs: `python -m animica wallet --wallet-file <path> create --label <label> --allow-insecure-fallback`
8. On success: Status shows "✓ Wallet created successfully at <path>!" (green text)
9. Dialog closes and new address is automatically filled into WalletConfigPage
10. Success message appears: "✓ New wallet created and loaded"

**Error Handling**:
- Empty label: "Please enter a wallet label" (red)
- Invalid file extension: "Wallet file must have .json extension" (red)
- Timeout: "Wallet creation timed out" (red)
- Other errors: "Error: [error message]" (red)

### 3. Summary Page

**Location**: `SummaryPage` in `apps/miner-gui/animica_miner_gui/ui/wizard.py`

**Before**:
- Simple checkbox: "Start mining immediately" (checked by default)
- Static finish message

**After**:
- Enhanced checkbox: "Start mining immediately (uncheck to setup wallet only)"
- Dynamic help text that appears when unchecked
- Summary table includes new "Mode" field
- Dynamic finish message based on checkbox state
- Enhanced subtitle: "Review your configuration and choose whether to start mining"

**Visual Layout (Mining Mode - Checkbox Checked)**:
```
┌─────────────────────────────────────────────────────┐
│ Summary                                             │
│ Review your configuration and choose whether to     │
│ start mining                                        │
├─────────────────────────────────────────────────────┤
│                                                     │
│ Configuration Summary                               │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                     │
│ Network:              Devnet                        │
│ Payout Address:       anim1abc...def               │
│ Performance Preset:   Recommended                   │
│ Mode:                 Start mining immediately      │
│                                                     │
│ Click Finish to save this configuration and start   │
│ mining.                                             │
│                                                     │
│ ☑ Start mining immediately (uncheck to setup       │
│    wallet only)                                     │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Visual Layout (Wallet-Only Mode - Checkbox Unchecked)**:
```
┌─────────────────────────────────────────────────────┐
│ Summary                                             │
│ Review your configuration and choose whether to     │
│ start mining                                        │
├─────────────────────────────────────────────────────┤
│                                                     │
│ Configuration Summary                               │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                     │
│ Network:              Devnet                        │
│ Payout Address:       anim1abc...def               │
│ Performance Preset:   Recommended                   │
│ Mode:                 Wallet setup only             │
│                                                     │
│ Click Finish to save this configuration. You can    │
│ start mining later from the main window.            │
│                                                     │
│ ☐ Start mining immediately (uncheck to setup       │
│    wallet only)                                     │
│                                                     │
│ You can start mining later from the main window if  │
│ you choose wallet-only setup.                       │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## User Workflows

### Workflow 1: Create New Wallet and Start Mining
1. User runs the miner GUI for the first time
2. Wizard opens to Network Selection page
3. User proceeds through RPC Config page
4. On Wallet Config page, user clicks "Create New Wallet"
5. Dialog opens, user enters "My Mining Wallet"
6. Wallet is created, address appears in the field
7. User proceeds through Device Selection and Preset pages
8. On Summary page, "Start mining immediately" is checked
9. User clicks Finish
10. Mining starts automatically

### Workflow 2: Create Wallet Only (No Mining)
1. User runs the miner GUI for the first time
2. Wizard opens to Network Selection page
3. User proceeds through RPC Config page
4. On Wallet Config page, user clicks "Create New Wallet"
5. Dialog opens, user enters "My Wallet"
6. Wallet is created, address appears in the field
7. User proceeds through Device Selection and Preset pages
8. On Summary page, user **unchecks** "Start mining immediately"
9. Help text appears explaining they can start mining later
10. Mode shows "Wallet setup only"
11. User clicks Finish
12. Main window opens but mining does not start
13. User can start mining manually from the Dashboard tab later

### Workflow 3: Import Existing Wallet
1. User has already created wallet using CLI: `animica wallet create --label "CLI Wallet"`
2. User runs the miner GUI
3. On Wallet Config page, user clicks "Import from Wallets"
4. First wallet from ~/.animica/wallets.json is loaded
5. User continues with mining or wallet-only setup

## Technical Implementation

### Wallet Creation
- Uses subprocess to call `python -m animica wallet --wallet-file <path> create`
- Accepts custom wallet file path via `--wallet-file` parameter
- Defaults to `~/.animica/wallets.json` if no custom path provided
- Passes `--allow-insecure-fallback` flag for development/testing
- 30-second timeout for wallet creation
- Parses stdout to extract the created address
- Error handling for timeout, subprocess errors, and parsing failures
- Validates wallet file path has `.json` extension

### UI Components
- `CreateWalletDialog.wallet_path_input`: Editable text field for wallet file path
- `CreateWalletDialog._browse_wallet_file()`: Opens file browser for path selection
- `CreateWalletDialog._update_info_text()`: Dynamically updates info text based on selected path
- Browse button uses `QFileDialog.getSaveFileName` to allow custom location selection
- Info text updates in real-time as user changes the path

### State Management
- `CreateWalletDialog.created_address` stores the new address
- Address is transferred to `WalletConfigPage.address_input` on success
- Validation label updated with success message including the file path
- Wizard field registration ensures address is available to all pages

### Configuration Storage
- Wallet is created in user-specified location (defaults to `~/.animica/wallets.json`)
- Mining configuration saved to `~/.animica/gui-miner/config.json`
- `config.miner.auto_start` field controls mining startup
- When checkbox is unchecked, `auto_start` is set to `False`

## Benefits

1. **Unified Application**: Users can now use the GUI as both a wallet and mining application
2. **Simplified Onboarding**: New users don't need to use CLI to create wallets
3. **Flexibility**: Users can setup wallet first, configure everything, then decide whether to mine
4. **Custom Wallet Location**: Users can choose where to save their wallet file (e.g., on a USB drive, cloud storage, etc.)
5. **Clear Communication**: Labels and help text make it obvious what each option does
6. **Secure**: Uses the same wallet creation CLI that's been tested and validated

## Testing

Tests added in `test_wizard_imports.py`:
- Import tests for new classes
- Component existence verification
- These tests skip if Qt display is not available (CI/headless environments)

Full GUI testing requires:
- Display environment (X11, Wayland, etc.)
- Manual testing of user workflows
- Verification of wallet creation via subprocess
- Validation of configuration persistence
