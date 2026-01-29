# Wallet Packaging Guide

This document describes the packaging infrastructure for the Animica Qt Wallet across macOS, Windows, and Linux platforms.

## Current Build Outputs

### Wallet Application

The Qt wallet application is built from the `wallet-qt` directory with the following target:

- **Target name**: `animica-wallet`
- **Project name**: `AnimicaWallet`
- **Bundle ID** (macOS): `org.animica.wallet`
- **Binary name**:
  - Linux: `animica-wallet`
  - macOS: `AnimicaWallet.app`
  - Windows: `animica-wallet.exe`

### Animica Node Bundle

The wallet bundles the complete Animica node implementation. The following artifacts must be included:

#### Node Binary/Artifacts

1. **Python Virtual Environment**: Complete venv with all dependencies
   - Location: Built via `AnimicaNode.cmake`
   - Contains: Python interpreter, pip packages, and all node modules

2. **Node Modules** (copied into venv site-packages):
   - `rpc` - RPC server module
   - `core` - Core blockchain components
   - `consensus` - PoIES consensus implementation
   - `execution` - Python-VM execution layer
   - `mempool` - Transaction pool
   - `p2p` - Peer-to-peer networking
   - `mining` - Mining logic
   - `proofs` - Proof verification (AI, Quantum, Storage)
   - `da` - Data availability
   - `randomness` - Randomness beacons
   - `pq` - Post-quantum cryptography
   - `capabilities` - Off-chain compute coordination
   - `aicf` - AI Compute Framework
   - `queue` - Job queue system
   - `chains` - Chain configuration
   - `genesis` - Genesis block data
   - `services` - Supporting services
   - `billing` - Billing infrastructure
   - `relayer` - Relayer components

3. **Installed Python Packages**:
   - `fastapi` - RPC server framework
   - `uvicorn` - ASGI server
   - `prometheus-client` - Metrics
   - `omni-sdk` - Animica SDK (from `sdk/python`)
   - `animica` - CLI and core tools (from `python/`)
   - `pq` - PQ crypto bindings (from `pq/`)

4. **Required Shared Libraries**:
   - **liboqs**: Post-quantum cryptography library
     - On Linux: bundled or system library
     - On macOS: bundled via Homebrew or system
     - On Windows: bundled DLLs
   - **OpenSSL**: Cryptography (typically system-provided)
   - **Qt libraries**: Bundled by packaging tools

5. **Configuration Templates**: None currently required
   - Node creates default configs at runtime
   - Chain specs embedded in `chains/` module
   - Genesis blocks in `genesis/` module

## Packaging Toolchain Decisions

### macOS

**Primary Tool**: CPack + create-dmg (optional)

- **Bundle Format**: `.app` bundle (MACOSX_BUNDLE via CMake)
- **Icon Format**: `.icns` (generated from logo.png)
- **Installer**: DMG (optional, for distribution)
- **Code Signing**: `codesign` with Developer ID Application certificate
- **Notarization**: `xcrun notarytool` for Gatekeeper approval
- **Architecture**: Universal binary (arm64 + x86_64) or per-arch builds

**Rationale**: 
- CMake already generates .app bundles via MACOSX_BUNDLE
- Native macOS tools for signing/notarization
- DMG provides familiar download experience

### Windows

**Primary Tool**: CPack/WIX generator

- **Installer Format**: `.msi` (Windows Installer)
- **Fallback**: NSIS `.exe` installer if WiX unavailable
- **Icon Format**: `.ico` (multi-resolution, generated from logo.png)
- **Icon Embedding**: CMake RC file (Win32 resource compiler)
- **Code Signing**: `signtool` with code signing certificate
- **Architecture**: x64 (primary), arm64 (future)

**Rationale**:
- MSI is the modern Windows standard
- WiX integrates with CPack
- Better uninstall support than NSIS

### Linux

**Primary Tools**: linuxdeployqt + CPack DEB

- **Portable Format**: AppImage (via linuxdeployqt)
- **Package Format**: `.deb` (via CPack DEB generator)
- **Icon Formats**: `.png` at standard sizes (16, 32, 48, 64, 128, 256, 512)
- **Desktop Integration**: `.desktop` file in `/usr/share/applications/`
- **Icon Installation**: Standard hicolor theme in `/usr/share/icons/hicolor/`

**Rationale**:
- AppImage provides universal compatibility (no dependencies)
- DEB covers Ubuntu/Debian ecosystem
- linuxdeployqt bundles Qt and dependencies automatically

## Icon Pipeline

Source: `/home/runner/work/all/all/contrib/logos/png/animica-logo-1024.png`

Generated Outputs:
- **macOS**: `wallet-qt/resources/icons/animica.icns`
- **Windows**: `wallet-qt/resources/icons/animica.ico`
- **Linux**: `wallet-qt/resources/icons/hicolor/*/apps/animica-wallet.png` (16-512px)

Tools:
- **ImageMagick** (convert): For PNG resizing and ICO generation
- **iconutil** (macOS): For ICNS generation from iconset
- **Python PIL** (fallback): Cross-platform icon generation

## Bundle Layout

### macOS

```
AnimicaWallet.app/
├── Contents/
│   ├── Info.plist           # Bundle metadata
│   ├── MacOS/
│   │   └── AnimicaWallet    # Wallet executable
│   ├── Resources/
│   │   ├── animica.icns     # App icon
│   │   └── node/
│   │       └── venv/        # Complete Python venv with node
│   │           ├── bin/
│   │           │   └── python
│   │           └── lib/
│   └── Frameworks/          # Qt and other dylibs (if bundled)
```

### Windows

```
C:\Program Files\AnimicaWallet\
├── animica-wallet.exe       # Wallet executable (with embedded icon)
├── node\
│   └── venv\                # Complete Python venv with node
│       ├── Scripts\
│       │   └── python.exe
│       └── Lib\
├── Qt6*.dll                 # Qt libraries
├── liboqs.dll               # PQ crypto (if required)
└── VC runtime DLLs          # Visual C++ redistributable
```

### Linux (AppImage)

```
AnimicaWallet.AppImage
└── (extracted AppDir):
    ├── AppRun               # Entry point script
    ├── animica-wallet.desktop
    ├── animica-wallet.png   # 256px icon
    └── usr/
        ├── bin/
        │   └── animica-wallet
        ├── lib/
        │   ├── node/
        │   │   └── venv/    # Complete Python venv with node
        │   └── (Qt libs)    # Bundled by linuxdeployqt
        └── share/
            └── icons/
```

### Linux (DEB)

```
/usr/
├── bin/
│   └── animica-wallet
├── lib/
│   └── animica-wallet/
│       └── node/
│           └── venv/        # Complete Python venv with node
└── share/
    ├── applications/
    │   └── animica-wallet.desktop
    └── icons/hicolor/
        ├── 16x16/apps/animica-wallet.png
        ├── 32x32/apps/animica-wallet.png
        ├── 48x48/apps/animica-wallet.png
        ├── 64x64/apps/animica-wallet.png
        ├── 128x128/apps/animica-wallet.png
        ├── 256x256/apps/animica-wallet.png
        └── 512x512/apps/animica-wallet.png
```

## Runtime Behavior

### Node Location

The wallet locates the bundled node relative to its installation path:

- **macOS**: `Contents/Resources/node/venv/bin/python` (relative to .app bundle)
- **Windows**: `node/venv/Scripts/python.exe` (relative to exe directory)
- **Linux AppImage**: `../lib/node/venv/bin/python` (relative to usr/bin)
- **Linux DEB**: `/usr/lib/animica-wallet/node/venv/bin/python` (absolute)

Implementation: See `src/platform/AppPaths.cpp` and `src/node/NodeManager.cpp`

### Node Binding

The node binds only to loopback (127.0.0.1) by default for security. No external network access is permitted to the RPC server.

### Data Directories

Default data directories follow OS conventions:

- **macOS**: `~/Library/Application Support/Animica/`
- **Windows**: `%APPDATA%\Animica\`
- **Linux**: `~/.animica/`

Structure within datadir:
```
Animica/
├── chain-1/        # Mainnet data
├── chain-2/        # Testnet data
├── chain-1337/     # Devnet data
├── wallet.db       # Wallet database
└── config.json     # User preferences
```

Users can override the datadir via:
- Command-line flag: `--datadir /custom/path`
- Environment variable: `ANIMICA_DATADIR`
- Settings UI (future)

### Packaging Validation

Each packaging script performs validation:

1. **Node binary exists**: Verify venv/bin/python or venv/Scripts/python.exe
2. **Node is executable**: Check file permissions
3. **Correct architecture**: Verify binary matches target arch (arm64/x64)
   - macOS: `file` or `lipo -info`
   - Linux: `file` or `readelf -h`
   - Windows: `dumpbin /headers`
4. **Dynamic libraries present**: Check for liboqs and Qt libs
   - macOS: `otool -L`
   - Linux: `ldd`
   - Windows: `dumpbin /dependents`
5. **Node imports work**: Run `python -c "import rpc; import animica"`

## Build Prerequisites

### All Platforms

- CMake 3.16+
- Qt 6.2+ (or Qt 5.15+)
- Python 3.10+
- Git (for version tagging)

### macOS

- Xcode Command Line Tools
- Homebrew (recommended for dependencies)
- `create-dmg` (optional): `brew install create-dmg`
- Developer ID Application certificate (for signing)
- Apple Developer account (for notarization)

### Windows

- Visual Studio 2019+ or MinGW-w64
- WiX Toolset 3.11+ (for MSI): https://wixtoolset.org/
- Windows SDK
- Code signing certificate (optional, for release)

### Linux

- GCC 9+ or Clang 10+
- `linuxdeployqt`: https://github.com/probonopd/linuxdeployqt/releases
- ImageMagick: `sudo apt-get install imagemagick`
- `dpkg-deb` (for DEB): Usually pre-installed

## Versioning Strategy

Version is derived from:
1. Git tag (if present): `git describe --tags --abbrev=0`
2. CMakeLists.txt `PROJECT_VERSION`: Fallback if no tag
3. Git commit hash: Always included in build metadata

Version format: `MAJOR.MINOR.PATCH` (semver)

Build metadata includes:
- Version string
- Git commit hash
- Build timestamp (UTC)
- Qt version
- Platform/architecture

Displayed in:
- About dialog
- `--version` CLI flag
- Installer metadata
- Package filenames

## Distribution Artifacts

Output directory: `dist/wallet-qt/<version>/`

Structure:
```
dist/wallet-qt/v0.1.0/
├── macos/
│   ├── AnimicaWallet-0.1.0-macos-universal.dmg
│   ├── AnimicaWallet-0.1.0-macos-arm64.dmg
│   └── AnimicaWallet-0.1.0-macos-x86_64.dmg
├── windows/
│   ├── AnimicaWallet-0.1.0-windows-x64.msi
│   └── AnimicaWallet-0.1.0-windows-x64.exe (NSIS fallback)
├── linux/
│   ├── AnimicaWallet-0.1.0-linux-x86_64.AppImage
│   ├── animica-wallet-0.1.0-amd64.deb
│   └── animica-wallet-0.1.0-arm64.deb
└── SHA256SUMS
```

## Security Considerations

### Code Signing

- **macOS**: All binaries (wallet + node Python) must be signed
- **Windows**: Recommended for avoiding SmartScreen warnings
- **Linux**: Not typically required, but can use GPG signatures for packages

### Sandboxing

- **macOS**: App Sandbox not currently enabled (requires entitlements for network, file access)
- **Windows**: No sandboxing (future: UWP conversion)
- **Linux**: No sandboxing (future: Flatpak/Snap)

### Updates

Auto-update not implemented yet. Future considerations:
- Sparkle (macOS)
- WinSparkle (Windows)
- AppImageUpdate (Linux AppImage)

## Next Steps

1. Implement icon generation pipeline (Part B)
2. Create release scripts for each platform (Parts D, E, F)
3. Add smoke tests (Part H)
4. Write detailed releasing documentation (Part I)
