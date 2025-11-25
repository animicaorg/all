# macOS distribution — codesign, notarization, and Sparkle updates

This guide covers **hardened runtime codesigning**, **notarization + stapling**, and **Sparkle 2** (Ed25519) auto-updates for the Wallet on macOS.

Related scripts you already have:

- `installers/scripts/setup_keychain_macos.sh`
- `installers/scripts/import_p12_macos.sh`
- `installers/scripts/verify_signatures.sh`

The stable feed we publish (see `installers/wallet/config/channels.json`):

https://updates.animica.dev/wallet/stable/macos/appcast.xml

---

## 1) Prereqs

- Xcode 15+ (Command Line Tools installed).
- **Developer ID Application** certificate in an **ephemeral keychain** (CI) or login keychain (local).
- App built with **hardened runtime** and no forbidden entitlements.
- Sparkle 2.x integrated (SPM/CocoaPods/manual), **Ed25519** signing enabled.

Env secrets (see `installers/scripts/store_signing_secret_ci.md`):
- `MACOS_SIGNING_CERT_BASE64`, `MACOS_SIGNING_CERT_PASSWORD`, `MACOS_CERT_CHAIN_BASE64`
- For notarization: `ASC_API_KEY_P8_BASE64`, `ASC_KEY_ID`, `ASC_ISSUER_ID`

---

## 2) Codesign (deep) + hardened runtime

We sign the app bundle and all embedded frameworks/plugins **deeply**, with timestamp and hardened runtime:

```bash
APP="build/macos/Build/Products/Release/Animica Wallet.app"
IDENT="${CODE_SIGN_IDENTITY_NAME:-Developer ID Application: Animica Labs (ABCDE12345)}"

# (CI) setup keychain & import certs
installers/scripts/setup_keychain_macos.sh setup
installers/scripts/import_p12_macos.sh

# Deep sign app + frameworks
codesign --force --deep --options runtime --timestamp \
  --sign "$IDENT" "$APP"

# Verify locally
codesign --verify --deep --strict --verbose=2 "$APP"
spctl -a -vv "$APP"

Notes
	•	Ensure Sparkle’s framework and any XPC helpers inside the app are also signed (the --deep pass does this).
	•	Avoid forbidden entitlements (e.g., device access) unless strictly needed.

⸻

3) Package → Notarize → Staple

We distribute a DMG (drag-and-drop). PKG works too, but DMG is the default.

DMG="dist/Animica-Wallet-macOS.dmg"
mkdir -p dist
hdiutil create -volname "Animica Wallet" -srcfolder "$APP" -ov -format UDZO "$DMG"
codesign --force --options runtime --timestamp --sign "$IDENT" "$DMG"

Notarize with App Store Connect API key (preferred):

echo "$ASC_API_KEY_P8_BASE64" | base64 --decode > asc_key.p8

xcrun notarytool submit "$DMG" \
  --key asc_key.p8 \
  --key-id "$ASC_KEY_ID" \
  --issuer "$ASC_ISSUER_ID" \
  --wait

xcrun stapler staple "$DMG"

Final verification:

APPLE_TEAM_ID=ABCDE12345 installers/scripts/verify_signatures.sh "$DMG"


⸻

4) Sparkle 2 setup (Ed25519, appcast, signatures)

Sparkle enables safe in-app updates on macOS outside the App Store.

4.1 Integrate Sparkle 2
	•	Prefer Swift Package Manager (Xcode → Project → Package Dependencies → sparkle-project/Sparkle).
	•	Embed Sparkle.framework and the Updater.app as recommended by Sparkle 2 docs.
	•	Add NSAppTransportSecurity exceptions if your appcast/feed or CDN uses custom TLS; otherwise keep ATS strict.

4.2 Generate Ed25519 update keys (once)

Use Sparkle’s generate_keys tool (ships with the project’s repo/tooling). This produces:
	•	Private key: keep in CI secrets or release infra only.
	•	Public key: add to your app’s Info.plist as SUPublicEDKey.

Example Info.plist entries:

<key>SUEnableAutomaticChecks</key><true/>
<key>SUFeedURL</key><string>https://updates.animica.dev/wallet/stable/macos/appcast.xml</string>
<key>SUPublicEDKey</key><string>QmFzZTY0RWQyNTUxOS1QdWJsaWNLZXk=</string>

Do not ship the private key. Rotate keys if compromised and publish a notice.

4.3 Building the appcast (RSS)

For each release, create an <item> with version, enclosure, length, and sparkle:edSignature:

<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" >
  <channel>
    <title>Animica Wallet Updates (Stable)</title>
    <link>https://updates.animica.dev/wallet/stable/macos/appcast.xml</link>
    <item>
      <title>Animica Wallet 1.2.3</title>
      <sparkle:minimumSystemVersion>12.0</sparkle:minimumSystemVersion>
      <sparkle:releaseNotesLink>https://updates.animica.dev/wallet/stable/notes/1.2.3.md</sparkle:releaseNotesLink>
      <enclosure
        url="https://updates.animica.dev/wallet/stable/macos/Animica-Wallet-1.2.3-macOS.dmg"
        sparkle:version="1.2.3"
        sparkle:shortVersionString="1.2.3"
        length="123456789"
        type="application/octet-stream"
        sparkle:edSignature="ZEdER...base64sig..." />
      <pubDate>Wed, 08 Oct 2025 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>

Generate sparkle:edSignature using Sparkle’s sign_update (or generate_appcast) with your private Ed25519 key. The signature covers the exact bytes of the DMG/ZIP—hash changes invalidate the signature.

Our installers/wallet/config/channels.json mirrors these locations so CI can drive the appcast update automatically.

4.4 User experience / security
	•	Sparkle validates the Ed25519 signature before mounting/executing updates.
	•	Gatekeeper still enforces notarization on mounted artifacts; we staple DMGs to avoid network-time dependency.
	•	When first launched from a translocated path, Sparkle may prompt users to move the app to /Applications before updating.

⸻

5) CI example (build → sign → notarize → appcast → verify)

jobs:
  macos-release:
    runs-on: macos-14
    environment: release
    steps:
      - uses: actions/checkout@v4

      - name: Flutter build (macOS)
        run: flutter build macos --release

      - name: Keychain + certs
        run: |
          installers/scripts/setup_keychain_macos.sh setup
          installers/scripts/import_p12_macos.sh
        env:
          MACOS_SIGNING_CERT_BASE64: ${{ secrets.MACOS_SIGNING_CERT_BASE64 }}
          MACOS_SIGNING_CERT_PASSWORD: ${{ secrets.MACOS_SIGNING_CERT_PASSWORD }}
          MACOS_CERT_CHAIN_BASE64: ${{ secrets.MACOS_CERT_CHAIN_BASE64 }}

      - name: Codesign app and DMG
        run: |
          APP="build/macos/Build/Products/Release/Animica Wallet.app"
          IDENT="${CODE_SIGN_IDENTITY_NAME}"
          codesign --force --deep --options runtime --timestamp --sign "$IDENT" "$APP"
          mkdir -p dist
          hdiutil create -volname "Animica Wallet" -srcfolder "$APP" -ov -format UDZO "dist/Animica-Wallet-${{ github.ref_name }}-macOS.dmg"
          codesign --force --options runtime --timestamp --sign "$IDENT" "dist/Animica-Wallet-${{ github.ref_name }}-macOS.dmg"

      - name: Notarize + staple
        run: |
          echo "$ASC_API_KEY_P8_BASE64" | base64 --decode > asc_key.p8
          xcrun notarytool submit "dist/Animica-Wallet-${{ github.ref_name }}-macOS.dmg" --key asc_key.p8 --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER_ID" --wait
          xcrun stapler staple "dist/Animica-Wallet-${{ github.ref_name }}-macOS.dmg"
        env:
          ASC_API_KEY_P8_BASE64: ${{ secrets.ASC_API_KEY_P8_BASE64 }}
          ASC_KEY_ID: ${{ secrets.ASC_KEY_ID }}
          ASC_ISSUER_ID: ${{ secrets.ASC_ISSUER_ID }}

      - name: Sign appcast item (Sparkle)
        run: |
          # Example: use Sparkle's sign_update to produce sparkle:edSignature
          # echo "$SPARKLE_PRIVATE_KEY_B64" | base64 --decode > ed25519_priv.pem
          # sign_update ed25519_priv.pem dist/Animica-Wallet-${{ github.ref_name }}-macOS.dmg > sig.txt
          # Replace token in appcast template and upload both DMG and appcast.xml to the CDN.
          echo "Update your appcast with the generated edSignature"
        env:
          SPARKLE_PRIVATE_KEY_B64: ${{ secrets.SPARKLE_PRIVATE_KEY_B64 }}

      - name: Final verify
        run: APPLE_TEAM_ID=ABCDE12345 installers/scripts/verify_signatures.sh dist/*.dmg


⸻

6) Troubleshooting
	•	“resource envelope is obsolete” — re-sign with --options runtime and ensure all nested frameworks are signed.
	•	“The application is damaged and can’t be opened” — missing/not failed staple; re-notarize and staple DMG.
	•	Sparkle update fails with signature error — wrong edSignature or artifact changed after signing; re-generate signature against the final DMG bytes.
	•	Gatekeeper still blocks — verify spctl -a -vv on the artifact; confirm Team ID and notarization ticket present.
	•	Translocation quirks — prompt user to move to /Applications (Sparkle has built-in convenience helpers in modern templates).

⸻

7) Checklist
	•	Deep codesign app (--options runtime --timestamp)
	•	Package DMG (and sign the DMG)
	•	Notarize with notarytool and staple
	•	Generate Ed25519 signature for the DMG and update appcast <enclosure>
	•	Upload DMG + appcast.xml to CDN (correct length, URL, signature)
	•	Run verify_signatures.sh and manual spctl sanity checks

