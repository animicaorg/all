# Setup.sh liboqs Installation Improvements

## Overview

This document describes the improvements made to `setup.sh` to provide a reliable fallback mechanism for installing `liboqs-python` when prebuilt wheels or system liboqs packages are not available.

## Problem Statement

Previously, if `liboqs-python` installation failed during setup, the script would:
1. Build liboqs from source
2. Retry the installation
3. Show warnings but continue even if the retry failed
4. Leave users without clear guidance on what went wrong or how to fix it

## Solution

The improved setup.sh now implements a robust three-tier installation strategy:

### 1. Fast Path (Prebuilt Wheels or System Package)
```bash
pip install liboqs-python
```
- Attempts direct installation first
- Success: Logs confirmation and continues
- Failure: Proceeds to fallback

### 2. Fallback Path (Build from Source)
When the fast path fails:

1. **Prerequisite Validation**
   - Checks for git (required for cloning)
   - Checks for cmake, make, gcc/clang (required for building)
   - Provides platform-specific installation instructions if missing

2. **Source Build**
   - Clones pinned liboqs version (0.15.0) from official repository
   - Builds with secure options:
     - `BUILD_SHARED_LIBS=ON` - Creates shared libraries
     - `OQS_USE_OPENSSL=OFF` - Reduces dependencies
     - `CMAKE_BUILD_TYPE=Release` - Optimized build
   - Installs to local prefix: `.liboqs/install/`

3. **Environment Setup**
   - Exports necessary environment variables:
     - `LIBRARY_PATH` - For linking
     - `PKG_CONFIG_PATH` - For pkg-config
     - `C_INCLUDE_PATH` / `CPLUS_INCLUDE_PATH` - For headers
     - `LD_LIBRARY_PATH` (Linux) or `DYLD_LIBRARY_PATH` (macOS) - For runtime
   - Creates convenience script `.liboqs/env.sh` for future sessions

4. **Retry Installation**
   - Retries `pip install liboqs-python --no-cache-dir`
   - Uses mktemp for secure temporary file creation
   - Captures output for debugging

### 3. Error Path (Installation Still Fails)
If installation fails even after building from source:

- **Exits with non-zero status** (fail immediately, don't continue)
- Provides comprehensive error message with:
  - Build location
  - Log file location
  - Possible causes
  - Debugging steps
  - Development workaround (ANIMICA_UNSAFE_PQ_FAKE=1)

## Key Improvements

### Security Enhancements
- ✓ Uses `mktemp` for secure temporary file creation
- ✓ All variables are properly quoted
- ✓ Uses pinned version for liboqs (no arbitrary code execution)
- ✓ Builds to local prefix (no sudo required)

### User Experience
- ✓ Clear visual indicators (checkmarks) for successful operations
- ✓ Actionable error messages with platform-specific instructions
- ✓ Prominent reuse instructions for future sessions
- ✓ Convenience script for easy environment setup

### Reliability
- ✓ Proper exit codes on all failure paths
- ✓ Validates all prerequisites before attempting build
- ✓ Uses `--no-cache-dir` to avoid cached failures
- ✓ Captures logs for troubleshooting

## Testing

### Automated Tests
Created comprehensive test suite in `tests/test_setup_sh.sh`:
- ✓ Bash syntax validation
- ✓ Function existence checks
- ✓ Error message validation
- ✓ Exit code verification
- ✓ Environment variable checks
- ✓ Convenience script creation
- ✓ Retry logic validation
- ✓ Logging verification
- ✓ Version pinning
- ✓ CMake options

### Manual Testing Scenarios
1. **Clean install** - No liboqs, no prebuilt wheel
2. **Missing prerequisites** - No cmake, no git, no compiler
3. **Existing liboqs** - System package or previous build
4. **Existing wheel** - Prebuilt wheel available

## Usage

### Normal Setup
```bash
./setup.sh
```

### Reusing Built liboqs
After setup completes, the environment variables are set for the current session.
For future sessions, source the convenience script:

```bash
source .liboqs/env.sh
```

Or add to your shell profile:
```bash
# In ~/.bashrc or ~/.zshrc
export LIBRARY_PATH="/path/to/repo/.liboqs/install/lib:$LIBRARY_PATH"
export PKG_CONFIG_PATH="/path/to/repo/.liboqs/install/lib/pkgconfig:$PKG_CONFIG_PATH"
export C_INCLUDE_PATH="/path/to/repo/.liboqs/install/include:$C_INCLUDE_PATH"
export LD_LIBRARY_PATH="/path/to/repo/.liboqs/install/lib:$LD_LIBRARY_PATH"  # Linux
# or
export DYLD_LIBRARY_PATH="/path/to/repo/.liboqs/install/lib:$DYLD_LIBRARY_PATH"  # macOS
```

## Acceptance Criteria Met

✅ Fast path attempts `pip install liboqs-python` first  
✅ On failure, detects cmake and C toolchain; fails with actionable guidance if missing  
✅ If tools exist, clones pinned liboqs release to temp dir  
✅ Builds shared libs with correct cmake options  
✅ Exports environment variables (LIBRARY_PATH, LD_LIBRARY_PATH, DYLD_LIBRARY_PATH)  
✅ Retries `pip install liboqs-python` after building  
✅ Logs success or exits with clear error on final failure  
✅ Leaves other setup steps intact  
✅ Adds logs explaining build location and reuse instructions  
✅ Includes tests to validate setup script behavior  

## Platform Support

### Ubuntu/Debian
Prerequisites: `sudo apt-get install git cmake build-essential`

### macOS
Prerequisites: `brew install git cmake` or Xcode Command Line Tools

### Fedora/RHEL
Prerequisites: `sudo dnf install git cmake gcc make`

## Files Modified

1. **setup.sh** - Main setup script with improved liboqs installation logic
2. **tests/test_setup_sh.sh** - Comprehensive test suite (new file)
3. **.liboqs/** - Build directory (generated, in .gitignore)
   - `install/` - Installation prefix for built liboqs
   - `src/` - Cloned liboqs source
   - `build/` - CMake build directory
   - `env.sh` - Convenience script for environment variables

## Future Improvements

Potential enhancements for future iterations:
- Cache built liboqs across CI runs
- Support for custom liboqs version via environment variable
- Automated cleanup of old builds
- Progress indicators during lengthy build process
- Verification of built library before retry
