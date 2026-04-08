# Wallet Packaging Guide

## Scope

Packaging in `wallet-qt/` targets a bundled desktop wallet, not a thin remote-RPC shell. All release scripts now configure with `-DWALLET_REMOTE_RPC_ONLY=OFF` so the embedded node and Python wallet bridge are included.

The bundled runtime is validated against:

- `rpc`
- `animica.qt_wallet_bridge`
- `omni_sdk`
- `core`

## Verified Runtime Layout

### Linux install tree

Verified locally:

- executable: `/tmp/walletqt-bundled-install/bin/animica-wallet`
- embedded node: `/tmp/walletqt-bundled-install/lib/animica-wallet/node/venv/bin/python`

Equivalent packaged layout:

- `/usr/bin/animica-wallet`
- `/usr/lib/animica-wallet/node/venv/bin/python`

### AppImage

Expected runtime path inside AppImage:

- `usr/bin/animica-wallet`
- `usr/lib/node/venv/bin/python`

### macOS

Expected runtime path:

- `AnimicaWallet.app/Contents/MacOS/AnimicaWallet`
- `AnimicaWallet.app/Contents/Resources/node/venv/bin/python`

### Windows

Expected runtime path:

- `animica-wallet.exe`
- `node\venv\Scripts\python.exe`

## Prerequisites

### Linux

- Qt 6
- CMake 3.16+
- Python 3.10+
- `linuxdeployqt` for AppImage
- `dpkg-deb` for `.deb`

### macOS

- Qt 6
- CMake 3.16+
- Python 3.10+
- Xcode command line tools
- `create-dmg` for DMG generation

### Windows

- Qt 6 for MSVC
- CMake 3.16+
- Python 3.10+
- Visual Studio 2019 or newer
- WiX Toolset for MSI
- `windeployqt.exe` recommended for ZIP staging and Qt runtime deployment

## Exact Build Commands

### Linux AppImage

```bash
cd wallet-qt
./scripts/release-linux.sh --appimage-only
```

Artifact:

- `dist/wallet-qt/<version>/linux/AnimicaWallet-<version>-linux-<arch>.AppImage`

### Linux DEB

```bash
cd wallet-qt
./scripts/release-linux.sh --deb-only
```

Artifact:

- `dist/wallet-qt/<version>/linux/animica-wallet_<version-without-v>_<deb-arch>.deb`

### macOS app bundle / DMG

```bash
cd wallet-qt
./scripts/release-mac.sh --dmg
```

Artifacts:

- `dist/wallet-qt/<version>/macos/AnimicaWallet-<version>-macos-<arch>.app`
- `dist/wallet-qt/<version>/macos/AnimicaWallet-<version>-macos-<arch>.dmg`

### Windows MSI / ZIP fallback

```powershell
cd wallet-qt
.\scripts\release-windows.ps1
```

Artifacts:

- MSI when WiX is installed:
  `dist\wallet-qt\<version>\windows\AnimicaWallet-<version>-windows-<arch>.msi`
- ZIP fallback otherwise:
  `dist\wallet-qt\<version>\windows\AnimicaWallet-<version>-windows-<arch>.zip`

## What the Release Scripts Do

- build the Qt wallet with `WALLET_REMOTE_RPC_ONLY=OFF`
- build the embedded Python node environment
- verify the bridge/runtime imports
- stage platform-specific runtime files
- produce checksums in `SHA256SUMS`

On Windows, the fallback ZIP path now prefers `windeployqt` so the stage contains Qt runtime DLLs instead of only the executable.

## Troubleshooting

### `pip` or dependency install fails during bundled configure

Cause:

- no network access while building the embedded venv

Fix:

- rerun the configure/build outside a restricted sandbox
- or pre-populate the build host with the required Python dependencies

### AppImage starts but embedded node cannot be found

Check:

- `usr/lib/node/venv/bin/python` exists inside the extracted AppImage

The runtime search path now explicitly includes the AppImage layout.

### Linux package install works but wallet cannot find embedded node

Check:

- `/usr/lib/animica-wallet/node/venv/bin/python`

The wallet and node manager now look in the installed package path, not only in the build tree.

### Windows MSI missing Qt runtime

Check:

- build with Qt 6
- ensure WiX and Qt deployment tools are available
- verify `cmake --install` includes the deployed Qt runtime during MSI creation

### macOS bundle opens but node will not start

Check:

- `AnimicaWallet.app/Contents/Resources/node/venv/bin/python`
- code signing/notarization if distributing publicly

## Reproducibility Notes

- Build on the native target OS for DMG/MSI/AppImage generation.
- Keep the same Python major/minor version across release machines where possible.
- Preserve the generated `SHA256SUMS` file with release artifacts.
