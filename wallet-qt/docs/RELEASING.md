# Releasing Animica Wallet

This checklist assumes native builds on the target platform.

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
- `dist/wallet-qt/<version>/linux/SHA256SUMS`

## 3. macOS release

```bash
cd wallet-qt
./scripts/release-mac.sh --dmg
```

Outputs:

- `dist/wallet-qt/<version>/macos/*.app`
- `dist/wallet-qt/<version>/macos/*.dmg`
- `dist/wallet-qt/<version>/macos/SHA256SUMS`

For signing/notarization:

```bash
export CODESIGN_IDENTITY="Developer ID Application: ..."
export APPLE_ID="you@example.com"
export APPLE_TEAM_ID="TEAMID"
./scripts/release-mac.sh --sign --notarize --dmg
```

## 4. Windows release

```powershell
cd wallet-qt
.\scripts\release-windows.ps1
```

Outputs:

- `dist\wallet-qt\<version>\windows\*.msi` when WiX is available
- `dist\wallet-qt\<version>\windows\*.zip` otherwise
- `dist\wallet-qt\<version>\windows\SHA256SUMS`

For signing:

```powershell
$env:CODESIGN_CERT = "thumbprint-or-pfx"
.\scripts\release-windows.ps1 -Sign
```

## 5. Validate artifacts

Run the platform smoke tests where supported:

```bash
./wallet-qt/scripts/smoke-test-linux.sh <AppImage-or-binary>
./wallet-qt/scripts/smoke-test-mac.sh <AnimicaWallet.app>
```

Also run the manual operator checklist in [operator-checklist.md](operator-checklist.md).

## 6. Publish

Ship the artifacts together with:

- `SHA256SUMS`
- release notes
- platform-specific install instructions

## Common Failure Modes

- Embedded-node configure fails because Python packages cannot be installed on the build host.
- Linux packaging host lacks `linuxdeployqt`.
- Windows host lacks WiX or `windeployqt`.
- macOS signing/notarization credentials are missing.
