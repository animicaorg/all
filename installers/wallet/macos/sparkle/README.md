# Sparkle (macOS Updater) Integration

This document explains how the Animica Wallet macOS app integrates the **Sparkle 2.x** updater with **Ed25519** signatures, sandbox-friendly settings, and our existing CI packaging (DMG/PKG, notarization, staple).

> Sparkle 2 uses Ed25519 appcast signatures, supports sandboxed apps, and expects all update downloads to be **signed + notarized** and served over **TLS 1.2+**.

---

## Repo layout & placeholders

- `installers/wallet/macos/Info.plist.template`  
  Contains `{{SUFeedURL}}` and `{{SUPublicEDKey}}` placeholders filled by CI (see `installers/wallet/macos/scripts/build_release.sh`).
- `installers/wallet/config/channels.json`  
  Maps `stable`/`beta` → macOS **Sparkle appcast feed URLs**.
- `installers/wallet/macos/entitlements.plist`  
  Hardened runtime + sandbox; no JIT; network client ON.
- `installers/wallet/macos/sign_and_notarize.sh`  
  Deep-codesigns the `.app`, builds a DMG/PKG, submits for notarization, and staples.

---

## 1) Add Sparkle to the Flutter macOS host

Flutter desktop apps bundle a native macOS host at `macos/Runner.xcworkspace`. Integrate Sparkle via **Swift Package Manager**:

1. Open `macos/Runner.xcworkspace` in Xcode.
2. **File → Add Packages…** and use:

https://github.com/sparkle-project/Sparkle

3. Choose the latest **Sparkle 2.x** release and add these products to the **Runner** app target:
- `Sparkle` (framework)
- `Sparkle-InstallerLauncher` (XPC / helper)
- `Sparkle-AutoUpdate` (optional, for automatic install UI)
4. Xcode will add the frameworks under **Frameworks, Libraries, and Embedded Content**. Ensure they are **Embed & Sign**.

### App sandbox & runtime
- Keep `com.apple.security.app-sandbox = true` and `com.apple.security.network.client = true` (already set).
- No JIT or unsigned executable memory (already OFF in our entitlements).

---

## 2) Wire the updater in code (Swift)

If your Runner uses Swift (Flutter default since recent versions), create a small controller. Example:

```swift
// macos/Runner/UpdaterBridge.swift
import Cocoa
import Sparkle

final class UpdaterBridge: NSObject, SPUStandardUserDriverDelegate {
 private var updaterController: SPUStandardUpdaterController!

 override init() {
     super.init()
     // The controller reads SUFeedURL & SUPublicEDKey from Info.plist
     updaterController = SPUStandardUpdaterController(
         startingUpdater: true,
         updaterDelegate: nil,
         userDriverDelegate: self
     )
 }

 // Optional: menu action
 @objc func checkForUpdates(_ sender: Any?) {
     updaterController.checkForUpdates(sender)
 }

 // Optional user driver delegate methods can go here
}

Hook it in your AppDelegate.swift:

// macos/Runner/AppDelegate.swift
import Cocoa

@NSApplicationMain
class AppDelegate: FlutterAppDelegate {
    private let updater = UpdaterBridge()

    override func applicationDidFinishLaunching(_ notification: Notification) {
        super.applicationDidFinishLaunching(notification)
        // Automatic checks are enabled by Info.plist:
        // SUEnableAutomaticChecks=true, SUAllowsAutomaticUpdates=true
    }

    // Add a top-menu item to trigger updates (optional)
    @IBAction func checkForUpdates(_ sender: Any?) {
        updater.checkForUpdates(sender)
    }
}

If your Runner is Objective-C, use SPUStandardUpdaterController similarly in Obj-C.

⸻

3) Appcast signing keys (Ed25519)

Generate an Ed25519 keypair once and store the private key only in CI secrets.

# From a Sparkle 2 release tarball:
./bin/generate_keys
# Outputs:
#  - ed25519_public.pem
#  - ed25519_private.pem

	•	Base64 encode the public key (Sparkle accepts the raw base64) and set the CI env SPARKLE_PUBLIC_ED25519.
The build script injects it into Info.plist → SUPublicEDKey.
	•	Store ed25519_private.pem in your secret manager and inject in CI to sign updates.

⸻

4) Publishing updates

4.1 Build, sign, notarize, staple

Our pipeline already:
	1.	Builds the .app (Flutter).
	2.	Produces a .dmg and optional .pkg.
	3.	Codesigns with hardened runtime.
	4.	Submits to notarization and staples the ticket.

Artifacts land in dist/.

4.2 Sign the artifacts for the appcast

Use Sparkle’s signing tool (part of the release):

# Sign a DMG (preferred)
./bin/sign_update -s ed25519_private.pem dist/Animica-Wallet-<ver>-macOS.dmg
# The command prints an 'edSignature' you’ll embed in the appcast item.

4.3 Generate the appcast

Use Sparkle’s generator:

./bin/generate_appcast \
  --download-url-prefix "https://updates.animica.dev/wallet/macos/stable/" \
  dist/

This creates or updates dist/appcast.xml. You can also provide the signature inline if not auto-injected by the tool.

Our feeds are defined per channel in installers/wallet/config/channels.json and injected into SUFeedURL.

4.4 Appcast item (Sparkle 2 example)

<item>
  <title>Animica Wallet 1.2.3</title>
  <sparkle:releaseNotesLink>https://animica.dev/wallet/releases/1.2.3</sparkle:releaseNotesLink>
  <pubDate>Wed, 08 Oct 2025 10:00:00 GMT</pubDate>
  <enclosure
    url="https://updates.animica.dev/wallet/macos/stable/Animica-Wallet-1.2.3-macOS.dmg"
    sparkle:edSignature="8m7k9...BASE64...S1g="
    length="12345678"
    type="application/octet-stream"/>
  <sparkle:minimumSystemVersion>12.0</sparkle:minimumSystemVersion>
  <sparkle:version>1.2.3</sparkle:version>
  <sparkle:shortVersionString>1.2.3</sparkle:shortVersionString>
</item>

Requirements
	•	Host via HTTPS (TLS 1.2+).
	•	The DMG/PKG must be Apple-notarized and stapled (users avoid scary dialogs).
	•	The sparkle:edSignature must match the artifact (CI signs it with the private key).

⸻

5) CI outline
	1.	Build app (flutter build macos).
	2.	Patch Info.plist with:
	•	CFBundleIdentifier, CFBundleShortVersionString, CFBundleVersion
	•	SUFeedURL from channels.json
	•	SUPublicEDKey from SPARKLE_PUBLIC_ED25519
	3.	Run sign_and_notarize.sh → produces DMG (and PKG), notarized + stapled.
	4.	Run sign_update to compute sparkle:edSignature.
	5.	Run generate_appcast (or template your own) and upload artifact + appcast.xml to the channel path.
	6.	Invalidate CDN if applicable.

⸻

6) Security & ATS
	•	NSAppTransportSecurity is locked down in Info.plist.template to allow only our update hosts.
	•	Hardened runtime + sandbox with outbound network only.
	•	Never commit private keys; store ed25519_private.pem in CI secrets.
	•	Keep Sparkle up-to-date (it ships security fixes in minors).

⸻

7) Troubleshooting
	•	Update not offered: Check app’s SUFeedURL and that appcast.xml is reachable and well-formed.
	•	Signature mismatch: Ensure you signed the exact uploaded artifact and the base64 signature matches.
	•	Gatekeeper warnings: Verify the artifact is notarized and xcrun stapler validate <artifact> passes.
	•	Sandbox network denied: Confirm com.apple.security.network.client=true and host is allowed by ATS.

⸻

Useful commands (local)

# Validate notarization
xcrun stapler validate dist/Animica-Wallet-1.2.3-macOS.dmg
spctl -a -vv dist/Animica-Wallet-1.2.3-macOS.dmg

# Re-run appcast generation with a prefix
./bin/generate_appcast --download-url-prefix "https://updates.animica.dev/wallet/macos/stable/" dist/

# Print current SU keys in the app bundle
/usr/libexec/PlistBuddy -c 'Print :SUFeedURL' dist/Animica\ Wallet.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c 'Print :SUPublicEDKey' dist/Animica\ Wallet.app/Contents/Info.plist


⸻

Notes on channels
	•	Stable and Beta have separate feed URLs and directories.
	•	The app selects the feed via --channel in build_release.sh (defaults to stable).

