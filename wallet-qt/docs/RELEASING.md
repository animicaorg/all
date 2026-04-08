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

If `website/public/wallet/` exists beside `wallet-qt/`, the Linux release script also refreshes the website download copies:

- `website/public/wallet/animica-wallet-linux.AppImage`
- `website/public/wallet/animica-wallet-linux.deb`
- `website/public/wallet/animica-wallet-linux.tar.gz`
- `website/public/wallet/animica-wallet-linux.sha256`

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

## 4. Windows release

Staged build:

```powershell
cd wallet-qt
.\scripts\build-windows.ps1
```

Per-user release:

```powershell
.\scripts\release-windows.ps1
```

Per-machine release:

```powershell
.\scripts\release-windows.ps1 -PerMachine
```

Signing placeholder:

```powershell
$env:CODESIGN_CERT = "thumbprint-or-pfx"
.\scripts\release-windows.ps1 -Sign
```

Outputs:

- `dist\wallet-qt\<version>\windows\*.zip`
- `dist\wallet-qt\<version>\windows\*.msi` when WiX Toolset v3 is installed
- `dist\wallet-qt\<version>\windows\SHA256SUMS`

Validate:

```powershell
.\scripts\smoke-test-windows.ps1 -WalletPath .\build\windows-release\stage
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
- WiX Toolset v3 is missing on Windows
- Apple signing or notarization credentials are missing on macOS
