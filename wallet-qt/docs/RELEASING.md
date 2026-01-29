# Releasing Animica Wallet

This document provides step-by-step instructions for building and releasing the Animica Wallet across all supported platforms (macOS, Windows, Linux).

## Table of Contents

- [Prerequisites](#prerequisites)
- [Version Tagging](#version-tagging)
- [macOS Release](#macos-release)
- [Windows Release](#windows-release)
- [Linux Release](#linux-release)
- [Verification](#verification)
- [Distribution](#distribution)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### All Platforms

1. **Git** with repository access
2. **Python 3.10+** with `pip` and `venv`
3. **CMake 3.16+**
4. **Qt 6.2+** (or Qt 5.15+)

### macOS Specific

- Xcode Command Line Tools: `xcode-select --install`
- Homebrew (recommended): `https://brew.sh`
- Qt: `brew install qt@6`
- create-dmg (optional): `brew install create-dmg`

**For Code Signing:**
- Apple Developer account
- Developer ID Application certificate installed in Keychain
- App-specific password for notarization

### Windows Specific

- Visual Studio 2019+ with C++ build tools
- Qt 6.x for MSVC (from https://www.qt.io/download)
- WiX Toolset 3.11+ (for MSI): https://wixtoolset.org/
- Windows SDK

**For Code Signing:**
- Code signing certificate (from trusted CA)
- `signtool.exe` (included with Windows SDK)

### Linux Specific

- GCC 9+ or Clang 10+: `sudo apt-get install build-essential`
- Qt development: `sudo apt-get install qt6-base-dev`
- linuxdeployqt: Download from https://github.com/probonopd/linuxdeployqt/releases
- ImageMagick: `sudo apt-get install imagemagick`
- dpkg-deb (usually pre-installed)

## Version Tagging

Before building a release, tag the repository with the version:

```bash
# Checkout the release branch/commit
git checkout main
git pull

# Create and push a tag
VERSION="v0.1.0"
git tag -a "$VERSION" -m "Release $VERSION"
git push origin "$VERSION"
```

The release scripts automatically use git tags for versioning. If no tag is present, they'll use `v0.1.0` with the commit hash.

## macOS Release

### Quick Build (No Signing)

```bash
cd wallet-qt
./scripts/release-mac.sh
```

Output: `dist/wallet-qt/v0.1.0/macos/AnimicaWallet-v0.1.0-macos-<arch>.app`

### With DMG

```bash
./scripts/release-mac.sh --dmg
```

Output: `.app` bundle + `.dmg` installer

### Code Signing

**Prerequisites:**
1. Obtain a Developer ID Application certificate from Apple Developer
2. Install the certificate in your Keychain
3. Find your Team ID from https://developer.apple.com/account

**Steps:**

```bash
# Set your code signing identity
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAM123)"

# Build and sign
./scripts/release-mac.sh --sign --dmg
```

The script will:
- Sign all embedded binaries (Python, dylibs)
- Sign the app bundle with hardened runtime
- Create a signed DMG (if `--dmg` is used)

**Verify signing:**

```bash
codesign --verify --verbose=2 dist/wallet-qt/v0.1.0/macos/*.app
spctl --assess --verbose=2 dist/wallet-qt/v0.1.0/macos/*.app
```

### Notarization

**Prerequisites:**
1. Apple Developer account with notarization access
2. App-specific password for your Apple ID

**Steps:**

```bash
# Store app-specific password in keychain
security add-generic-password -a "your@apple.id" \
    -w "your-app-specific-password" \
    -s "AC_PASSWORD"

# Set environment variables
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAM123)"
export APPLE_ID="your@apple.id"
export APPLE_TEAM_ID="TEAM123"

# Build, sign, and notarize
./scripts/release-mac.sh --sign --notarize --dmg
```

The script will:
1. Sign the app
2. Create a ZIP archive
3. Submit to Apple's notarization service
4. Wait for approval (usually 2-10 minutes)
5. Staple the notarization ticket to the app
6. Create signed DMG (if requested)

**Verify notarization:**

```bash
spctl --assess --type execute --verbose=2 dist/wallet-qt/v0.1.0/macos/*.app
```

Should output: `source=Notarized Developer ID`

### Distribution

The notarized DMG can be distributed publicly. Users can download and open it without Gatekeeper warnings.

**Note:** First-time users may need to grant network permissions for the embedded node.

## Windows Release

### Quick Build

```powershell
cd wallet-qt
.\scripts\release-windows.ps1
```

Output: 
- MSI installer (if WiX is available)
- Or ZIP archive (fallback)

Location: `dist/wallet-qt/v0.1.0/windows/`

### Code Signing

**Prerequisites:**
1. Obtain a code signing certificate from a trusted CA (e.g., DigiCert, Sectigo)
2. Install the certificate (usually a .pfx file)
3. Note the certificate thumbprint or path

**Steps:**

```powershell
# Set certificate (by thumbprint)
$env:CODESIGN_CERT = "ABC123DEF456..."

# Or by PFX path
$env:CODESIGN_CERT = "C:\Path\To\cert.pfx"

# Build and sign
.\scripts\release-windows.ps1 -Sign
```

The script will:
- Sign the wallet executable
- Sign the embedded Python executable
- Sign the MSI installer (if created)
- Use SHA256 with timestamping

**Verify signing:**

```powershell
# Check signature
signtool verify /pa dist\wallet-qt\v0.1.0\windows\*.exe

# View certificate details
signtool verify /pa /v dist\wallet-qt\v0.1.0\windows\*.exe
```

### Troubleshooting Windows Build

**WiX not found:**
- Download from https://wixtoolset.org/
- Add `C:\Program Files (x86)\WiX Toolset v3.11\bin` to PATH
- Restart PowerShell

**Qt not found:**
- Set `CMAKE_PREFIX_PATH`:
  ```powershell
  $env:CMAKE_PREFIX_PATH = "C:\Qt\6.5.3\msvc2019_64"
  ```

**Missing Visual C++ runtime:**
- The installer should include VC runtime DLLs
- Or users can install the VC++ Redistributable

## Linux Release

### Quick Build (Both AppImage and DEB)

```bash
cd wallet-qt
./scripts/release-linux.sh
```

Output:
- `AnimicaWallet-v0.1.0-linux-x86_64.AppImage`
- `animica-wallet_0.1.0_amd64.deb`

Location: `dist/wallet-qt/v0.1.0/linux/`

### AppImage Only

```bash
./scripts/release-linux.sh --appimage-only
```

### DEB Only

```bash
./scripts/release-linux.sh --deb-only
```

### Installing linuxdeployqt

If linuxdeployqt is not found:

```bash
# Download
wget https://github.com/probonopd/linuxdeployqt/releases/download/continuous/linuxdeployqt-continuous-x86_64.AppImage

# Make executable
chmod +x linuxdeployqt-continuous-x86_64.AppImage

# Install
sudo mv linuxdeployqt-continuous-x86_64.AppImage /usr/local/bin/linuxdeployqt
```

### Distribution

**AppImage:**
- Universal format, works on most Linux distributions
- Users download and run: `chmod +x AnimicaWallet*.AppImage && ./AnimicaWallet*.AppImage`
- No installation required

**DEB:**
- For Ubuntu/Debian-based distributions
- Install: `sudo dpkg -i animica-wallet_*.deb`
- Adds desktop entry and system integration

## Verification

After building, run smoke tests to verify the release works correctly.

### macOS

```bash
cd wallet-qt
./scripts/smoke-test-mac.sh dist/wallet-qt/v0.1.0/macos/AnimicaWallet-*.app
```

### Linux

```bash
cd wallet-qt

# Test AppImage
./scripts/smoke-test-linux.sh dist/wallet-qt/v0.1.0/linux/AnimicaWallet-*.AppImage

# Or test built executable
./scripts/smoke-test-linux.sh build/linux-release/bin/animica-wallet
```

### Windows

```powershell
cd wallet-qt

# Test built executable
.\scripts\smoke-test-windows.ps1 build\windows-release\bin\Release\animica-wallet.exe
```

### What the Smoke Tests Do

1. ✓ Verify node binary exists and is executable
2. ✓ Test Python imports (rpc, animica, core modules)
3. ✓ Start the embedded node
4. ✓ Verify RPC endpoints respond (`/health`, `/status`)
5. ✓ Verify correct chain ID
6. ✓ Test clean shutdown

If all tests pass, the release is ready for distribution.

## Distribution

### Checksums

All release scripts generate `SHA256SUMS` in the output directory. Publish this alongside binaries.

**Verify a download:**

```bash
# macOS/Linux
sha256sum -c SHA256SUMS

# Windows
Get-FileHash AnimicaWallet-*.msi -Algorithm SHA256
```

### Release Structure

```
dist/wallet-qt/v0.1.0/
├── macos/
│   ├── AnimicaWallet-0.1.0-macos-arm64.app
│   ├── AnimicaWallet-0.1.0-macos-arm64.dmg
│   ├── AnimicaWallet-0.1.0-macos-x86_64.app
│   ├── AnimicaWallet-0.1.0-macos-x86_64.dmg
│   └── SHA256SUMS
├── windows/
│   ├── AnimicaWallet-0.1.0-windows-x64.msi
│   └── SHA256SUMS
└── linux/
    ├── AnimicaWallet-0.1.0-linux-x86_64.AppImage
    ├── animica-wallet-0.1.0-amd64.deb
    └── SHA256SUMS
```

### GitHub Release

1. Create a release on GitHub with the tag
2. Upload all artifacts from `dist/wallet-qt/v0.1.0/`
3. Include SHA256SUMS files
4. Add release notes

### Website

Update download links:
- macOS: Link to `.dmg` file
- Windows: Link to `.msi` file
- Linux: Link to AppImage and/or DEB

## Troubleshooting

### macOS: "App is damaged" error

**Cause:** App was downloaded from internet but not signed/notarized

**Solution 1:** Sign and notarize the app (see above)

**Solution 2:** For testing, remove quarantine attribute:
```bash
xattr -cr AnimicaWallet.app
```

### macOS: Node fails to start

**Cause:** Python in venv doesn't have correct architecture

**Check:**
```bash
file AnimicaWallet.app/Contents/Resources/node/venv/bin/python
```

**Solution:** Rebuild with correct `CMAKE_OSX_ARCHITECTURES`

### Windows: "Windows protected your PC" warning

**Cause:** Executable is not signed

**Solution:** Sign the executable with a code signing certificate (see above)

**Workaround:** Users can click "More info" → "Run anyway" (not recommended for production)

### Windows: DLL not found errors

**Cause:** Qt or VC++ runtime DLLs not bundled

**Solution:**
1. Use WinDeployQt: `windeployqt.exe animica-wallet.exe`
2. Or manually copy required DLLs from Qt installation
3. Install VC++ Redistributable on target machine

### Linux: AppImage won't run

**Cause 1:** AppImage not executable
```bash
chmod +x AnimicaWallet-*.AppImage
```

**Cause 2:** FUSE not available (required for AppImage)
```bash
sudo apt-get install fuse libfuse2
```

**Cause 3:** Running on Wayland with older AppImage runtime
- Try setting `QT_QPA_PLATFORM=xcb`
- Or extract and run directly:
  ```bash
  ./AnimicaWallet-*.AppImage --appimage-extract
  ./squashfs-root/AppRun
  ```

### Linux: Qt plugin errors

**Cause:** Missing Qt plugins in bundle

**Solution:** Ensure linuxdeployqt runs successfully and bundles all Qt plugins

**Workaround:** Install Qt on target system:
```bash
sudo apt-get install qt6-base-dev
```

### Node fails to start (all platforms)

**Check node logs:**
- macOS: `~/Library/Application Support/AnimicaWallet/logs/`
- Windows: `%APPDATA%\AnimicaWallet\logs\`
- Linux: `~/.animica/logs/` or `~/.local/share/AnimicaWallet/logs/`

**Common issues:**
1. Port already in use → Node will auto-increment
2. Permissions issue → Check datadir is writable
3. Missing Python dependencies → Rebuild with fresh venv

**Test node directly:**
```bash
# macOS
AnimicaWallet.app/Contents/Resources/node/venv/bin/python -m rpc --help

# Windows
.\node\venv\Scripts\python.exe -m rpc --help

# Linux
./usr/lib/node/venv/bin/python -m rpc --help
```

### Build fails during node bundling

**Cause:** Python dependencies failed to install

**Check:**
```bash
# View CMake output for pip install errors
cmake --build build --verbose
```

**Common issues:**
1. No internet access → Build offline or use pip cache
2. Wrong Python version → Install Python 3.10+
3. Missing build tools → Install compiler and headers

**Solution:** Clean build and retry:
```bash
rm -rf build
cmake -B build
cmake --build build
```

## Support

For additional help:
- GitHub Issues: https://github.com/animicaorg/all/issues
- Documentation: `wallet-qt/docs/`
- Community: [Discord/Forum link]

## Security

- Always verify SHA256 checksums after downloading
- macOS: Only distribute notarized apps
- Windows: Always sign production releases
- Never commit signing keys or certificates to repository
