# Build and Bundle Documentation

This document describes the cross-platform build system for the Animica Wallet Qt application, including how the Animica node is bundled into the wallet distribution.

## Overview

The Animica Wallet uses CMake to build a Qt desktop application that includes an embedded Animica node. The build system:

1. **Builds the Qt wallet GUI** using standard Qt/CMake integration
2. **Creates a Python virtual environment** with all node dependencies
3. **Bundles the node** into the wallet output in a platform-specific way
4. **Provides build scripts** for each platform with comprehensive prerequisite checking

## Architecture

### Components

- **Qt Wallet Application**: Cross-platform GUI written in C++17 using Qt 6 (or Qt 5.15+)
- **Animica Node**: Python-based blockchain node (from `python/` and `rpc/` directories)
- **CMake Build System**: Coordinates building and bundling both components

### Build Flow

```
1. CMake Configuration
   ├─> Find Qt (6 or 5.15+)
   ├─> Find Python 3.10+
   └─> Configure wallet targets

2. Node Build (via AnimicaNode.cmake)
   ├─> Create Python venv
   ├─> Install core dependencies (FastAPI, uvicorn, prometheus)
   ├─> Install omni-sdk (from sdk/python)
   ├─> Install animica package (from python/)
   └─> Install pq package (from pq/)

3. Wallet Build
   ├─> Compile C++ sources
   ├─> Link Qt libraries
   └─> Create executable/app bundle

4. Bundling
   ├─> Copy node venv into wallet output
   ├─> Create wrapper scripts
   └─> Set executable permissions
```

## Runtime Layout

The bundled wallet has a platform-specific directory structure:

### macOS (App Bundle)

```
AnimicaWallet.app/
├── Contents/
│   ├── MacOS/
│   │   └── AnimicaWallet              # Main executable
│   ├── Resources/
│   │   └── node/
│   │       ├── venv/                   # Python virtual environment
│   │       │   ├── bin/python          # Python interpreter
│   │       │   └── lib/                # Installed packages
│   │       └── animica-node            # Wrapper script
│   └── Info.plist
```

**Node invocation:**
```bash
# From NodeManager.cpp
<app_bundle>/Contents/Resources/node/venv/bin/python -m rpc
```

### Windows

```
dist/windows/
├── animica-wallet.exe                   # Main executable
├── node/
│   ├── venv/
│   │   ├── Scripts/
│   │   │   ├── python.exe              # Python interpreter
│   │   │   └── pip.exe
│   │   └── Lib/                        # Installed packages
│   └── animica-node.bat                # Wrapper script
└── *.dll                                # Qt and system DLLs
```

**Node invocation:**
```batch
node\venv\Scripts\python.exe -m rpc
```

### Linux

```
dist/linux/
├── animica-wallet                       # Main executable
├── node/
│   ├── venv/
│   │   ├── bin/
│   │   │   ├── python                  # Python interpreter
│   │   │   └── pip
│   │   └── lib/                        # Installed packages
│   └── animica-node                    # Wrapper script
└── lib/                                 # Qt libraries (optional)
```

**Node invocation:**
```bash
node/venv/bin/python -m rpc
```

## Building

### Prerequisites

All platforms require:
- **CMake 3.16+**
- **Qt 6.2+** (or Qt 5.15+)
- **C++17 compiler** (GCC 9+, Clang 10+, MSVC 2019+)
- **Python 3.10+** with venv module

### Platform-Specific Build Scripts

Each platform has a dedicated build script with comprehensive prerequisite checking:

#### Linux

```bash
cd wallet-qt
./scripts/build-linux.sh

# Options:
#   --debug       Build in Debug mode
#   --clean       Clean build directory first
#   --qt <path>   Override Qt installation path
#   --jobs <n>    Parallel build jobs
```

#### macOS

```bash
cd wallet-qt
./scripts/build-mac.sh

# Options: same as Linux
```

#### Windows

```powershell
cd wallet-qt
.\scripts\build-windows.ps1

# Options:
#   -Debug        Build in Debug mode
#   -Clean        Clean build directory first
#   -QtPath       Override Qt installation path
#   -Jobs         Parallel build jobs
```

### Manual CMake Build

If you prefer to use CMake directly:

```bash
cd wallet-qt
mkdir -p build
cd build

# Configure
cmake .. -DCMAKE_BUILD_TYPE=Release

# Or with custom Qt path
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/path/to/qt6

# Build
cmake --build . -j $(nproc)

# Output will be in build/bin/
```

## Node Detection in NodeManager

The `NodeManager` class automatically detects the bundled Python:

```cpp
QString NodeManager::findBundledPython()
{
    QString appDir = QCoreApplication::applicationDirPath();
    QString bundledPython;
    
#ifdef Q_OS_MACOS
    // macOS: AnimicaWallet.app/Contents/Resources/node/venv/bin/python
    bundledPython = appDir + "/../Resources/node/venv/bin/python";
#elif defined(Q_OS_WIN)
    // Windows: <exe_dir>/node/venv/Scripts/python.exe
    bundledPython = appDir + "/node/venv/Scripts/python.exe";
#else
    // Linux: <exe_dir>/node/venv/bin/python
    bundledPython = appDir + "/node/venv/bin/python";
#endif
    
    if (QFileInfo(bundledPython).exists()) {
        return bundledPython;
    }
    
    return QString(); // Fall back to system Python
}
```

## Development Workflow

### Iterative Development

When developing the wallet, you don't need to rebuild the node every time:

1. **First build**: Runs full node build (~2-5 minutes)
2. **Subsequent builds**: Node venv is cached, only wallet rebuilds

To force a node rebuild:
```bash
rm -rf build/animica-node
cmake --build build
```

### Debugging

To debug the bundled node:

1. **Check bundle contents:**
   ```bash
   # macOS
   ls -la build/bin/AnimicaWallet.app/Contents/Resources/node/
   
   # Linux
   ls -la build/bin/node/
   ```

2. **Test node directly:**
   ```bash
   # macOS
   build/bin/AnimicaWallet.app/Contents/Resources/node/venv/bin/python -m rpc --help
   
   # Linux
   build/bin/node/venv/bin/python -m rpc --help
   ```

3. **Check installed packages:**
   ```bash
   # macOS
   build/bin/AnimicaWallet.app/Contents/Resources/node/venv/bin/pip list
   ```

## Distribution

### Creating Distribution Packages

After a successful build, create distribution packages:

#### Linux (AppImage, .deb, and portable tarball)

```bash
cd wallet-qt
./scripts/release-linux.sh
```

#### macOS (DMG)

```bash
cd wallet-qt

# Install create-dmg if needed
brew install create-dmg

# Create DMG
create-dmg \
  --volname "Animica Wallet" \
  --window-size 600 400 \
  --icon-size 100 \
  --app-drop-link 450 150 \
  dist/AnimicaWallet-mac-x64.dmg \
  build/mac/bin/AnimicaWallet.app
```

#### Windows (Installer or ZIP)

```powershell
cd wallet-qt
New-Item -ItemType Directory -Force -Path dist\windows

# Copy artifacts
Copy-Item -Recurse build\windows\bin\Release\* dist\windows\

# Copy Qt DLLs (adjust path to your Qt installation)
$QtBin = "$env:CMAKE_PREFIX_PATH\bin"
Copy-Item "$QtBin\Qt6Core.dll" dist\windows\
Copy-Item "$QtBin\Qt6Widgets.dll" dist\windows\
Copy-Item "$QtBin\Qt6Network.dll" dist\windows\
# ... (copy other required DLLs)

# Create ZIP
Compress-Archive -Path dist\windows\* -DestinationPath dist\AnimicaWallet-win-x64.zip
```

## Versioning

The wallet build includes version information compiled in:

- **WALLET_VERSION**: From CMake project version (0.1.0)
- **GIT_COMMIT_HASH**: Short commit hash from git
- **BUILD_TIMESTAMP**: Build date/time in UTC

Access in code:
```cpp
#ifdef WALLET_VERSION
    QString version = WALLET_VERSION;
#endif
```

## Troubleshooting

### Node Not Found at Runtime

**Symptom**: "Python 3.11+ not found" error when starting wallet

**Solution**:
1. Check that node was bundled:
   ```bash
   ls -la build/bin/node/venv/bin/python  # Linux
   ```
2. Rebuild with verbose output:
   ```bash
   cmake --build build --verbose
   ```
3. Check CMake output for "Building Animica Node" section

### Build Fails During Node Install

**Symptom**: pip install errors during CMake build

**Solution**:
1. Check Python version: `python3 --version` (must be 3.10+)
2. Check internet connection (pip needs to download packages)
3. Check venv module: `python3 -m venv --help`
4. Try manual venv creation:
   ```bash
   python3 -m venv test-venv
   source test-venv/bin/activate
   pip install fastapi uvicorn
   ```

### Qt Not Found

**Symptom**: "Qt6 not found" error

**Solution**:
1. Install Qt or use `--qt` flag:
   ```bash
   ./scripts/build-linux.sh --qt /path/to/qt6
   ```
2. Set CMAKE_PREFIX_PATH:
   ```bash
   export CMAKE_PREFIX_PATH=/path/to/qt6
   cmake ..
   ```

## CI/CD Integration

See [ci_build.md](ci_build.md) for CI-specific build instructions and GitHub Actions integration.

## References

- **Qt CMake Integration**: https://doc.qt.io/qt-6/cmake-manual.html
- **Python venv**: https://docs.python.org/3/library/venv.html
- **CMake Custom Commands**: https://cmake.org/cmake/help/latest/command/add_custom_command.html
