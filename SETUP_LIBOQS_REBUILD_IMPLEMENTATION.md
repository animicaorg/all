# Setup.sh liboqs Rebuild Implementation

## Summary

Modified `setup.sh` to **always rebuild liboqs** on every run by default, ensuring a fresh, known-good installation. Added an optional `--skip-liboqs-rebuild` flag for faster repeated runs when liboqs rebuild is not needed.

## Problem Solved

Previously, `setup.sh` would skip rebuilding liboqs if a previous build or system library was present, which could lead to:
- Stale or mismatched liboqs versions
- Inconsistent behavior across different environments
- Difficult debugging when library versions don't match expectations

## Implementation Details

### Changes to setup.sh

#### 1. Command-Line Flag Parsing (Lines 13-26)
```bash
SKIP_LIBOQS_REBUILD=false
for arg in "$@"; do
  case "$arg" in
    --skip-liboqs-rebuild)
      SKIP_LIBOQS_REBUILD=true
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--skip-liboqs-rebuild]"
      exit 1
      ;;
  esac
done
```
- Adds flag support for opting out of rebuild
- Shows usage message for unknown arguments

#### 2. Forced Rebuild Logic (Lines 283-300)
```bash
# Strategy: Always build liboqs from source to ensure fresh, known-good installation
# unless --skip-liboqs-rebuild flag is passed
if [[ "$SKIP_LIBOQS_REBUILD" == "false" ]]; then
  log "Building liboqs from source (use --skip-liboqs-rebuild to skip on repeated runs)..."
  build_liboqs_from_source
else
  log "Skipping liboqs rebuild (--skip-liboqs-rebuild flag set)"
  local install_prefix="$LIBOQS_DIR/install"
  if [[ -d "$install_prefix" ]]; then
    log "Using existing liboqs installation at $install_prefix"
    setup_liboqs_env_vars "$install_prefix"
  else
    warn "No existing liboqs installation found at $install_prefix"
    log "Building liboqs from source (required for first-time setup)..."
    build_liboqs_from_source
  fi
fi
```
- Default behavior: always builds from source
- With flag: uses existing installation if present
- Falls back to building if no installation exists

#### 3. Directory Cleanup (Lines 100-103)
```bash
# Always clean up previous builds to ensure fresh installation
if [[ -d "$LIBOQS_DIR" ]]; then
  log "Removing previous liboqs build at $LIBOQS_DIR"
  rm -rf "$LIBOQS_DIR"
fi
```
- Changed from "Clean up any partial builds" to emphasize always rebuilding
- Ensures fresh clone and build every time

#### 4. Enhanced Logging (Lines 87-91, 159-178)
```bash
log "════════════════════════════════════════════════════════════════════════"
log "Building liboqs from source (this may take a few minutes)..."
log "Using liboqs version: $LIBOQS_VERSION"
log "Repository: $LIBOQS_REPO"
log "════════════════════════════════════════════════════════════════════════"

# ... after build ...

log ""
log "════════════════════════════════════════════════════════════════════════"
log "✓ liboqs v${LIBOQS_VERSION} successfully built and installed"
log "  Install location: $install_prefix"
log "  Library path: $install_prefix/lib"
log "  Include path: $install_prefix/include"
log ""
log "Environment variables have been set for the current session."
```
- Added visual separators for clarity
- Shows repository URL during build
- Clearly displays install paths
- Confirms environment variable setup

### Test Coverage

#### Existing Tests (Updated)
- **tests/test_setup_sh.sh**: Updated error message check to match new text
  - All 10 test cases pass ✓

#### New Tests
- **tests/test_setup_sh_rebuild.sh**: Tests rebuild-specific behavior
  - Flag parsing validation ✓
  - Forced rebuild logic ✓
  - Skip logic when flag is set ✓
  - Directory cleanup verification ✓
  - Logging improvements ✓
  - Usage message validation ✓
  - Environment variables always set ✓
  
- **tests/manual_test_setup_rebuild.sh**: Manual demonstration script
  - Shows expected behavior with and without flag
  - Can be used for manual verification

## Usage Examples

### Default Behavior (Always Rebuild)
```bash
./setup.sh
```
**Output:**
```
[setup] ════════════════════════════════════════════════════════════════════════
[setup] Building liboqs from source (this may take a few minutes)...
[setup] Using liboqs version: 0.15.0
[setup] Repository: https://github.com/open-quantum-safe/liboqs.git
[setup] ════════════════════════════════════════════════════════════════════════
[setup] Build prerequisites check passed (cmake, make, C compiler)
[setup] Removing previous liboqs build at /path/to/repo/.liboqs
[setup] Cloning liboqs v0.15.0 from https://github.com/open-quantum-safe/liboqs.git...
[setup] Configuring liboqs build (shared libs, no OpenSSL dependency)...
[setup] Building liboqs with 8 parallel jobs...
[setup] Installing liboqs to /path/to/repo/.liboqs/install...
[setup] Set library environment variables for liboqs at /path/to/repo/.liboqs/install
[setup] 
[setup] ════════════════════════════════════════════════════════════════════════
[setup] ✓ liboqs v0.15.0 successfully built and installed
[setup]   Install location: /path/to/repo/.liboqs/install
[setup]   Library path: /path/to/repo/.liboqs/install/lib
[setup]   Include path: /path/to/repo/.liboqs/install/include
```

### Skip Rebuild (Faster Repeated Runs)
```bash
./setup.sh --skip-liboqs-rebuild
```
**Output:**
```
[setup] Skipping liboqs rebuild (--skip-liboqs-rebuild flag set)
[setup] Using existing liboqs installation at /path/to/repo/.liboqs/install
[setup] Set library environment variables for liboqs at /path/to/repo/.liboqs/install
```

### Help/Usage
```bash
./setup.sh --help
```
**Output:**
```
Unknown argument: --help
Usage: setup.sh [--skip-liboqs-rebuild]
```

## Behavior Comparison

### Before This Change
| Run | Condition | Behavior |
|-----|-----------|----------|
| 1st | No liboqs | Builds from source |
| 2nd | .liboqs/ exists | **Skips rebuild** (uses existing) |
| 3rd | .liboqs/ exists | **Skips rebuild** (uses existing) |

### After This Change
| Run | Flag | Condition | Behavior |
|-----|------|-----------|----------|
| 1st | none | No liboqs | Builds from source |
| 2nd | none | .liboqs/ exists | **Rebuilds** (removes old, builds fresh) |
| 3rd | none | .liboqs/ exists | **Rebuilds** (removes old, builds fresh) |
| Any | --skip-liboqs-rebuild | .liboqs/ exists | Uses existing (sets env vars only) |
| Any | --skip-liboqs-rebuild | No liboqs | Builds from source (required) |

## Environment Variables

The following environment variables are set on **every** run:

- `LIBRARY_PATH` - For linking
- `PKG_CONFIG_PATH` - For pkg-config
- `C_INCLUDE_PATH` - For C headers
- `CPLUS_INCLUDE_PATH` - For C++ headers
- `LD_LIBRARY_PATH` (Linux) or `DYLD_LIBRARY_PATH` (macOS) - For runtime loading

These are set in the current session. For future sessions, use:
```bash
source .liboqs/env.sh
```

## Acceptance Criteria Met

✅ **Always rebuilds liboqs by default** - Removes `.liboqs/` directory before building
✅ **Opt-out flag provided** - `--skip-liboqs-rebuild` flag for faster repeated runs
✅ **Valid liboqs release** - Uses `0.15.0` tag from official repository
✅ **Environment variables updated** - Set on every run via `setup_liboqs_env_vars()`
✅ **Clear logging** - Shows rebuild status, install location, library paths
✅ **Actionable errors** - Prerequisites checked, clear error messages if missing
✅ **Idempotent** - Can run multiple times safely, always produces same result
✅ **Other setup steps unchanged** - Only modified liboqs-related portions
✅ **Tests pass** - All existing tests pass, new tests added for rebuild behavior

## Benefits

1. **Consistency**: Every setup run uses the exact same liboqs version
2. **Reproducibility**: Known-good state on every run
3. **Debugging**: Eliminates "it works on my machine" issues related to liboqs
4. **Flexibility**: Can opt out for faster iteration when liboqs isn't changing
5. **CI/CD Friendly**: Always rebuilds in CI ensures clean environment

## Migration Notes

For users who previously relied on skipping the rebuild:
- Add `--skip-liboqs-rebuild` flag to setup.sh invocations for faster re-runs
- First run will always rebuild to establish known-good state
- No breaking changes to other functionality

## Technical Details

- **liboqs version**: 0.15.0 (pinned, no "v" prefix as per liboqs conventions)
- **Repository**: https://github.com/open-quantum-safe/liboqs.git
- **Install location**: `.liboqs/install/` (local to repository, no sudo needed)
- **Build type**: Release (optimized)
- **Shared libraries**: Yes (`BUILD_SHARED_LIBS=ON`)
- **OpenSSL dependency**: No (`OQS_USE_OPENSSL=OFF`)
- **Parallel build**: Uses all available CPU cores (`nproc` or `sysctl -n hw.ncpu`)

## Files Modified

1. **setup.sh** - Main setup script
   - Added flag parsing
   - Changed rebuild strategy to always build
   - Enhanced logging
   
2. **tests/test_setup_sh.sh** - Existing test suite
   - Updated error message check

3. **tests/test_setup_sh_rebuild.sh** - New test suite (NEW)
   - Tests rebuild-specific behavior
   
4. **tests/manual_test_setup_rebuild.sh** - Manual demonstration (NEW)
   - Shows expected behavior and usage examples

## Performance Impact

- **First run**: Same as before (~3-5 minutes to build liboqs)
- **Subsequent runs without flag**: ~3-5 minutes (rebuilds liboqs each time)
- **Subsequent runs with flag**: ~1-2 seconds (skips rebuild, sets env vars only)

For development workflows where liboqs doesn't change, use `--skip-liboqs-rebuild` to save time.
