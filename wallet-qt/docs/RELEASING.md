# Releasing Animica Wallet

Linux remains the native release host for Linux artifacts and is now the primary host for Windows desktop releases via MinGW-w64 cross-compilation.

## 1. Prepare the tree

```bash
git checkout <release-commit>
git status
```

Optional tag:

```bash
git tag -a v0.1.0 -m "Release v0.1.0"
```

## 2. Linux release

```bash
cd wallet-qt
./scripts/release-linux.sh
```

Outputs:

- `dist/wallet-qt/<version>/linux/*.AppImage`
- `dist/wallet-qt/<version>/linux/*.deb`
- `dist/wallet-qt/<version>/linux/*.tar.gz`
- `dist/wallet-qt/<version>/linux/SHA256SUMS`

Validate:

```bash
./scripts/smoke-test-linux.sh <artifact>
```

Linux validation resolves the bundled node under either:

- `usr/lib/x86_64-linux-gnu/animica-wallet/node`
- `usr/lib/animica-wallet/node`

The same resolver is used for staged installs, AppDir/AppImage contents, tarballs, and direct executable smoke tests.

If `website/public/wallet/` exists beside `wallet-qt/`, the Linux release script also refreshes the website download copies and regenerates `website/public/wallet/manifest.json`:

- `website/public/wallet/animica-wallet-linux.AppImage`
- `website/public/wallet/animica-wallet-linux.deb`
- `website/public/wallet/animica-wallet-linux.tar.gz`
- `website/public/wallet/animica-wallet`
- `website/public/wallet/animica-wallet-linux.sha256`
- `website/public/wallet/animica-wallet.sha256`
- `website/public/wallet/manifest.json`

## 3. macOS release

Staged build:

```bash
cd wallet-qt
./scripts/build-mac.sh --clean
```

Unsigned/ad-hoc DMG:

```bash
./scripts/release-mac.sh --adhoc-sign --dmg
```

Developer ID / notarization placeholders:

```bash
export CODESIGN_IDENTITY="Developer ID Application: ..."
export APPLE_ID="you@example.com"
export APPLE_TEAM_ID="TEAMID"
./scripts/release-mac.sh --sign --notarize --dmg
```

Outputs:

- `dist/wallet-qt/<version>/macos/*.app`
- `dist/wallet-qt/<version>/macos/*.dmg`
- `dist/wallet-qt/<version>/macos/SHA256SUMS`

Validate:

```bash
./scripts/smoke-test-mac.sh dist/wallet-qt/<version>/macos/*.app
```

## 4. Windows release from Linux

### Ubuntu 24.04 host prerequisites

Install the Linux-hosted toolchain pieces once:

```bash
sudo apt-get update
sudo apt-get install -y \
  binutils-mingw-w64-x86-64 \
  g++-mingw-w64-x86-64 \
  gcc-mingw-w64-x86-64 \
  mingw-w64 \
  ninja-build \
  nsis \
  python3-pip \
  python3-venv \
  qt6-base-dev \
  qt6-base-dev-tools \
  zip
```

The cross-build also needs a Windows-target Qt SDK and Windows-target OpenSSL prefix on disk. The repo expects these environment variables:

```bash
export WINDOWS_QT_ROOT=/opt/qt/windows/6.7.3/win64_mingw
export QT_HOST_PATH=/usr
export WINDOWS_OPENSSL_ROOT=/opt/openssl/windows-x64
```

`WINDOWS_QT_ROOT` must contain:

- `bin/`
- `plugins/`
- `lib/cmake/Qt6/Qt6Config.cmake`

`WINDOWS_OPENSSL_ROOT` must contain:

- `include/openssl/ssl.h`
- MinGW import libraries such as `libssl*.dll.a` and `libcrypto*.dll.a`
- runtime DLLs such as `libssl*.dll` and `libcrypto*.dll`

Use the build script in prerequisite-check mode to validate the host before building:

```bash
cd wallet-qt
./scripts/build-windows-cross.sh --check
```

### Build Windows artifacts only

Remote-RPC-only build (default and fully Linux-driven):

```bash
cd wallet-qt
./scripts/build-windows-cross.sh --clean
```

If you already have a prebuilt Windows node virtual environment and want the embedded-node bundle instead of remote-RPC-only mode:

```bash
cd wallet-qt
./scripts/build-windows-cross.sh --clean --node-venv /abs/path/to/windows-node/venv
```

### Publish Windows downloads into the website

```bash
cd wallet-qt
./scripts/publish-wallet-downloads.sh \
  --platform windows \
  --version "$(git describe --tags --always --dirty)" \
  --source-dir ./dist/windows
```

### Combined build + website publish

```bash
cd wallet-qt
./scripts/release-windows-cross.sh --clean
```

Per-machine installer instead of per-user:

```bash
./scripts/release-windows-cross.sh --clean --per-machine
```

Outputs:

- `wallet-qt/dist/windows/animica-wallet-setup-x64.exe`
- `wallet-qt/dist/windows/animica-wallet-windows-x64.zip`
- `wallet-qt/dist/windows/SHA256SUMS.txt`
- `website/public/wallet/animica-wallet-windows-x64.exe`
- `website/public/wallet/animica-wallet-windows-x64.zip`
- `website/public/wallet/animica-wallet-windows.sha256`
- `website/public/wallet/manifest.json`

Validate the staged runtime layout from Linux:

```bash
./scripts/verify-bundle-layout.py --platform windows --path ./build/windows-cross/stage --remote-rpc-only
```

If you bundled a prebuilt Windows node venv:

```bash
./scripts/verify-bundle-layout.py --platform windows --path ./build/windows-cross/stage
```

The native Windows PowerShell flow now emits an NSIS installer `.exe`, a portable `.zip`, and an optional WiX `.msi` when WiX is installed:

```powershell
cd wallet-qt
.\scripts\release-windows.ps1
```

## 5. Release Verification Checklist

Run this on the native target OS before publishing:

- wallet opens
- balances load
- receive address shows a real QR
- QR saves to PNG
- send form opens
- history loads
- packaged app launches without the repo checkout
- bundled runtime can import `animica.wallet_qr`

## 6. Publish

Ship the artifacts together with:

- `SHA256SUMS`
- release notes
- platform-specific install instructions

## Common Failure Modes

- the bundled node build host cannot install Python dependencies
- Qt deployment did not run because the workflow skipped `cmake --install`
- the Linux host does not have a Windows-target Qt SDK or Windows-target OpenSSL prefix configured
- WiX Toolset v3 is missing on Windows when you use the native PowerShell MSI flow
- Apple signing or notarization credentials are missing on macOS
