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
- embedded node: `lib/animica-wallet/node/venv/bin/python`
- bundled chain params: `lib/animica-wallet/node/assets/spec/params.yaml`
- bundled genesis files: `lib/animica-wallet/node/assets/genesis/*.json`

### Linux AppImage

- executable: `usr/bin/animica-wallet`
- embedded node: `usr/lib/node/venv/bin/python`
- bundled chain params: `usr/lib/node/assets/spec/params.yaml`

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
- `linuxdeployqt` for AppImage generation
- `dpkg-deb` / CPack tooling for `.deb`

### macOS

- Qt 6
- CMake 3.16+
- Python 3.10+
- Xcode command line tools
- `create-dmg` for polished DMG generation
- Apple signing credentials only if you are doing Developer ID signing/notarization

### Windows

- Qt 6 for MSVC
- CMake 3.16+
- Python 3.10+
- Visual Studio 2022 or newer with C++ tools
- WiX Toolset v3 (`candle.exe`, `light.exe`) for MSI generation through CPack
- `signtool.exe` only if you are code signing

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
- `dist/wallet-qt/<version>/linux/SHA256SUMS`

### Validate Linux artifacts

```bash
./wallet-qt/scripts/smoke-test-linux.sh <path-to-AppImage-or-wallet-executable>
```

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

### Build a staged runtime

```powershell
cd wallet-qt
.\scripts\build-windows.ps1
```

Expected staged output:

- `wallet-qt\build\windows\stage\`

### Build release ZIP and MSI

Per-user MSI/ZIP:

```powershell
cd wallet-qt
.\scripts\release-windows.ps1
```

Per-machine MSI/ZIP:

```powershell
cd wallet-qt
.\scripts\release-windows.ps1 -PerMachine
```

Artifacts:

- `dist\wallet-qt\<version>\windows\AnimicaWallet-<version>-windows-x64.zip`
- `dist\wallet-qt\<version>\windows\AnimicaWallet-<version>-windows-x64.msi` when WiX v3 is installed
- `dist\wallet-qt\<version>\windows\SHA256SUMS`

### Windows signing flow

```powershell
$env:CODESIGN_CERT = "thumbprint-or-path-to-pfx"
.\scripts\release-windows.ps1 -Sign
```

### Validate a staged or installed Windows runtime

```powershell
.\scripts\smoke-test-windows.ps1 -WalletPath .\build\windows-release\stage
```

### Native Windows validation checklist

- run `python .\wallet-qt\scripts\verify-bundle-layout.py --platform windows --path <stage-or-install-dir>`
- confirm `platforms\qwindows.dll` exists
- run the packaged executable outside the repo checkout
- confirm the receive tab renders a real QR and can save PNG
- confirm Add/Remove Programs metadata is correct after MSI install
- test uninstall from Apps & Features or `msiexec /x <product-code-or-msi>`
- if signing is enabled, verify with `signtool verify /pa <artifact>`

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
- `windeployqt.exe` is available if you need the fallback deployment step

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
