# CI Build Documentation

This document describes how to build the Animica Wallet in CI environments (GitHub Actions, GitLab CI, etc.) with a focus on reproducibility and clean-room builds.

## Overview

The wallet build is designed to work in ephemeral CI runners with minimal dependencies. All required tools are standard and can be installed via package managers.

## Requirements

### All Platforms

- **CMake 3.16+**
- **Qt 6.2+** (or Qt 5.15+)
- **Python 3.10+** with venv and pip
- **C++17 compiler**
- **Internet connection** (for Python package installation during build)

### Platform-Specific

#### Ubuntu/Debian Runners

```yaml
- build-essential (g++, make)
- cmake
- qt6-base-dev
- qt6-tools-dev
- libqt6network6
- python3.11
- python3.11-venv
- python3-pip
```

#### macOS Runners

```yaml
- Xcode Command Line Tools
- Homebrew (for installing Qt and CMake)
- Python 3.10+ (usually pre-installed)
```

#### Windows Runners

```yaml
- Visual Studio 2019+ (or MinGW-w64)
- CMake
- Qt 6 (from official installer or Chocolatey)
- Python 3.10+
```

## Environment Variables

The build system uses the following environment variables:

### Qt Location

Set **CMAKE_PREFIX_PATH** to the Qt installation directory:

```bash
# Linux/macOS
export CMAKE_PREFIX_PATH=/path/to/Qt/6.5.0/gcc_64

# Windows (PowerShell)
$env:CMAKE_PREFIX_PATH = "C:\Qt\6.5.0\msvc2019_64"
```

Or use the `--qt` flag in build scripts.

### Optional Variables

- **JOBS**: Number of parallel build jobs (default: auto-detect)
- **BUILD_TYPE**: Release or Debug (default: Release)

## GitHub Actions Examples

### Linux Build

```yaml
name: Build Wallet (Linux)

on: [push, pull_request]

jobs:
  build-linux:
    runs-on: ubuntu-22.04
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
      
      - name: Install system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y \
            build-essential \
            cmake \
            qt6-base-dev \
            qt6-tools-dev \
            libqt6network6 \
            python3.11 \
            python3.11-venv \
            python3-pip
      
      - name: Verify prerequisites
        run: |
          cmake --version
          qmake6 -version
          python3.11 --version
          g++ --version
      
      - name: Build wallet
        run: |
          cd wallet-qt
          ./scripts/build-linux.sh --jobs $(nproc)
      
      - name: Verify build artifacts
        run: |
          test -f wallet-qt/build/linux/bin/animica-wallet
          test -d wallet-qt/build/linux/bin/node/venv
          test -f wallet-qt/build/linux/bin/node/venv/bin/python
      
      - name: Test bundled node
        run: |
          wallet-qt/build/linux/bin/node/venv/bin/python -m rpc --help
      
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: animica-wallet-linux
          path: wallet-qt/build/linux/bin/
```

### macOS Build

```yaml
name: Build Wallet (macOS)

on: [push, pull_request]

jobs:
  build-macos:
    runs-on: macos-13
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
      
      - name: Install dependencies via Homebrew
        run: |
          brew install cmake qt@6 python@3.11
      
      - name: Set Qt path
        run: |
          echo "CMAKE_PREFIX_PATH=$(brew --prefix qt@6)" >> $GITHUB_ENV
      
      - name: Verify prerequisites
        run: |
          cmake --version
          qmake6 -version || qmake -version
          python3 --version
          clang++ --version
      
      - name: Build wallet
        run: |
          cd wallet-qt
          ./scripts/build-mac.sh --jobs $(sysctl -n hw.ncpu)
      
      - name: Verify app bundle
        run: |
          test -d wallet-qt/build/mac/bin/AnimicaWallet.app
          test -f wallet-qt/build/mac/bin/AnimicaWallet.app/Contents/MacOS/AnimicaWallet
          test -d wallet-qt/build/mac/bin/AnimicaWallet.app/Contents/Resources/node/venv
      
      - name: Test bundled node
        run: |
          wallet-qt/build/mac/bin/AnimicaWallet.app/Contents/Resources/node/venv/bin/python -m rpc --help
      
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: animica-wallet-macos
          path: wallet-qt/build/mac/bin/AnimicaWallet.app
```

### Windows Build

```yaml
name: Build Wallet (Windows)

on: [push, pull_request]

jobs:
  build-windows:
    runs-on: windows-2022
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
      
      - name: Install Qt via aqtinstall
        run: |
          pip install aqtinstall
          aqt install-qt windows desktop 6.5.0 win64_msvc2019_64
      
      - name: Set Qt path
        run: |
          echo "CMAKE_PREFIX_PATH=${{ github.workspace }}\6.5.0\msvc2019_64" >> $env:GITHUB_ENV
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Verify prerequisites
        run: |
          cmake --version
          python --version
      
      - name: Build wallet
        run: |
          cd wallet-qt
          .\scripts\build-windows.ps1 -Jobs $env:NUMBER_OF_PROCESSORS
      
      - name: Verify build artifacts
        run: |
          Test-Path wallet-qt\build\windows\bin\Release\animica-wallet.exe
          Test-Path wallet-qt\build\windows\bin\node\venv
      
      - name: Test bundled node
        run: |
          wallet-qt\build\windows\bin\node\venv\Scripts\python.exe -m rpc --help
      
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: animica-wallet-windows
          path: wallet-qt\build\windows\bin\Release\
```

## Minimal Build Commands

For CI systems without our build scripts:

### Linux

```bash
cd wallet-qt
mkdir -p build && cd build

# Configure
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=/path/to/qt6

# Build
cmake --build . -j $(nproc)

# Verify
test -f bin/animica-wallet
test -f bin/node/venv/bin/python
```

### macOS

```bash
cd wallet-qt
mkdir -p build && cd build

# Configure
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=/path/to/qt6

# Build
cmake --build . -j $(sysctl -n hw.ncpu)

# Verify
test -d bin/AnimicaWallet.app
```

### Windows

```powershell
cd wallet-qt
mkdir build
cd build

# Configure
cmake .. `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_PREFIX_PATH="C:\Qt\6.5.0\msvc2019_64" `
  -G "Visual Studio 17 2022"

# Build
cmake --build . --config Release -j $env:NUMBER_OF_PROCESSORS

# Verify
Test-Path bin\Release\animica-wallet.exe
```

## Smoke Testing

### Headless Node Test

The wallet cannot run headless (it's a GUI app), but the bundled node can be tested:

```bash
#!/bin/bash
# smoke-test-node.sh

set -euo pipefail

# Find the bundled Python
if [[ -d "build/linux/bin/node/venv" ]]; then
    PYTHON="build/linux/bin/node/venv/bin/python"
elif [[ -d "build/mac/bin/AnimicaWallet.app/Contents/Resources/node/venv" ]]; then
    PYTHON="build/mac/bin/AnimicaWallet.app/Contents/Resources/node/venv/bin/python"
else
    echo "ERROR: Could not find bundled node"
    exit 1
fi

echo "Testing bundled node with Python: $PYTHON"

# Test 1: Check imports
echo "Test 1: Checking Python imports..."
$PYTHON -c "import rpc; import animica; print('✓ Imports successful')"

# Test 2: Start node briefly
echo "Test 2: Starting node..."
DATA_DIR=$(mktemp -d)
export ANIMICA_DATA_DIR="$DATA_DIR"
export ANIMICA_RPC_PORT=28545
export ANIMICA_P2P_PORT=31337
export ANIMICA_CHAIN_ID=1337

# Start node in background
$PYTHON -m rpc &
NODE_PID=$!

# Wait for node to start
sleep 5

# Test 3: RPC health check
echo "Test 3: Checking RPC health..."
if curl -sf http://127.0.0.1:28545/health > /dev/null; then
    echo "✓ Node RPC is responding"
else
    echo "✗ Node RPC failed"
    kill $NODE_PID 2>/dev/null || true
    exit 1
fi

# Cleanup
kill $NODE_PID 2>/dev/null || true
rm -rf "$DATA_DIR"

echo "✓ All smoke tests passed"
```

Usage in CI:

```yaml
- name: Smoke test bundled node
  run: |
    cd wallet-qt
    chmod +x scripts/smoke-test-node.sh
    ./scripts/smoke-test-node.sh
```

## Caching Strategies

### Cache Qt Installation

Qt is large (~2-3GB). Cache it between builds:

```yaml
- name: Cache Qt
  uses: actions/cache@v4
  with:
    path: ~/Qt
    key: ${{ runner.os }}-qt-6.5.0
```

### Don't Cache Node Venv

The node venv is built deterministically from source and is relatively fast (~1-2 minutes). Don't cache it to ensure reproducibility.

## Troubleshooting CI Builds

### Build Fails During Node Install

**Symptom**: pip install errors

**Solution**:
1. Check Python version in CI runner
2. Ensure venv module is available
3. Check network connectivity
4. Increase timeout if network is slow

```yaml
- name: Install Python venv module
  run: sudo apt-get install -y python3-venv
```

### Qt Not Found

**Symptom**: CMake can't find Qt

**Solution**: Set CMAKE_PREFIX_PATH explicitly

```yaml
- name: Set Qt path
  run: echo "CMAKE_PREFIX_PATH=/path/to/qt6" >> $GITHUB_ENV
```

### Disk Space Issues

**Symptom**: Build runs out of disk space

**Solution**: Clean up unneeded files before building

```yaml
- name: Free disk space
  run: |
    sudo rm -rf /usr/share/dotnet
    sudo rm -rf /opt/ghc
    sudo apt-get clean
```

## Security Considerations

### Dependency Pinning

The build currently installs Python packages from PyPI without version pinning beyond ranges in `pyproject.toml`. For production builds, consider:

1. **Pin exact versions** in a `requirements-lock.txt`:
   ```
   fastapi==0.115.0
   uvicorn==0.30.6
   # ...
   ```

2. **Use pip-tools** to generate lockfile:
   ```bash
   pip-compile python/pyproject.toml -o requirements-lock.txt
   ```

3. **Update CMake** to use lockfile:
   ```cmake
   execute_process(
       COMMAND ${NODE_PIP} install -r ${ANIMICA_REPO_ROOT}/requirements-lock.txt
   )
   ```

### Checksum Verification

For hermetic builds, verify checksums of downloaded packages:

```bash
pip install --require-hashes -r requirements-hashes.txt
```

Generate hashes with:
```bash
pip hash fastapi==0.115.0
```

## Build Artifact Verification

After successful build, verify:

1. **Executable exists and runs:**
   ```bash
   ./build/bin/animica-wallet --version
   ```

2. **Node is bundled:**
   ```bash
   test -d build/bin/node/venv
   ```

3. **Node can start:**
   ```bash
   build/bin/node/venv/bin/python -m rpc --help
   ```

4. **Required libraries are present:**
   ```bash
   ldd build/bin/animica-wallet  # Linux
   otool -L build/bin/AnimicaWallet.app/Contents/MacOS/AnimicaWallet  # macOS
   ```

## Next Steps

- Add automated tests to verify wallet UI functionality
- Create installer packages (DMG, MSI, AppImage)
- Set up code signing for production releases
- Add reproducible build verification (compare checksums across runners)

## References

- **GitHub Actions Qt Setup**: https://github.com/jurplel/install-qt-action
- **Qt CI Documentation**: https://doc.qt.io/qt-6/cmake-build-on-cmdline.html
- **Python venv in CI**: https://docs.python.org/3/library/venv.html
