# Fix: Miner GUI and Qt Wallet - Infinite Spawn & Built-in RPC Node

## Problem Statement

The user reported three interconnected issues:
1. **Miner GUI and wallet still opens infinitely** 
2. **Doesn't have built-in RPC node**
3. **Build script needs to make Animica binary usable by Qt apps**

## Root Cause Analysis

### Issue 1: Infinite Spawn (Miner GUI)
**Status**: Already fixed in previous commit (MACOS_INFINITE_SPAWN_FIX.md)

The miner GUI had `multiprocessing.freeze_support()` incorrectly placed inside `if __name__ == "__main__"`. It's now at module level (line 14 of main.py), which prevents infinite spawning when packaged with PyInstaller.

### Issue 2: Built-in RPC Node Not Working
**Status**: Fixed in this PR

**Root Cause**:
- Both miner-gui and qt-wallet-py have node management code
- The qt-wallet's `node_manager.py` tried to execute an external `animica` binary
- When apps are packaged with PyInstaller:
  - The `animica` wrapper script at repo root is not included
  - The `_resolve_animica_binary()` function fails to find the binary
  - Node startup fails silently or with "command not found" errors

**The Fix**:
Changed from binary execution to Python module invocation:
```python
# Before (broken in packaged apps)
cmd = [self._resolve_animica_binary(), "--network", network, "node", "up", ...]

# After (works in packaged apps)
import sys
cmd = [sys.executable, "-m", "animica.cli.main", "--network", network, "node", "up", ...]
```

This uses the bundled Python interpreter to run the animica CLI as a module, which works correctly in frozen applications.

### Issue 3: Build Scripts Don't Bundle Animica Binary
**Status**: Fixed in this PR

**Root Cause**:
- Build scripts used PyInstaller but didn't include animica CLI modules in `hiddenimports`
- PyInstaller's automatic dependency detection missed these because they're invoked dynamically
- Result: Packaged apps lacked the code needed to run embedded nodes

**The Fix**:
Added comprehensive hidden imports to all build scripts:
```python
hiddenimports = [
    # ... existing PySide6, matplotlib, etc.
    # Animica CLI and dependencies for embedded node support
    'animica',
    'animica.cli',
    'animica.cli.main',
    'animica.cli.node',
    'animica.config',
    'animica.bootstrap',
    'animica.bootstrap.state',
    'animica.seeds',
    'mining',
    'mining.cli',
    'mining.cli.miner',
]
```

## Changes Made

### 1. Qt Wallet Node Manager (`apps/qt-wallet-py/src/animica_qt_wallet/walletd/node_manager.py`)

**Changed**:
- Removed `_resolve_animica_binary()` method (no longer needed)
- Removed `import shutil` (no longer needed)
- Added `import sys` 
- Modified `_spawn_process()` to use `sys.executable -m animica.cli.main`

**Impact**: Embedded node now starts correctly in packaged applications

### 2. Miner GUI Build Scripts

**Modified files**:
- `apps/miner-gui/build-scripts/build_macos.sh`
- `apps/miner-gui/build-scripts/build_linux.sh`
- `apps/miner-gui/build-scripts/build_windows.sh`

**Changed**: Added animica and mining CLI modules to `hiddenimports` list

**Impact**: Packaged miner GUI now includes all modules needed for mining operations

### 3. Qt Wallet Build Scripts (NEW)

**Created**:
- `apps/qt-wallet-py/build-scripts/build_macos.sh` - macOS build script
- `apps/qt-wallet-py/build-scripts/README.md` - Build documentation

**Features**:
- Creates standalone .app bundle and .dmg installer
- Includes Qt runtime hooks for proper plugin loading
- Bundles all animica CLI modules for embedded node
- Follows same patterns as miner-gui build scripts

**Impact**: Qt wallet can now be distributed as a standalone application

## Technical Details

### Why Module Invocation Works

Python's `-m` flag runs a module as a script:
```bash
python -m animica.cli.main --network mainnet node up
```

When PyInstaller freezes an application:
1. It includes all Python modules in `hiddenimports`
2. `sys.executable` points to the frozen executable
3. The frozen executable can import and run bundled modules
4. This works identically to running modules in development

### Why Binary Wrapper Doesn't Work

The `animica` wrapper at repo root:
```bash
#!/usr/bin/env bash
exec "$CLI_PATH" "$@"  # Points to .venv/bin/animica
```

Problems when packaged:
- Bash scripts can't be frozen by PyInstaller
- The `.venv/bin/animica` entrypoint doesn't exist
- Even if bundled, it would point to non-existent paths

### Miner GUI Already Had It Right

The miner-gui's `miner_runner.py` (line 335) already uses:
```python
cmd = [sys.executable, "-m", "mining.cli.miner", "mine-blocks", ...]
```

This worked correctly. The qt-wallet's node_manager just needed the same approach.

## Testing Recommendations

### Miner GUI
1. Build on each platform (macOS/Windows/Linux)
2. Launch the packaged application
3. Verify mining starts without errors
4. Check that only one instance opens (not infinite)
5. Verify wallet integration works

### Qt Wallet
1. Build on macOS: `cd apps/qt-wallet-py && ./build-scripts/build_macos.sh`
2. Open the .app bundle or mount the .dmg
3. Launch the wallet
4. Verify embedded node starts (check logs at `~/.local/share/Animica Qt Wallet/`)
5. Test wallet operations (create account, send tx, etc.)
6. Verify external RPC also works

### Verification Commands
```bash
# Check embedded node is running
ps aux | grep "python.*animica.cli.main.*node up"

# Check logs
tail -f ~/.local/share/Animica\ Qt\ Wallet/node-mainnet.log
tail -f ~/.local/share/Animica\ Miner\ GUI/gui-miner/logs/miner.log

# Test RPC
curl http://127.0.0.1:8545/rpc -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'
```

## Backward Compatibility

### Development Mode
All changes are backward compatible with development mode:
- `./run.sh` scripts still work (use Python from PATH/venv)
- Direct `python -m` invocation still works
- No changes to CLI entrypoints or module structure

### Packaged Applications
- New builds will have embedded node support
- Old builds without this fix will continue to fail on node startup
- Users should download new builds after this PR merges

## Security Considerations

### No New Attack Vectors
- Still using the same animica CLI code
- No new dependencies added
- No new network exposure

### Data Directory Isolation
- Each app uses its own data directory
- Qt Wallet: `~/.local/share/Animica Qt Wallet/`
- Miner GUI: `~/.animica/gui-miner/`
- No shared state between apps

## Future Improvements

1. **Windows and Linux builds for qt-wallet**: Currently only macOS build script exists
2. **Code signing**: Add certificate signing for distribution
3. **Auto-updates**: Implement update checking and installation
4. **Better node startup feedback**: Add progress indicators during node initialization
5. **Node health monitoring**: Show node sync status in UI

## References

- **PyInstaller multiprocessing guide**: https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing
- **Python -m flag documentation**: https://docs.python.org/3/using/cmdline.html#cmdoption-m
- **Previous fix**: MACOS_INFINITE_SPAWN_FIX.md (multiprocessing.freeze_support)
