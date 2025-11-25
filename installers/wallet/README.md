# Wallet installers — Flutter build → signed installers per OS

This doc explains how to turn the Flutter desktop app into **signed, distributable installers** on macOS, Windows, and Linux. It references the helper scripts in `installers/scripts/` and shows local and CI flows.

> Repo scripts you’ll use:
>
> - `installers/scripts/setup_keychain_macos.sh`
> - `installers/scripts/import_p12_macos.sh`
> - `installers/scripts/import_pfx_windows.ps1`
> - `installers/scripts/verify_signatures.sh`

---

## Prerequisites

- **Flutter**: 3.x (stable), with desktop targets enabled:
  ```bash
  flutter config --enable-macos-desktop --enable-windows-desktop --enable-linux-desktop
  flutter doctor

	•	macOS: Xcode 15+, Command Line Tools. Developer ID Application + (optional) Developer ID Installer certs.
	•	Windows: Windows 10/11 SDK (for signtool if you prefer it), or PowerShell 5.1/7 for verification.
	•	Linux: Distribution packaging tools (e.g., appimagekit, dpkg-deb, rpmbuild) depending on your target.

Secrets for CI (see installers/scripts/store_signing_secret_ci.md):
	•	macOS:
	•	MACOS_SIGNING_CERT_BASE64, MACOS_SIGNING_CERT_PASSWORD, (optional) MACOS_CERT_CHAIN_BASE64
	•	(Optional notarization) ASC_API_KEY_P8_BASE64, ASC_KEY_ID, ASC_ISSUER_ID
	•	Windows:
	•	PFX_BASE64, PFX_PASSWORD (or import certificate manually on a secure build host)

⸻

1) Build Flutter binaries

macOS (universal or x64)

flutter build macos --release
# Output: build/macos/Build/Products/Release/<App>.app

Windows (x64)

flutter build windows --release
# Output: build\windows\runner\Release\<App>.exe (+ supporting DLLs)

Linux (x64)

flutter build linux --release
# Output: build/linux/x64/release/bundle/


⸻

2) macOS: codesign → (DMG or PKG) → notarize

2.1 Setup ephemeral keychain & import cert (CI or local)

# Creates a temporary keychain, unlocks, and sets search paths
installers/scripts/setup_keychain_macos.sh setup

# Import Developer ID .p12 (idempotent)
installers/scripts/import_p12_macos.sh

Env:
	•	MACOS_SIGNING_CERT_BASE64, MACOS_SIGNING_CERT_PASSWORD, MACOS_CERT_CHAIN_BASE64
	•	Optional: MACOS_KEYCHAIN_NAME, MACOS_KEYCHAIN_PASSWORD

2.2 Codesign the .app (deep, hardened runtime)

APP="build/macos/Build/Products/Release/<App>.app"
IDENT="${CODE_SIGN_IDENTITY_NAME:-Developer ID Application: Your Org (ABCDE12345)}"

# Sign embedded frameworks and app (deep)
codesign --force --deep --options runtime --timestamp \
  --sign "$IDENT" "$APP"

# Verify
codesign --verify --deep --strict --verbose=2 "$APP"
spctl -a -vv "$APP"

2.3 Package as DMG (recommended for drag-and-drop UX)

DMG="dist/<App>-macOS.dmg"
mkdir -p dist
hdiutil create -volname "<App>" -srcfolder "$APP" -ov -format UDZO "$DMG"

# (Optional) sign the DMG itself
codesign --force --options runtime --timestamp --sign "$IDENT" "$DMG"

2.4 Or package as PKG (installer)

PKG="dist/<App>-macOS.pkg"
mkdir -p dist
# Create a component package
pkgbuild --component "$APP" \
  --install-location "/Applications/<App>.app" \
  --identifier "com.yourorg.app" \
  --version "<SemVer>" \
  "dist/<App>.component.pkg"

# Wrap with productsign (use Developer ID Installer identity)
INSTALLER_IDENT="Developer ID Installer: Your Org (ABCDE12345)"
productsign --sign "$INSTALLER_IDENT" "dist/<App>.component.pkg" "$PKG"

2.5 Notarize + staple (DMG or PKG)

Using App Store Connect API key (preferred):

# Save API key to disk if coming from base64
echo "$ASC_API_KEY_P8_BASE64" | base64 --decode > asc_key.p8

xcrun notarytool submit "$DMG" \
  --key asc_key.p8 \
  --key-id "$ASC_KEY_ID" \
  --issuer "$ASC_ISSUER_ID" \
  --wait

xcrun stapler staple "$DMG"

2.6 Final verification

installers/scripts/verify_signatures.sh "$DMG"
# or
installers/scripts/verify_signatures.sh "$PKG"

Set APPLE_TEAM_ID=ABCDE12345 to enforce team-id match during verification.

⸻

3) Windows: package → sign MSIX

3.1 Import Code Signing PFX (CI or local)

# PowerShell
installers/scripts/import_pfx_windows.ps1

Env:
	•	PFX_BASE64, PFX_PASSWORD (or PFX_PATH)
	•	Exports CODE_SIGN_CERT_THUMBPRINT for convenience.

3.2 Create MSIX package

Options:
	•	Use the Flutter msix plugin (simple DX)
	•	Or use Microsoft MSIX Packaging Tool / makeappx

Using the msix plugin:

flutter pub add msix
flutter pub run msix:create
# Config via msix pubspec keys: displayName, publisher, identityName, logoPath, capabilities...
# Output: <App>.msix

Using MSIX Packaging Tool (GUI) or makeappx (CLI) if you have a custom pipeline.

3.3 Sign the MSIX

If you prefer signtool (SDK installed):

$MSIX = "dist\<App>-win.msix"
# Sign using thumbprint imported to CurrentUser\My
signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com `
  /sha1 $env:CODE_SIGN_CERT_THUMBPRINT `
  "$MSIX"

Alternatively, some MSIX tooling can sign as part of packaging—ensure it uses the same certificate.

3.4 Verify signature

# Works on Windows/macOS/Linux if PowerShell 7 is available
installers/scripts/verify_signatures.sh dist/*.msix
# Optional stricter checks:
#   WIN_CERT_ORG="Your Org, Inc." CODE_SIGN_CERT_THUMBPRINT="<thumb>" installers/scripts/verify_signatures.sh dist/*.msix


⸻

4) Linux: bundle + (optional) signature

Common options:
	•	AppImage:

# Use appimagetool to turn the bundle into AppImage
appimagetool build/linux/x64/release/bundle dist/<App>-linux.AppImage

You can GPG-sign the AppImage and publish a detached signature.

	•	.deb / .rpm:
	•	Use dpkg-deb or rpmbuild. If you ship repositories, sign the repo metadata with GPG.

Linux code-signing is not standardized like macOS/Windows; treat signatures as detached (GPG) and verify in your distribution channels.

⸻

5) CI examples (GitHub Actions)

macOS job (build → sign → notarize → verify)

jobs:
  macos:
    runs-on: macos-14
    environment: release
    steps:
      - uses: actions/checkout@v4
      - uses: subosito/flutter-action@v2
        with: { flutter-version: '3.x' }

      - run: flutter build macos --release

      - name: Setup keychain
        run: installers/scripts/setup_keychain_macos.sh setup
        env:
          MACOS_KEYCHAIN_PASSWORD: ${{ secrets.MACOS_KEYCHAIN_PASSWORD }}

      - name: Import Developer ID
        run: installers/scripts/import_p12_macos.sh
        env:
          MACOS_SIGNING_CERT_BASE64: ${{ secrets.MACOS_SIGNING_CERT_BASE64 }}
          MACOS_SIGNING_CERT_PASSWORD: ${{ secrets.MACOS_SIGNING_CERT_PASSWORD }}
          MACOS_CERT_CHAIN_BASE64: ${{ secrets.MACOS_CERT_CHAIN_BASE64 }}

      - name: Codesign & DMG
        run: |
          APP="build/macos/Build/Products/Release/<App>.app"
          IDENT="${CODE_SIGN_IDENTITY_NAME}"
          mkdir -p dist
          codesign --force --deep --options runtime --timestamp --sign "$IDENT" "$APP"
          hdiutil create -volname "<App>" -srcfolder "$APP" -ov -format UDZO "dist/<App>-macOS.dmg"

      - name: Notarize & staple
        run: |
          echo "$ASC_API_KEY_P8_BASE64" | base64 --decode > asc_key.p8
          xcrun notarytool submit "dist/<App>-macOS.dmg" --key asc_key.p8 --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER_ID" --wait
          xcrun stapler staple "dist/<App>-macOS.dmg"
        env:
          ASC_API_KEY_P8_BASE64: ${{ secrets.ASC_API_KEY_P8_BASE64 }}
          ASC_KEY_ID: ${{ secrets.ASC_KEY_ID }}
          ASC_ISSUER_ID: ${{ secrets.ASC_ISSUER_ID }}

      - name: Verify
        run: APPLE_TEAM_ID=ABCDE12345 installers/scripts/verify_signatures.sh dist/*.dmg

Windows job (build → msix → sign → verify)

  windows:
    runs-on: windows-2022
    environment: release
    steps:
      - uses: actions/checkout@v4
      - uses: subosito/flutter-action@v2
        with: { flutter-version: '3.x' }

      - name: Build
        shell: pwsh
        run: flutter build windows --release

      - name: Import PFX
        shell: pwsh
        run: installers/scripts/import_pfx_windows.ps1
        env:
          PFX_BASE64: ${{ secrets.PFX_BASE64 }}
          PFX_PASSWORD: ${{ secrets.PFX_PASSWORD }}

      - name: Pack MSIX (msix plugin)
        shell: pwsh
        run: |
          flutter pub add msix
          flutter pub run msix:create
          mkdir dist
          Copy-Item *.msix dist\

      - name: Sign with signtool
        shell: pwsh
        run: |
          $msix = Get-ChildItem dist\*.msix | Select-Object -First 1
          & "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe" sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /sha1 $env:CODE_SIGN_CERT_THUMBPRINT $msix

      - name: Verify
        shell: bash
        run: installers/scripts/verify_signatures.sh dist/*.msix
        env:
          WIN_CERT_ORG: "Your Org, Inc."


⸻

Troubleshooting
	•	macOS: “resource envelope is obsolete” → ensure --options runtime and re-sign all nested frameworks with --deep.
	•	Notarization fails: check notarytool log <submission-id> for entitlement or hardened runtime issues.
	•	Windows signtool missing: use MSIX plugin signing or install the Windows SDK; for CI, prefer a runner image with SDK preinstalled.
	•	Gatekeeper quarantine: staple the notarization and verify with spctl (our script also checks).
	•	CI secrets leakage: review workflow logs; ensure secrets are environment-scoped with required reviewers.

⸻

Release checklist
	•	Build Flutter app in --release for each OS
	•	Codesign (macOS) / Sign MSIX (Windows)
	•	Notarize + staple (macOS)
	•	Verify with installers/scripts/verify_signatures.sh
	•	Publish artifacts (and checksums) to your release channel

