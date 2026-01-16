# Animica Build Scripts

Defensive bash build scripts for creating redistributable Animica node binaries and miner-gui application bundles.

## Overview

This directory contains scripts to build:
- **Node Binary**: Standalone `animica-node` daemon (Python-based, packaged with PyInstaller)
- **Miner GUI (macOS)**: `.app` bundle with embedded node binary
- **Miner GUI (Linux)**: Standalone executable with embedded node binary

All scripts follow defensive programming practices:
- Set `-euo pipefail` for strict error handling
- Use helper functions from `common.sh`
- Validate inputs and outputs
- Provide clear error messages
- Never assume `/root` paths or interactive prompts

## Prerequisites

### All Platforms
- Git
- Python 3.10 or higher
- Sufficient disk space (~500MB for build artifacts)

### macOS
- macOS 10.15 (Catalina) or later
- Xcode Command Line Tools: `xcode-select --install`

### Linux
- Ubuntu 20.04+, Debian 11+, Fedora 35+, or similar
- Python development headers: `sudo apt install python3-dev` or `sudo dnf install python3-devel`

## Scripts

### `common.sh`

Shared helper functions for all build scripts:
- Logging: `log()`, `warn()`, `err()`, `die()`
- Path resolution: `get_repo_root()`, `get_build_dir()`
- Version helpers: `compute_version()`, `get_git_sha()`, `get_git_tag()`
- Safe operations: `safe_rm_rf()`, `verify_executable()`, `verify_directory()`
- Python helpers: `find_python3()`, `check_python_version()`, `pip_install()`
- Manifest generation: `create_manifest()`

### `build-node-binary.sh`

Builds the Animica node daemon as a standalone binary.

**Usage:**
```bash
./ops/build/build-node-binary.sh [OPTIONS]

Options:
  --network NETWORK    Network type (default: mainnet)
  --out-dir DIR        Output directory (default: dist/)
  --clean              Clean previous builds before building
  --version VERSION    Override version string
  --help               Show this help
```

**Examples:**
```bash
# Basic build
./ops/build/build-node-binary.sh

# Clean build with custom output
./ops/build/build-node-binary.sh --clean --out-dir /tmp/build

# Testnet build
./ops/build/build-node-binary.sh --network testnet
```

**Output:**
- `dist/animica-node` - Standalone node binary
- `dist/animica-node.manifest.json` - Build metadata (version, git SHA, timestamp, platform)

### `build-miner-gui-macos.sh`

Builds the macOS `.app` bundle for the miner GUI, with the node binary bundled inside.

**Usage:**
```bash
./ops/build/build-miner-gui-macos.sh [OPTIONS]

Options:
  --out-dir DIR    Output directory (default: dist/)
  --clean          Clean previous builds before building
  --dev            Development build (no codesigning warnings)
  --help           Show this help
```

**Examples:**
```bash
# Basic build
./ops/build/build-miner-gui-macos.sh --clean

# Development build
./ops/build/build-miner-gui-macos.sh --dev
```

**Output:**
- `dist/Animica Miner GUI.app` - macOS application bundle
- `dist/Animica-Miner-GUI-{version}-macOS-{arch}.dmg` - DMG installer
- `dist/animica-miner-gui-macos.manifest.json` - Build metadata

**Bundled Node:**
The node binary is embedded at: `Animica Miner GUI.app/Contents/Resources/bin/animica-node`

### `build-miner-gui-linux.sh`

Builds the Linux executable for the miner GUI, with the node binary bundled.

**Usage:**
```bash
./ops/build/build-miner-gui-linux.sh [OPTIONS]

Options:
  --out-dir DIR    Output directory (default: dist/)
  --clean          Clean previous builds before building
  --help           Show this help
```

**Examples:**
```bash
# Basic build
./ops/build/build-miner-gui-linux.sh --clean
```

**Output:**
- `dist/animica-miner-gui` - Standalone executable
- `dist/Animica-Miner-GUI-{version}-Linux-{arch}.tar.gz` - Tarball archive
- `dist/animica-miner-gui-linux.manifest.json` - Build metadata

## Build Process

Each script follows these steps:

1. **Dependency Installation**: Installs PyInstaller and required Python packages
2. **Node Binary Build** (GUI builds): Builds node binary first, then bundles it
3. **Version Detection**: Reads version from git tags or generates `dev+{sha}`
4. **Spec File Generation**: Creates PyInstaller spec with proper hidden imports
5. **PyInstaller Build**: Bundles application and all dependencies
6. **Verification**: Verifies output files exist and are executable
7. **Packaging**: Creates platform-specific installers (DMG, tarball)
8. **Manifest Generation**: Creates JSON manifest with build metadata
9. **Cleanup**: Removes intermediate build files

## Configuration

### Hidden Imports

Build scripts include comprehensive hidden imports to ensure all modules are bundled:

**Node Binary:**
- `animica.*` - All CLI modules
- `core.*` - Core blockchain functionality
- `consensus`, `execution`, `mining` - Consensus and execution layers
- `p2p`, `mempool`, `rpc` - Network and RPC
- `wallet`, `pq` - Wallet and post-quantum crypto
- `httpx`, `uvicorn`, `fastapi` - HTTP server dependencies

**Miner GUI:**
- `PySide6.*` - Qt framework
- `matplotlib` - Graphing
- `pydantic`, `httpx` - Configuration and HTTP
- `animica.cli.*` - Limited CLI integration (for config reading)

### Excluded Modules

To reduce binary size:
- `tkinter` - Alternative GUI framework (not used)
- `test`, `unittest` - Testing modules
- `matplotlib`, `PIL` - Unused in node binary

## Troubleshooting

### macOS: "App is damaged and can't be opened"

Unsigned apps trigger Gatekeeper. For testing:
```bash
xattr -cr "dist/Animica Miner GUI.app"
```

For distribution, code-sign the app:
```bash
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name" \
  "dist/Animica Miner GUI.app"
```

### Linux: Missing Qt Platform Plugin

If you see "Could not find the Qt platform plugin":
```bash
sudo apt install libxcb-xinerama0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
                 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0
```

### Build Fails: ModuleNotFoundError

Ensure all dependencies are installed:
```bash
cd /path/to/repo
pip install -e ".[dev]"
```

### Python Version Too Old

Python 3.10+ is required:
```bash
# macOS
brew install python@3.10

# Ubuntu
sudo apt install python3.10 python3.10-dev

# Check version
python3 --version
```

## Verification

### Test Node Binary

```bash
dist/animica-node --help
dist/animica-node --version
```

### Test Miner GUI (macOS)

```bash
open "dist/Animica Miner GUI.app"
```

Check that:
1. Only one instance opens (no infinite spawning)
2. RPC is localhost-only (check Settings → Network)
3. Node is started from bundled binary (check logs for path)

### Test Miner GUI (Linux)

```bash
./dist/animica-miner-gui
```

### Verify Bundled Node

**macOS:**
```bash
ls -lh "dist/Animica Miner GUI.app/Contents/Resources/bin/animica-node"
"dist/Animica Miner GUI.app/Contents/Resources/bin/animica-node" --help
```

**Linux:**
```bash
# Extract tarball and check
tar -tzf dist/Animica-Miner-GUI-*-Linux-*.tar.gz | grep animica-node
```

### Check Logs

**macOS:**
```bash
# GUI logs
tail -f ~/Library/Logs/Animica\ Miner\ GUI/app.log

# Or check in app data directory
tail -f ~/.animica/gui-miner/logs/miner-gui.log
```

**Linux:**
```bash
tail -f ~/.animica/gui-miner/logs/miner-gui.log
```

### Confirm Single Instance

1. Open the GUI
2. Try to open it again
3. Verify: Only one window appears (second launch should activate existing window)

### Confirm No External RPC

1. Open the GUI
2. Go to Settings → Network
3. Verify: RPC URL is `http://127.0.0.1:8545/rpc` (localhost only)
4. Try changing to external URL → Should show warning
5. Best: Check firewall logs to confirm no outbound connections to external nodes

## Advanced Usage

### Custom Version

```bash
./ops/build/build-node-binary.sh --version "1.0.0-rc1"
./ops/build/build-miner-gui-macos.sh
```

The GUI build will use the node binary from the same output directory.

### Cross-Platform Considerations

- **macOS arm64 (M1/M2)**: UPX is automatically disabled (recommended)
- **Linux**: Builds on x86_64 only target x86_64; for arm64, build on arm64 system
- **Windows**: Not yet implemented (PRs welcome)

### CI/CD Integration

Example GitHub Actions workflow:

```yaml
name: Build Binaries

on:
  push:
    tags:
      - 'v*'

jobs:
  build-macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Build
        run: |
          ./ops/build/build-node-binary.sh --clean
          ./ops/build/build-miner-gui-macos.sh --clean
      - uses: actions/upload-artifact@v3
        with:
          name: macos-builds
          path: dist/*.dmg
  
  build-linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Build
        run: |
          ./ops/build/build-node-binary.sh --clean
          ./ops/build/build-miner-gui-linux.sh --clean
      - uses: actions/upload-artifact@v3
        with:
          name: linux-builds
          path: dist/*.tar.gz
```

## Security Considerations

- **Node RPC**: Binds to `127.0.0.1` only (no external access)
- **Auth Token**: Node generates auth token in `~/.animica/node.token`
- **GUI-Node Communication**: Uses localhost RPC with auth token
- **No External Nodes**: GUI is hardcoded to never connect to remote RPC endpoints
- **Single Instance**: Prevents multiple GUI instances via QLocalServer lock

## License

See LICENSE.txt in the repository root.

## Support

- **Issues**: https://github.com/animicaorg/all/issues
- **Documentation**: https://docs.animica.org
- **Community**: https://discord.gg/animica
