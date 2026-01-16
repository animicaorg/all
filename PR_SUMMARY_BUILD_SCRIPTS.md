# Build Scripts and macOS Infinite Spawn Fix - PR Summary

## Problem Statement

1. **Missing Build Automation**: No defensive bash scripts to build the node binary and miner-gui application bundles
2. **macOS Infinite Spawn Bug**: Packaged miner-gui opens infinitely after launch, making it unusable
3. **Node Not Bundled**: GUI doesn't bundle the node binary, requiring external setup
4. **No Local-Only Enforcement**: No explicit protection preventing external RPC connections

## Solution Overview

This PR implements:
- **Unified build system** in `ops/build/` with defensive bash scripts
- **Multiple layers** of macOS spawn protection
- **Node binary bundling** inside GUI .app/.tar.gz for local-only operation
- **Comprehensive documentation** with troubleshooting and verification

## Changes Summary

### New Build Scripts (ops/build/)

```
ops/build/
├── common.sh                      # Shared helper functions (7.3 KB)
├── build-node-binary.sh           # Build standalone node daemon (8.4 KB)
├── build-miner-gui-macos.sh       # Build macOS .app with bundled node (11.1 KB)
├── build-miner-gui-linux.sh       # Build Linux binary with bundled node (9.2 KB)
└── README.md                      # Complete documentation (9.7 KB)
```

**Key Features:**
- Defensive bash: `set -euo pipefail`, input validation, clear errors
- Version tracking: Git SHA, tags, timestamps in manifest files
- Node bundling: Binary embedded in .app (macOS) or tarball (Linux)
- No prompts: Fully automated, CI-ready
- Verification: Checks outputs exist and are executable

### macOS Spawn Fix Layers

**New Protection Modules:**

1. **`freeze_utils.py`** (4.5 KB)
   - Detects PyInstaller frozen execution
   - Locates bundled binaries in .app/dist
   - Prevents `sys.executable` misuse when frozen

2. **`single_instance.py`** (3.2 KB)
   - Qt-based single-instance enforcement
   - Uses `QLocalServer`/`QLocalSocket`
   - Secondary instances raise primary and exit

3. **`startup_loop.py`** (4.0 KB)
   - Detects >5 launches in 30 seconds
   - Shows error dialog with reset option
   - Safety net for edge cases

4. **`startup_logging.py`** (2.7 KB)
   - Logs to `~/.animica/gui-miner/logs/`
   - Records PID, PPID, sys.frozen, argv
   - Stage markers for debugging

**Modified Core Files:**

1. **`main.py`**: Added all protection layers in sequence
   - Startup logging → Loop detection → Single instance → UI
   - Graceful error dialogs for loop detection
   - Releases lock on exit

2. **`miner_runner.py`**: Fixed subprocess command building
   - Never uses `sys.executable` when frozen
   - Uses bundled `animica-node` or `animica` binary
   - Falls back to Python module in dev mode

### Legacy Script Updates

Updated `apps/miner-gui/build-scripts/` to delegate:
- `build_macos.sh`: Shows 3-second deprecation warning, delegates to `ops/build/`
- `build_linux.sh`: Shows 3-second deprecation warning, delegates to `ops/build/`
- `README.md`: Prominent deprecation notice at top

Backward compatible but encourages migration.

## Root Cause: macOS Infinite Spawn

The spawn loop occurred because:

1. **Frozen executable path**: `sys.executable` points to the GUI binary when frozen
2. **Subprocess invocation**: Code used `sys.executable -m mining.cli.miner ...`
3. **Re-execution**: Each "subprocess" actually launched another GUI instance
4. **Exponential growth**: Each instance spawned more, creating infinite loop

**The Fix:**
- Detect frozen mode with `is_frozen()`
- Use bundled binary path instead: `animica-node mining mine-blocks ...`
- Raise error if binary not found (clear failure mode)

## Defense-in-Depth Strategy

Multiple independent protections ensure reliability:

```
Layer 1: freeze_support() at module level
         ↓ (Required by PyInstaller, already present)
Layer 2: Never use sys.executable when frozen
         ↓ (Use bundled binary path)
Layer 3: Single-instance enforcement
         ↓ (Prevent multiple windows)
Layer 4: Startup loop detection
         ↓ (Safety net, shows reset dialog)
Layer 5: Comprehensive logging
         ↓ (Diagnostic for edge cases)
```

Each layer is independent and contributes to overall robustness.

## Verification Steps

### Build Verification

```bash
# Build node
./ops/build/build-node-binary.sh --clean
./dist/animica-node --help

# Build GUI (macOS)
./ops/build/build-miner-gui-macos.sh --clean
open "dist/Animica Miner GUI.app"

# Verify bundled node
"dist/Animica Miner GUI.app/Contents/Resources/bin/animica-node" --help
```

### Spawn Fix Verification

1. **Single Instance**: Open GUI twice → Only one window
2. **No External RPC**: Settings show `http://127.0.0.1:8545/rpc`
3. **Bundled Node**: Logs show path to bundled binary
4. **Startup Logs**: Check `~/.animica/gui-miner/logs/startup-*.log`

## Files Changed

**New files (11):**
- `ops/build/` scripts (5 files)
- `animica_miner_gui/backend/` protections (4 files)
- `IMPLEMENTATION_SUMMARY_BUILD_SCRIPTS.md` (1 file)
- Summary doc (this file)

**Modified files (5):**
- `animica_miner_gui/main.py`
- `animica_miner_gui/backend/miner_runner.py`
- `apps/miner-gui/build-scripts/` (3 files, now delegate)

**Total additions:** ~2,360 lines (scripts, docs, protections)

## Security Considerations

- ✅ Node RPC binds to `127.0.0.1` only
- ✅ GUI hardcoded to use localhost RPC (no external connections)
- ✅ Node binary bundled inside .app (tamper resistance)
- ✅ Single-instance prevents resource exhaustion
- ✅ Auth token in `~/.animica/node.token` for local RPC

## Testing Recommendations

1. **macOS Build Test** (on actual Mac hardware)
   - Run build script
   - Verify .app and .dmg created
   - Open .app and test basic operations
   - Try opening twice (should see single window)
   - Check logs for bundled binary path

2. **Linux Build Test** (on Linux system)
   - Run build script
   - Verify binary and tarball created
   - Extract and run
   - Check for bundled node in bin/

3. **Spawn Loop Test** (optional)
   - Temporarily break single-instance guard
   - Launch app repeatedly
   - Verify loop breaker triggers after 5 launches

## Documentation

All documentation complete:
- ✅ `ops/build/README.md` - Complete build guide with examples
- ✅ `IMPLEMENTATION_SUMMARY_BUILD_SCRIPTS.md` - Technical details
- ✅ Inline code comments in all new modules
- ✅ Troubleshooting sections for common issues
- ✅ Verification instructions for testing

## Migration Path

For existing users of `apps/miner-gui/build-scripts/`:
1. Scripts show deprecation warning (3 seconds)
2. Auto-delegate to `ops/build/` scripts
3. Fully backward compatible
4. Recommendation: Update scripts/docs to use `ops/build/` directly

## CI/CD Integration

Scripts are CI-ready:
- No interactive prompts
- Exit codes indicate success/failure
- Artifacts in predictable locations (`dist/`)
- Manifest files for version tracking

Example GitHub Actions:
```yaml
- name: Build macOS
  run: ./ops/build/build-miner-gui-macos.sh --clean
- uses: actions/upload-artifact@v3
  with:
    path: dist/*.dmg
```

## Next Steps

1. **Test on macOS** (primary platform for spawn issue)
2. **Test on Linux** (verify bundled node works)
3. **Update CI/CD** to use new scripts
4. **Monitor telemetry** for any spawn issues (should be zero)
5. **Deprecate old scripts** after migration period

## Acknowledgments

This implementation follows best practices for:
- **PyInstaller freezing**: Official multiprocessing guide
- **Defensive bash**: POSIX strict mode patterns
- **Qt single instance**: Official Qt documentation
- **macOS app bundles**: Apple developer guidelines

All scripts and code are original work based on these established patterns.

---

**Ready for review and testing on actual macOS/Linux systems.**
