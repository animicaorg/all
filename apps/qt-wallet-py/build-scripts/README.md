# Build Scripts for Animica Qt Wallet

This directory contains build scripts for creating standalone executables of the Animica Qt Wallet.

## Overview

The build scripts use PyInstaller to create distributable packages for different platforms:

- **macOS**: Creates a `.app` bundle and `.dmg` installer
- **Windows**: Creates a standalone `.exe` executable (TODO)
- **Linux**: Creates a standalone binary and AppImage (TODO)

## Key Features

### Built-in Node Support

All builds include the full Animica CLI and node runtime, allowing the wallet to:
- Start and manage an embedded Animica node
- Connect to external RPC endpoints
- Switch between networks (mainnet, testnet, devnet)

The embedded node uses `sys.executable -m animica.cli.main` instead of relying on external `animica` binary, ensuring it works correctly in packaged applications.

### Hidden Imports

The build scripts include these critical modules:
- PySide6 (Qt framework)
- qasync (async event loop)
- aiohttp (HTTP client)
- animica.cli.* (CLI framework)
- animica.config (network configuration)
- animica.bootstrap.* (bootstrap state)
- animica.seeds (seed nodes)

## Building

### Prerequisites

- Python 3.11 or higher
- PyInstaller
- Platform-specific tools:
  - macOS: Xcode Command Line Tools, `hdiutil`
  - Windows: Visual Studio Build Tools (TODO)
  - Linux: Qt development libraries (TODO)

### macOS

```bash
cd apps/qt-wallet-py
./build-scripts/build_macos.sh
```

Output:
- `dist/Animica Qt Wallet.app` - Application bundle
- `dist/Animica-Qt-Wallet-{version}-macOS-{arch}.dmg` - DMG installer

### Testing

```bash
# Test the app bundle directly
open "dist/Animica Qt Wallet.app"

# Or mount and test the DMG
open dist/*.dmg
```

## Implementation Details

### Node Manager Integration

The wallet's `node_manager.py` has been updated to use Python module invocation:

**Before (broken in packaged apps):**
```python
cmd = [self._resolve_animica_binary(), ...]
```

**After (works in packaged apps):**
```python
import sys
cmd = [sys.executable, "-m", "animica.cli.main", ...]
```

This ensures the embedded node can be started using the bundled Python interpreter and modules.

### PyInstaller Configuration

The build scripts create a custom `.spec` file that:
1. Sets up Qt plugin paths via runtime hooks
2. Includes all necessary hidden imports
3. Configures macOS-specific settings (bundle identifier, version, etc.)
4. Excludes unnecessary modules (tkinter, tests, etc.)

## Troubleshooting

### "Module not found" errors

If the built application fails with import errors:
1. Check that the module is listed in `hiddenimports` in the build script
2. Ensure the module is installed in the build environment
3. Try running with `console=True` in the spec file to see detailed errors

### Qt platform plugin errors

If you see "Could not find the Qt platform plugin":
1. The runtime hook should fix this automatically
2. Check that PySide6 is properly installed
3. Verify Qt plugin libraries are included in the bundle

### Node startup failures

If the embedded node fails to start:
1. Check the walletd process logs at `~/.local/share/Animica Qt Wallet/walletd-process.log`
2. Check node logs at `~/.local/share/Animica Qt Wallet/node-{network}.log`
3. Ensure all animica.* modules are in hiddenimports

## Future Work

- [ ] Create Windows build script
- [ ] Create Linux build script with AppImage
- [ ] Add code signing for macOS
- [ ] Add Windows installer (MSI/NSIS)
- [ ] Add Linux packaging (DEB/RPM)
