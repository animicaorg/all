# liboqs/python-oqs CLI Loading Fix - Summary

## Executive Summary

Successfully fixed CLI commands to properly detect and load liboqs from all installation methods (system packages, python-oqs wheels, custom builds) without triggering unnecessary fallback installers. Added comprehensive diagnostics and clear error messages to help users troubleshoot installation issues.

## Problem

Users reported that even after successful liboqs installation, CLI commands (wallet, tx, key) would:
- Fail to find the installed liboqs library
- Show confusing error messages
- Not provide actionable troubleshooting steps

Root causes identified:
1. Loader didn't check python-oqs wheel bundled library locations
2. No fallback to ctypes backend when oqs module unavailable
3. Poor diagnostic output - users couldn't see which paths were searched
4. Missing environment variable hints in error messages

## Solution Implemented

### 1. Enhanced Library Loading (`pq/py/algs/oqs_backend.py`)

**New Function**: `_get_python_oqs_bundled_lib_paths()`
- Detects python-oqs package location
- Searches for bundled liboqs in: `<oqs-dir>/`, `<oqs-dir>/.libs/`, `<oqs-dir>/lib/`
- Returns list of candidate paths

**Improved**: `_load_liboqs()` now uses 5-step priority search:
```
1. LIBOQS_PATH env var (explicit override)
   ↓ not found
2. Python-oqs bundled paths (wheel installations)
   ↓ not found
3. System library search (find_library)
   ↓ not found
4. Common library names (liboqs.so, liboqs.dylib, versioned SONAMEs)
   ↓ not found
5. Returns None with detailed diagnostic message
```

**Enhanced Logging**:
- Step-by-step progress with ✓/✗ indicators
- Environment variable values logged
- Number of candidates checked at each step
- Detailed failure message with actionable steps

### 2. Improved PQ Detection (`python/animica/cli/pq_utils.py`)

**New Function**: `get_pq_diagnostics()`
```
PQ Library Diagnostics
==================================================
✓ python-oqs (oqs module): installed (version 0.10.0)
  ✓ SPHINCS+ mechanisms: SPHINCS+-SHAKE-128s
✗ liboqs (ctypes backend): not loaded

Environment Variables:
  LD_LIBRARY_PATH: /usr/local/lib
  DYLD_LIBRARY_PATH: (not set)
  LIBOQS_PATH: (not set)
  ANIMICA_UNSAFE_PQ_FAKE: (not set)
```

**Enhanced**: `check_pq_signing_available()`
- Now checks both oqs module AND oqs_backend
- Falls back to oqs_backend if oqs module not available
- Logs which method succeeded
- Returns detailed error information

**Improved Error Messages**:
- Include diagnostics automatically
- Show environment variable state
- List all installation methods with examples
- Platform-specific instructions (Linux/macOS)
- Reference to setup.sh for project builds

### 3. Comprehensive Testing

**Unit Tests** (`pq/tests/test_oqs_backend_loader.py`, `python/animica/cli/tests/test_pq_utils.py`):
- Test bundled path detection
- Test load priority sequence
- Test diagnostics output
- Test environment variable handling
- Test fallback mechanisms

**Integration Tests** (`tests/integration/test_liboqs_loading.py`):
- Wallet creation without liboqs (enhanced errors)
- Wallet creation with fake mode (fallback works)
- Diagnostics with various env configs
- Load sequence priority verification
- Comprehensive error messages

All tests pass ✅

### 4. Documentation

**LIBOQS_LOADING_FIX.md**: Comprehensive guide covering:
- Installation scenarios with examples
- Environment variable priority
- Verification commands
- Troubleshooting steps
- Testing instructions

## Installation Scenarios Supported

### ✅ System Package (Recommended)
```bash
# Ubuntu/Debian
sudo apt-get install liboqs-dev
pip install liboqs-python

# macOS
brew install liboqs
pip install liboqs-python
```
**Result**: Loader finds via system paths automatically

### ✅ Python-oqs Wheel with Bundled liboqs
```bash
pip install liboqs-python
```
**Result**: Loader now detects and uses bundled library

### ✅ Custom Build from Source
```bash
export LD_LIBRARY_PATH=/opt/liboqs/lib:$LD_LIBRARY_PATH
# or
export LIBOQS_PATH=/opt/liboqs/lib/liboqs.so
pip install liboqs-python
```
**Result**: Loader respects environment variables

### ✅ Animica setup.sh
```bash
./setup.sh
source .liboqs/env.sh
```
**Result**: Setup script builds liboqs, sets env vars

## Environment Variables

Priority order (highest to lowest):
1. **LIBOQS_PATH** - Direct path to .so/.dylib
2. **LD_LIBRARY_PATH** (Linux) / **DYLD_LIBRARY_PATH** (macOS)
3. Python-oqs bundled locations
4. System library search paths

Development variables:
- **ANIMICA_UNSAFE_PQ_FAKE=1** - Enable pure-Python fallbacks (dev/test only)

## Verification

### Check Status
```bash
python3 -c "
from animica.cli.pq_utils import check_pq_signing_available, get_pq_diagnostics
print(get_pq_diagnostics())
"
```

### Test Wallet Command
```bash
# Should show enhanced error if liboqs missing
animica wallet create --label test

# Or use insecure fallback for testing
animica wallet create --label test --allow-insecure-fallback
```

## Example Error Output

**Before** (confusing):
```
Error: PQ not available
```

**After** (actionable):
```
Error: Post-quantum signing dependencies not available.

liboqs shared library not found after searching:
  - LIBOQS_PATH environment variable: (not set)
  - python-oqs wheel bundled paths: 0 checked
  - System library search: 6 candidates
  - Environment: LD_LIBRARY_PATH/DYLD_LIBRARY_PATH not set

To fix:
  1. Install liboqs-dev (apt/brew) or build from source
  2. Install python-oqs: pip install liboqs-python
  3. Set library path if needed:
     - Linux: export LD_LIBRARY_PATH=/path/to/liboqs/lib:$LD_LIBRARY_PATH
     - macOS: export DYLD_LIBRARY_PATH=/path/to/liboqs/lib:$DYLD_LIBRARY_PATH
  4. Or set LIBOQS_PATH=/path/to/liboqs.so directly

PQ Library Diagnostics
==================================================
✗ python-oqs (oqs module): not installed
✗ liboqs (ctypes backend): not loaded

Environment Variables:
  LD_LIBRARY_PATH: (not set)
  DYLD_LIBRARY_PATH: (not set)
  LIBOQS_PATH: (not set)
  ANIMICA_UNSAFE_PQ_FAKE: (not set)
```

## Files Changed

1. `pq/py/algs/oqs_backend.py` - Enhanced library loading
2. `python/animica/cli/pq_utils.py` - Improved PQ detection and diagnostics
3. `pq/tests/test_oqs_backend_loader.py` - Added bundled path tests
4. `python/animica/cli/tests/test_pq_utils.py` - Added diagnostics tests
5. `tests/integration/test_liboqs_loading.py` - Comprehensive integration tests
6. `LIBOQS_LOADING_FIX.md` - Detailed documentation
7. `LIBOQS_FIX_SUMMARY.md` - This summary

## Acceptance Criteria

✅ **On machines with liboqs/python-oqs installed**: CLI commands load successfully  
✅ **No unnecessary fallback installers**: None exist (were already removed)  
✅ **Clear logging**: Shows which path/version is loaded  
✅ **Actionable errors**: Include environment variable hints  
✅ **Comprehensive tests**: Cover success, failure, and path resolution scenarios  
✅ **Backward compatible**: Existing configurations still work  

## Benefits

1. **Better User Experience**: Clear, actionable error messages
2. **Wider Compatibility**: Works with wheel-bundled liboqs
3. **Easier Troubleshooting**: Diagnostics show exactly what's wrong
4. **More Robust**: Multiple fallback paths for finding liboqs
5. **Better Logging**: Detailed progress for debugging

## No Fallback Installer Issues

**Finding**: No runtime fallback installers were found in the codebase. The `setup.sh` script properly uses liboqs v0.15.0 tag. The "fallback installer" issue mentioned in the problem statement was already resolved in previous work.

This fix focuses on **detection and loading**, not installation.

## Security

- No security vulnerabilities introduced (CodeQL clean)
- Improved error handling (no silent failures)
- Clear separation of dev/test fallbacks (ANIMICA_UNSAFE_PQ_FAKE)
- Proper environment variable validation

## Future Enhancements

1. Auto-detect more wheel formats (different packaging schemes)
2. Platform-specific search path optimizations
3. Configuration file support for library paths
4. Health check CLI command (`animica pq check`)
5. Automatic liboqs version compatibility checking

## Conclusion

Successfully resolved the liboqs loading issues reported by users. CLI commands now properly detect liboqs from all common installation methods and provide clear, actionable feedback when issues occur. The solution is backward compatible, well-tested, and documented.

Users can now:
- Install liboqs via any method and have it work
- Understand exactly what's wrong when it doesn't work
- Follow clear steps to fix their installation
- Use comprehensive diagnostics for troubleshooting
