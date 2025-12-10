# liboqs/python-oqs Loading Improvements

## Summary

Enhanced the liboqs library loading mechanism to properly detect and load liboqs from python-oqs wheel bundled locations, system paths, and custom environment variables. Added comprehensive diagnostics and clear error messages to help users troubleshoot installation issues.

## Problem Statement

Users reported that even after installing liboqs and python-oqs successfully, CLI commands would fail to find the library and show confusing errors. The root causes were:

1. **Incomplete search paths**: The loader didn't check python-oqs wheel bundled library locations
2. **Poor diagnostic output**: Users couldn't tell which paths were being searched or why loading failed
3. **No fallback detection**: When the `oqs` Python module wasn't available, the ctypes backend wasn't used as a fallback

## Solution

### 1. Enhanced Library Search (`pq/py/algs/oqs_backend.py`)

Added `_get_python_oqs_bundled_lib_paths()` function that detects where python-oqs installed its bundled liboqs:

```python
def _get_python_oqs_bundled_lib_paths() -> List[str]:
    """
    Get paths where python-oqs might have bundled liboqs.
    
    When python-oqs (liboqs-python) is installed via wheel, it may bundle
    the liboqs shared library in its package directory.
    """
```

The loader now searches in this order:
1. **LIBOQS_PATH** environment variable (explicit override)
2. **Python-oqs bundled paths** (from wheel installation)
3. **System library search** via `find_library("oqs")`
4. **Common library names** (liboqs.so, liboqs.dylib, versioned SONAMEs)
5. **Environment paths** (LD_LIBRARY_PATH/DYLD_LIBRARY_PATH)

### 2. Improved Diagnostics (`python/animica/cli/pq_utils.py`)

Added `get_pq_diagnostics()` function that provides detailed status:

```
PQ Library Diagnostics
==================================================
✗ python-oqs (oqs module): not installed
✗ liboqs (ctypes backend): not loaded

Environment Variables:
  LD_LIBRARY_PATH: (not set)
  DYLD_LIBRARY_PATH: (not set)
  LIBRARY_PATH: (not set)
  LIBOQS_PATH: (not set)
  ANIMICA_UNSAFE_PQ_FAKE: (not set)
```

### 3. Enhanced `check_pq_signing_available()`

Now falls back to checking `oqs_backend` when the `oqs` module isn't available:

```python
except ImportError as e:
    # Also check if oqs_backend can load liboqs directly via ctypes
    try:
        from pq.py.algs import oqs_backend
        if oqs_backend.is_available():
            version = oqs_backend.get_version_info()
            logger.info(f"✓ liboqs loaded via ctypes backend: version {version}")
            return True, None
    except Exception:
        pass
```

### 4. Detailed Error Messages

When liboqs can't be loaded, users now see:

```
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
```

## Installation Scenarios

### Scenario 1: System Package Installation (Recommended)

```bash
# Ubuntu/Debian
sudo apt-get install liboqs-dev
pip install liboqs-python

# macOS
brew install liboqs
pip install liboqs-python
```

**Result**: The loader finds liboqs via system paths, no environment variables needed.

### Scenario 2: python-oqs Wheel with Bundled liboqs

```bash
pip install liboqs-python
```

Some python-oqs wheels bundle liboqs. The enhanced loader now checks:
- `<oqs-module-dir>/liboqs.so*`
- `<oqs-module-dir>/.libs/liboqs.so*`
- `<oqs-module-dir>/lib/liboqs.so*`

**Result**: Loader finds and uses the bundled library automatically.

### Scenario 3: Custom Build from Source

```bash
# Build liboqs
cd /opt
git clone --branch 0.15.0 https://github.com/open-quantum-safe/liboqs.git
cd liboqs && mkdir build && cd build
cmake -DCMAKE_INSTALL_PREFIX=/opt/liboqs ..
make -j$(nproc) && make install

# Set environment
export LD_LIBRARY_PATH=/opt/liboqs/lib:$LD_LIBRARY_PATH
# or
export LIBOQS_PATH=/opt/liboqs/lib/liboqs.so

# Install python-oqs
pip install liboqs-python
```

**Result**: Loader uses `LD_LIBRARY_PATH` or `LIBOQS_PATH` to find the custom build.

### Scenario 4: Animica setup.sh

```bash
./setup.sh
source .liboqs/env.sh
```

**Result**: The setup script builds liboqs in `.liboqs/install/` and sets environment variables.

## Environment Variables

### Priority Order

1. **LIBOQS_PATH** - Direct path to liboqs shared library (highest priority)
   ```bash
   export LIBOQS_PATH=/path/to/liboqs.so
   ```

2. **LD_LIBRARY_PATH** (Linux) / **DYLD_LIBRARY_PATH** (macOS) - Library search paths
   ```bash
   export LD_LIBRARY_PATH=/path/to/liboqs/lib:$LD_LIBRARY_PATH
   ```

3. **System defaults** - Standard locations checked by `find_library()`

### Development/Testing Variables

- **ANIMICA_UNSAFE_PQ_FAKE=1** - Enable insecure pure-Python fallbacks (NOT for production)
- **ANIMICA_ALLOW_PQ_PURE_FALLBACK=1** - Allow pure-Python implementations when native unavailable

## Verification

### Check PQ Availability

```bash
python3 -c "
from animica.cli.pq_utils import check_pq_signing_available, get_pq_diagnostics

available, error = check_pq_signing_available()
print('Available:', available)
if error:
    print('Error:', error)
print()
print(get_pq_diagnostics())
"
```

### Verify python-oqs Installation

```bash
python3 -c "
import oqs
print('python-oqs version:', oqs.__version__)
mechs = oqs.get_enabled_sig_mechanisms()
print('SPHINCS+ variants:', [m for m in mechs if 'SPHINCS' in m])
"
```

### Check liboqs Backend

```bash
python3 -c "
from pq.py.algs import oqs_backend
print('Backend available:', oqs_backend.is_available())
print('liboqs version:', oqs_backend.get_version_info())
"
```

## Testing

Added comprehensive tests covering:

1. **Library loading from python-oqs bundled paths** (`test_load_from_python_oqs_bundled_path`)
2. **Bundled path detection** (`test_get_python_oqs_bundled_lib_paths`)
3. **Fallback to oqs_backend** when oqs module unavailable
4. **Diagnostic output** with various environment configurations
5. **Error messages** include environment variable state

Run tests:
```bash
pytest pq/tests/test_oqs_backend_loader.py -xvs
pytest python/animica/cli/tests/test_pq_utils.py -xvs
```

## Breaking Changes

None. All changes are backward compatible:
- Existing LIBOQS_PATH/LD_LIBRARY_PATH configurations continue to work
- No changes to public APIs
- Enhanced error messages are additive

## Future Improvements

1. **Auto-detection of more wheel formats**: Currently checks common patterns, could be extended
2. **Per-platform optimizations**: Could add platform-specific search paths
3. **Configuration file support**: Allow specifying library paths in config file
4. **Health check command**: Add `animica pq check` command for diagnostics

## References

- [liboqs GitHub](https://github.com/open-quantum-safe/liboqs)
- [python-oqs Documentation](https://github.com/open-quantum-safe/liboqs-python)
- Problem statement: Fix CLI commands to locate installed liboqs without triggering fallback installers
