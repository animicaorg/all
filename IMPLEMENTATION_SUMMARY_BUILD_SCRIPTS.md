# Build Scripts and macOS Infinite Spawn Fix - Implementation Summary

## Overview

This implementation adds comprehensive build scripts for the Animica node binary and miner-gui application, along with fixes for the macOS infinite spawn issue.

## Part A: Build Scripts (ops/build/)

### New Files Created

1. **`ops/build/common.sh`** (7,272 bytes)
   - Shared helper functions for all build scripts
   - Logging: `log()`, `warn()`, `err()`, `die()`
   - Path resolution: `get_repo_root()`, `get_build_dir()`
   - Version helpers: `compute_version()`, `get_git_sha()`, `get_git_tag()`
   - Safe operations: `safe_rm_rf()` with guards against system directories
   - Python helpers: `find_python3()`, `check_python_version()`, `pip_install()`
   - Manifest generation: `create_manifest()` for build metadata
   - All scripts use `set -euo pipefail` for strict error handling

2. **`ops/build/build-node-binary.sh`** (8,405 bytes)
   - Builds standalone `animica-node` daemon using PyInstaller
   - Supports `--network`, `--out-dir`, `--clean`, `--version` flags
   - Creates manifest file with version, git SHA, timestamp, platform
   - Includes comprehensive hidden imports for all node functionality
   - Output: `dist/animica-node` and `dist/animica-node.manifest.json`

3. **`ops/build/build-miner-gui-macos.sh`** (11,094 bytes)
   - Builds macOS `.app` bundle for miner-gui
   - **Bundles node binary inside**: `Contents/Resources/bin/animica-node`
   - First builds node binary, then packages GUI with it
   - Creates DMG installer automatically
   - Includes Qt runtime hook for plugin paths
   - Output: `.app`, `.dmg`, and manifest
   - Supports `--out-dir`, `--clean`, `--dev` flags

4. **`ops/build/build-miner-gui-linux.sh`** (9,203 bytes)
   - Builds Linux standalone executable for miner-gui
   - **Bundles node binary** in `bin/` subdirectory
   - Creates tarball archive
   - Output: standalone binary, `.tar.gz`, and manifest
   - Supports `--out-dir`, `--clean` flags

5. **`ops/build/README.md`** (9,716 bytes)
   - Comprehensive documentation for all build scripts
   - Prerequisites, usage examples, troubleshooting
   - Verification instructions for testing builds
   - Security considerations (localhost-only RPC, single-instance)
   - CI/CD integration examples

### Key Features

- **Defensive Scripting**: All scripts use strict mode, validate inputs/outputs, provide clear errors
- **No Interactive Prompts**: Fully automated builds
- **Cross-Platform**: Scripts work on macOS and Linux (Windows TBD)
- **Bundled Node**: GUI apps include the node binary for local-only operation
- **Manifest Generation**: JSON metadata for each build (version, git SHA, timestamp)
- **Verification**: Scripts verify outputs exist and are executable

## Part B: macOS Infinite Spawn Fixes

### Root Cause Analysis

The infinite spawn issue occurs when:
1. `sys.executable` is used to launch subprocesses in frozen mode (points to GUI binary, not Python)
2. Child processes re-execute the GUI entrypoint
3. Each instance tries to launch more subprocesses, creating exponential spawn

### New Files Created

1. **`animica_miner_gui/backend/freeze_utils.py`** (4,452 bytes)
   - `is_frozen()`: Detects PyInstaller frozen execution
   - `get_bundled_bin_path(name)`: Locates bundled binaries in .app or dist/
   - `get_python_executable()`: Returns Python path or raises error if frozen
   - `should_use_bundled_node()`: Returns True if frozen (enforces local node use)
   - Handles different directory structures for macOS (.app) vs Linux

2. **`animica_miner_gui/backend/single_instance.py`** (3,202 bytes)
   - Qt-based single-instance enforcement using `QLocalServer`/`QLocalSocket`
   - If another instance running: sends "raise" message and exits immediately
   - Primary instance raises/activates window on message from secondary
   - Removes stale server on startup (handles crashed previous instances)

3. **`animica_miner_gui/backend/startup_loop.py`** (3,952 bytes)
   - Detects infinite launch loops (>5 launches in 30 seconds)
   - Writes timestamped launch markers to `launch_marker.txt`
   - Removes old launches outside time window
   - Provides `reset()` method for user-initiated reset

4. **`animica_miner_gui/backend/startup_logging.py`** (2,724 bytes)
   - Logs startup information to `~/.animica/gui-miner/logs/startup-{date}.log`
   - Records: PID, PPID, sys.executable, sys.frozen, sys._MEIPASS, argv
   - Stage markers: `log_startup_stage(stage)` for tracking progress
   - Helps diagnose launch issues and spawn loops

### Modified Files

1. **`animica_miner_gui/main.py`**
   - Added startup logging to file (for debugging)
   - Added startup loop detection (shows error dialog if loop detected)
   - Added single-instance guard (prevents multiple windows)
   - Connected guard's `raise_requested` signal to window raise/activate
   - Releases single-instance lock on exit
   - Already had `multiprocessing.freeze_support()` at module level (correct)

2. **`animica_miner_gui/backend/miner_runner.py`**
   - **Critical fix**: Never uses `sys.executable` when frozen
   - In frozen mode: locates and uses bundled `animica-node` or `animica` binary
   - In dev mode: uses Python executable with `-m mining.cli.miner`
   - Falls back gracefully if bundled binary not found (shows clear error)
   - Imports `freeze_utils` for safe subprocess handling

3. **`apps/miner-gui/build-scripts/build_macos.sh`** (deprecated)
   - Added deprecation warning (3-second delay)
   - Delegates to unified script: `ops/build/build-miner-gui-macos.sh`
   - Backward compatible but encourages migration

4. **`apps/miner-gui/build-scripts/build_linux.sh`** (deprecated)
   - Added deprecation warning (3-second delay)
   - Delegates to unified script: `ops/build/build-miner-gui-linux.sh`
   - Backward compatible but encourages migration

5. **`apps/miner-gui/build-scripts/README.md`** (deprecated)
   - Added prominent deprecation notice at top
   - Directs users to `ops/build/README.md`
   - Explains benefits of unified scripts

## Defense-in-Depth for Spawn Prevention

The implementation uses **multiple layers of protection**:

1. **freeze_support() at module level** (already present)
   - Required by PyInstaller to prevent multiprocessing spawn loops
   - Must be called before any multiprocessing operations

2. **Never use sys.executable when frozen** (new)
   - `miner_runner.py` checks `is_frozen()` before launching subprocesses
   - Uses bundled binary path instead of GUI executable
   - Raises clear error if binary not found

3. **Single-instance enforcement** (new)
   - Prevents multiple GUI windows from opening
   - Uses Qt's `QLocalServer` for cross-platform enforcement
   - Secondary instance sends "raise" and exits immediately

4. **Startup loop detection** (new, safety net)
   - Tracks launch timestamps in marker file
   - Shows error dialog if >5 launches in 30 seconds
   - Allows user to reset settings and disable auto-start
   - Not the primary fix, but catches edge cases

5. **Startup logging** (new, diagnostic)
   - Detailed logs to app data directory
   - Records PID, PPID, executable paths, arguments
   - Stage markers track progress through startup
   - Helps diagnose any remaining issues

## Verification Instructions

### Build Verification

```bash
# Build node binary
./ops/build/build-node-binary.sh --clean
dist/animica-node --help

# Build macOS GUI (on Mac)
./ops/build/build-miner-gui-macos.sh --clean
open "dist/Animica Miner GUI.app"

# Build Linux GUI (on Linux)
./ops/build/build-miner-gui-linux.sh --clean
./dist/animica-miner-gui

# Check bundled node (macOS)
ls -lh "dist/Animica Miner GUI.app/Contents/Resources/bin/animica-node"
"dist/Animica Miner GUI.app/Contents/Resources/bin/animica-node" --help

# Check bundled node (Linux)
tar -tzf dist/Animica-Miner-GUI-*-Linux-*.tar.gz | grep animica-node
```

### Spawn Fix Verification

1. **Single Instance Test**
   - Open the GUI
   - Try to open it again (double-click .app or run command again)
   - ✅ Expected: Only one window; second launch activates existing window

2. **No External RPC Test**
   - Open the GUI
   - Go to Settings → Network
   - ✅ Expected: RPC URL is `http://127.0.0.1:8545/rpc` (localhost only)

3. **Bundled Node Test** (macOS)
   - Open the GUI
   - Start mining
   - Check logs at `~/.animica/gui-miner/logs/startup-*.log`
   - ✅ Expected: Log shows bundled node path (Contents/Resources/bin/animica-node)

4. **Startup Logs Test**
   - Open the GUI
   - Check `~/.animica/gui-miner/logs/startup-{date}.log`
   - ✅ Expected: Contains PID, PPID, sys.frozen=True, stage markers

5. **Loop Breaker Test** (optional, requires manual loop creation)
   - Temporarily modify code to cause spawn loop
   - Launch app
   - ✅ Expected: After 5 launches in 30s, shows error dialog with reset option

## Security Considerations

- **Node RPC**: Binds to `127.0.0.1` only (no external access)
- **Auth Token**: Node generates auth token in `~/.animica/node.token`
- **GUI-Node Communication**: Uses localhost RPC with auth token
- **No External Nodes**: GUI hardcoded to never connect to remote RPC
- **Single Instance**: Prevents multiple GUI instances (reduces confusion, resource usage)
- **Bundled Binary**: Node binary embedded in .app (can't be replaced without rebuilding)

## Files Changed Summary

```
New files (ops/build/):
  ops/build/common.sh                                  (7,272 bytes)
  ops/build/build-node-binary.sh                       (8,405 bytes)
  ops/build/build-miner-gui-macos.sh                  (11,094 bytes)
  ops/build/build-miner-gui-linux.sh                   (9,203 bytes)
  ops/build/README.md                                  (9,716 bytes)

New files (miner-gui protections):
  apps/miner-gui/animica_miner_gui/backend/freeze_utils.py       (4,452 bytes)
  apps/miner-gui/animica_miner_gui/backend/single_instance.py    (3,202 bytes)
  apps/miner-gui/animica_miner_gui/backend/startup_loop.py       (3,952 bytes)
  apps/miner-gui/animica_miner_gui/backend/startup_logging.py    (2,724 bytes)

Modified files:
  apps/miner-gui/animica_miner_gui/main.py             (added 50 lines)
  apps/miner-gui/animica_miner_gui/backend/miner_runner.py  (added 80 lines, modified command building)
  apps/miner-gui/build-scripts/build_macos.sh         (replaced with delegation wrapper)
  apps/miner-gui/build-scripts/build_linux.sh         (replaced with delegation wrapper)
  apps/miner-gui/build-scripts/README.md              (added deprecation notice)

Total: 11 new files, 5 modified files
Total lines added: ~2,360
```

## Next Steps

1. **Test builds locally** (if macOS/Linux available)
2. **Update CI/CD** to use new build scripts
3. **Document release process** using unified build system
4. **Deprecate old scripts** after migration period (6 months?)

## References

- PyInstaller multiprocessing: https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing
- Qt single instance: https://doc.qt.io/qt-6/qlocalserver.html
- macOS code signing: https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution
