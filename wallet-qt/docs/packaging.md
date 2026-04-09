# Wallet Packaging Guide

## Scope

`wallet-qt/` ships a bundled desktop wallet with:

- the Qt GUI
- the embedded Python/node runtime
- runtime assets required outside the repo checkout
- platform-native packaging scripts for Linux, macOS, and Windows

All native release scripts build with `-DWALLET_REMOTE_RPC_ONLY=OFF`.

## Runtime Layout

### Linux install tree

- executable: `bin/animica-wallet`
- embedded node: `lib/<multiarch>/animica-wallet/node/venv/bin/python` or `lib/animica-wallet/node/venv/bin/python`
- bundled chain params: `lib/<multiarch>/animica-wallet/node/assets/spec/params.yaml` or `lib/animica-wallet/node/assets/spec/params.yaml`
- bundled genesis files: `lib/<multiarch>/animica-wallet/node/assets/genesis/*.json` or `lib/animica-wallet/node/assets/genesis/*.json`

`<multiarch>` is typically `x86_64-linux-gnu` on Debian-derived x86_64 systems. The Linux packaging and smoke-test tooling resolves both layouts, preferring the actual installed multiarch path when present and falling back to the legacy `lib/animica-wallet` layout for compatibility.

### Linux AppImage

- executable: `usr/bin/animica-wallet`
- embedded node: `usr/lib/<multiarch>/animica-wallet/node/venv/bin/python` or `usr/lib/animica-wallet/node/venv/bin/python`
- bundled chain params: `usr/lib/<multiarch>/animica-wallet/node/assets/spec/params.yaml` or `usr/lib/animica-wallet/node/assets/spec/params.yaml`
- bundled genesis files: `usr/lib/<multiarch>/animica-wallet/node/assets/genesis/*.json` or `usr/lib/animica-wallet/node/assets/genesis/*.json`

### macOS app bundle

- executable: `AnimicaWallet.app/Contents/MacOS/AnimicaWallet`
- embedded node: `AnimicaWallet.app/Contents/Resources/node/venv/bin/python`
- bundled chain params: `AnimicaWallet.app/Contents/Resources/node/assets/spec/params.yaml`
- Qt platform plugin: `AnimicaWallet.app/Contents/PlugIns/platforms/libqcocoa.dylib`

### Windows staged/install tree

- executable: `animica-wallet.exe`
- embedded node: `node\venv\Scripts\python.exe`
- bundled chain params: `node\assets\spec\params.yaml`
- Qt platform plugin: `platforms\qwindows.dll`

## Prerequisites

### Linux

- Qt 6
- CMake 3.16+
- Python 3.10+
- `linuxdeployqt` for AppImage and portable tarball generation
- `dpkg-deb` for `.deb`

### macOS

- Qt 6
- CMake 3.16+
- Python 3.10+
- Xcode command line tools
- `create-dmg` for polished DMG generation
- Apple signing credentials only if you are doing Developer ID signing/notarization

### Windows from Linux

- MinGW-w64 cross compiler (`x86_64-w64-mingw32-*`)
- Qt 6 for Windows (MinGW target) plus Qt host tools on Linux
- CMake 3.16+
- Ninja
- NSIS (`makensis`) for installer `.exe` generation
- Python 3.10+
- Windows-target OpenSSL headers, import libraries, and DLLs
- optional: a prebuilt Windows node virtual environment if you want the embedded-node bundle instead of the default remote-RPC-only build

## QR Dependency Note

The receive screen uses the bundled Python runtime to render QR PNGs via the `animica.wallet_qr` helper. The packaged node build must therefore include the `animica[wallet_qt]` extra, which pulls in:

- `segno`
- `pypng`

If those dependencies are missing, the UI shows an explicit actionable failure state instead of a fake QR placeholder.

## Linux Commands

### Build a bundled runtime

```bash
cd wallet-qt
cmake -S . -B build/linux-bundled -DCMAKE_BUILD_TYPE=Release -DWALLET_REMOTE_RPC_ONLY=OFF -DBUILD_TESTING=OFF
cmake --build build/linux-bundled -j"$(nproc)"
```

### Build release artifacts

```bash
cd wallet-qt
./scripts/release-linux.sh
```

Artifacts:

- `dist/wallet-qt/<version>/linux/AnimicaWallet-<version>-linux-<arch>.AppImage`
- `dist/wallet-qt/<version>/linux/animica-wallet_<version>_<arch>.deb`
- `dist/wallet-qt/<version>/linux/AnimicaWallet-<version>-linux-<arch>.tar.gz`
- `dist/wallet-qt/<version>/linux/SHA256SUMS`

### Validate Linux artifacts

```bash
./wallet-qt/scripts/smoke-test-linux.sh <path-to-AppImage-or-tarball-or-wallet-executable>
```

The verifier checks these bundled node files in whichever Linux libdir layout is present:

- `venv/bin/python`
- `assets/spec/params.yaml`
- `assets/genesis/devnet.json`

## macOS Commands

### Build a staged `.app`

```bash
cd wallet-qt
./scripts/build-mac.sh --clean
```

Expected staged output:

- `wallet-qt/build/mac/stage/AnimicaWallet.app`

### Build release `.app` and `.dmg`

```bash
cd wallet-qt
./scripts/release-mac.sh --dmg
```

Artifacts:

- `dist/wallet-qt/<version>/macos/AnimicaWallet-<version>-macos-<arch>.app`
- `dist/wallet-qt/<version>/macos/AnimicaWallet-<version>-macos-<arch>.dmg`
- `dist/wallet-qt/<version>/macos/SHA256SUMS`

### macOS signing flows

Ad-hoc validation signing:

```bash
cd wallet-qt
./scripts/release-mac.sh --adhoc-sign --dmg
```

Developer ID signing:

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
./scripts/release-mac.sh --sign --dmg
```

Notarization placeholder:

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_ID="you@example.com"
export APPLE_TEAM_ID="TEAMID"
./scripts/release-mac.sh --sign --notarize --dmg
```

### Validate a staged or release macOS bundle

```bash
./wallet-qt/scripts/smoke-test-mac.sh /path/to/AnimicaWallet.app
```

### Native macOS validation checklist

- run `python3 wallet-qt/scripts/verify-bundle-layout.py --platform macos --path <app>`
- confirm `Contents/PlugIns/platforms/libqcocoa.dylib` exists
- run `otool -L <app>/Contents/MacOS/AnimicaWallet`
- launch the app with `open <app>`
- confirm the receive tab renders a real QR and can save PNG
- if distributing publicly, verify signing with `codesign --verify --verbose=2 <app>`
- if notarized, verify with `spctl --assess --type execute --verbose=2 <app>`

## Windows Commands

### Check Linux cross-build prerequisites

```bash
cd wallet-qt
./scripts/build-windows-cross.sh --check
```

### Build a staged Windows runtime and release artifacts from Linux

Remote-RPC-only build (default):

```bash
cd wallet-qt
./scripts/build-windows-cross.sh --clean
```

Embedded-node build with a prebuilt Windows venv:

```bash
cd wallet-qt
./scripts/build-windows-cross.sh --clean --node-venv /abs/path/to/windows-node/venv
```

Artifacts:

- `wallet-qt/dist/windows/animica-wallet-setup-x64.exe`
- `wallet-qt/dist/windows/animica-wallet-windows-x64.zip`
- `wallet-qt/dist/windows/SHA256SUMS.txt`

Expected staged output:

- `wallet-qt/build/windows-cross/stage/`

### Publish Windows downloads into the website

```bash
cd wallet-qt
./scripts/publish-wallet-downloads.sh \
  --platform windows \
  --version "$(git describe --tags --always --dirty)" \
  --source-dir ./dist/windows
```

Combined build + website publish:

```bash
cd wallet-qt
./scripts/release-windows-cross.sh --clean
```

### Validate a staged Windows runtime

Remote-RPC-only verification:

```bash
./wallet-qt/scripts/verify-bundle-layout.py --platform windows --path ./wallet-qt/build/windows-cross/stage --remote-rpc-only
```

Embedded-node verification:

```bash
./wallet-qt/scripts/verify-bundle-layout.py --platform windows --path ./wallet-qt/build/windows-cross/stage
```

### Native Windows validation checklist

- `.\scripts\build-windows.ps1` should produce `build\windows\stage\`
- `.\scripts\package-windows-installer.ps1` should produce `build\windows\installer\AnimicaWallet-Setup.exe`
- verify `platforms\qwindows.dll` exists
- run the packaged executable outside the repo checkout
- confirm the receive tab renders a real QR and can save PNG
- confirm the Inno Setup installer writes Apps & Features metadata correctly
- confirm uninstall works from Apps & Features or the generated uninstaller

## Common Failure Points

### QR generation fails in the receive tab

Check:

- the bundled Python can import `animica.wallet_qr`
- `segno` and `pypng` are present in the staged runtime

### macOS app starts but fails with a Qt platform plugin error

Check:

- the build/release flow used `cmake --install`
- `Contents/PlugIns/platforms/libqcocoa.dylib` exists
- `python3 wallet-qt/scripts/verify-bundle-layout.py --platform macos --path <app>` passes

### Windows package starts but fails with `qwindows.dll` missing

Check:

- the build/release flow used `cmake --install`
- `platforms\qwindows.dll` exists in the staged runtime
- the Linux cross-build was pointed at the correct `WINDOWS_QT_ROOT`

### Linux cross-build stops before CMake configure

Check:

- `./wallet-qt/scripts/build-windows-cross.sh --check` passes
- `WINDOWS_QT_ROOT` points at a Windows Qt prefix with `lib/cmake/Qt6/Qt6Config.cmake`
- `QT_HOST_PATH` points at a Linux Qt host tools prefix
- `WINDOWS_OPENSSL_ROOT` contains Windows-target headers, import libraries, and DLLs

### Packaged app still depends on the repo checkout

Check:

- `AnimicaNode.cmake` did not use editable installs
- the staged runtime includes `node/assets/spec/params.yaml`
- the staged runtime includes `node/assets/genesis/*.json`

## Release Verification Checklist

Use this after each native build:

- wallet opens
- balances load
- receive address shows a real QR
- QR saves as PNG
- send form opens
- history loads
- packaged app launches on the target OS without the repo checkout
