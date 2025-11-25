# Animica Wallet — Windows Packaging & Signing

This document explains how we build, sign, and ship the Windows desktop wallet. We support two packaging strategies:

- **MSIX** (preferred): modern, sandboxed installer with OS-level updates via *App Installer*.
- **NSIS** (fallback/legacy): classic EXE installer with custom silent switches; requires our own update logic.

Both outputs are fully **code-signed (SHA-256)** to satisfy SmartScreen and enterprise policy.

---

## 1) Targets & Toolchain

- **Architectures:** `x64` (primary), `arm64` (optional).
- **Build:** Flutter Desktop (Windows).
- **SDKs & Tools (CI/Dev):**
  - Visual Studio 2022 (Desktop development with C++).
  - Windows 11/10 SDK.
  - `signtool.exe` (from Windows SDK).
  - `makeappx.exe` / `MakeAppx` (MSIX packaging).
  - `makemsix.exe` (optional newer packager) or *Windows App Packaging Project* (VS).
  - `powershell` (for PFX import — see `installers/scripts/import_pfx_windows.ps1`).
  - **Optional for NSIS:** `makensis.exe`.

> We do **not** require MSI. Use **MSIX** where possible.

---

## 2) Code Signing Certificates

We sign **all** shipped binaries and installers.

- **Certificate:** Authenticode Code Signing certificate (`.pfx`), ideally **EV** for faster SmartScreen reputation.
- **Storage:** Keep the PFX in the CI secret store; decrypt in-job and import **per-run** only.
- **Import:** Use `installers/scripts/import_pfx_windows.ps1` in CI:
  ```powershell
  # Example (CI):
  # $env:CSC_PFX_BASE64 contains base64 of encrypted PFX
  # $env:CSC_PFX_PASSWORD contains the password
  powershell -ExecutionPolicy Bypass -File installers/scripts/import_pfx_windows.ps1

	•	Timestamping: Always timestamp signatures (/tr https://timestamp.digicert.com /td sha256).

⸻

3) MSIX (Preferred)

3.1 Overview

MSIX packages the app + manifest and runs inside a light container. Benefits:
	•	Clean install/uninstall.
	•	Delta updates via App Installer (.appinstaller feed) — no custom updater code.
	•	Enterprise-friendly (intune/wsfB) & UWP-like capabilities declaration.

Trade-offs:
	•	Some file system/registry writes are virtualized; design with that in mind.
	•	Requires the app to declare needed capabilities in the MSIX manifest.

3.2 MSIX Manifest Essentials
	•	Identity — Name, Publisher (must match certificate subject), Version.
	•	Properties — display name, description, logo assets.
	•	Capabilities — network, certificates (none elevated), etc.
	•	If we use a desktop bridge, ensure the Flutter runner EXE is declared correctly.

We keep a template in installers/wallet/windows/msix/Package.appxmanifest.tmpl (to be created alongside build scripts) with {{BUNDLE_ID}}, {{DISPLAY_NAME}}, {{VERSION}}, and channel feed placeholders.

3.3 Build → Package → Sign
	1.	Build Flutter Windows:

flutter build windows --release
# Output: build/windows/x64/runner/Release/Animica Wallet.exe


	2.	Layout the package folder (Appx layout):

dist/msix/Appx\*      # assets, VCLibs if needed
dist/msix/AppxManifest.xml
dist/msix/Animica Wallet.exe

Use MakeAppx to validate layout if needed.

	3.	Create MSIX:

MakeAppx pack /d dist\msix /p dist\Animica-Wallet_1.2.3.0_x64.msix


	4.	Sign MSIX:

signtool sign `
  /fd sha256 `
  /tr https://timestamp.digicert.com `
  /td sha256 `
  /n "Animica Labs, Inc." `
  dist\Animica-Wallet_1.2.3.0_x64.msix


	5.	Verify:

signtool verify /pa /all /v dist\Animica-Wallet_1.2.3.0_x64.msix
Get-AuthenticodeSignature dist\Animica-Wallet_1.2.3.0_x64.msix | Format-List



3.4 App Installer (Updates)

Publish a channel-specific .appinstaller next to the MSIX, e.g.:

<?xml version="1.0" encoding="utf-8"?>
<AppInstaller Uri="https://updates.animica.dev/wallet/windows/stable/Animica-Wallet.appinstaller"
              Version="1.2.3.0"
              xmlns="http://schemas.microsoft.com/appx/appinstaller/2017/2">
  <MainPackage Name="AnimicaWallet"
               Publisher="CN=Animica Labs, Inc., O=Animica Labs, L=…, S=…, C=US"
               Version="1.2.3.0"
               ProcessorArchitecture="x64"
               Uri="https://updates.animica.dev/wallet/windows/stable/Animica-Wallet_1.2.3.0_x64.msix" />
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="24" />
    <AutomaticBackgroundTask />
    <ShowPrompt />
  </UpdateSettings>
</AppInstaller>

Users install via the .appinstaller link once. Windows then auto-updates as new MSIX versions are published.

Keep per-channel feeds (e.g., /stable/, /beta/), mirroring macOS Sparkle channels.

⸻

4) NSIS (Fallback)

Use only if MSIX is not viable in a target environment.

4.1 Pros/Cons
	•	Pros: Works everywhere, no container constraints, easy to add custom steps.
	•	Cons: No OS-native updates — you must implement your own update check/download and run the installer silently; uninstall isn’t as clean; elevation prompts are frequent.

4.2 Build → Pack → Sign
	1.	Build Flutter Windows (same as MSIX).
	2.	Run NSIS script (installer.nsi) to produce Animica-Wallet-Setup-1.2.3.exe.
	3.	Sign the EXE:

signtool sign `
  /fd sha256 `
  /tr https://timestamp.digicert.com `
  /td sha256 `
  /n "Animica Labs, Inc." `
  dist\Animica-Wallet-Setup-1.2.3.exe


	4.	Silent install flags (use in the auto-updater flow):

Animica-Wallet-Setup-1.2.3.exe /S


	5.	Verify signature:

signtool verify /pa /all /v dist\Animica-Wallet-Setup-1.2.3.exe



Consider publishing a small JSON feed per channel with version, url, sha256, and signature to drive the app’s update checker.

⸻

5) Versioning & Channels
	•	Version format: Major.Minor.Patch.Build (MSIX requires 4 parts, e.g., 1.2.3.0).
	•	Channel config: Leverage installers/wallet/config/channels.json to mirror macOS; add Windows endpoints:

{
  "channels": [
    {
      "id": "stable",
      "platforms": {
        "windows": {
          "appinstaller": "https://updates.animica.dev/wallet/windows/stable/Animica-Wallet.appinstaller",
          "msix_base":   "https://updates.animica.dev/wallet/windows/stable/"
        }
      }
    }
  ]
}



⸻

6) CI Outline (Windows)
	1.	Checkout & restore caches (Flutter, VS build).
	2.	Import signing cert via installers/scripts/import_pfx_windows.ps1.
	3.	flutter build windows --release (x64).
	4.	MSIX path (preferred):
	•	Render AppxManifest.xml from template (set Version, Identity, logos).
	•	MakeAppx pack → .msix.
	•	signtool sign → sign .msix.
	•	Generate/refresh *.appinstaller with new Version and MSIX URL.
	•	Upload artifacts to channel path (CDN).
	5.	NSIS path (if enabled):
	•	Run makensis → Setup.exe.
	•	signtool sign the EXE.
	•	Publish releases.json (channel feed) for app auto-update.

Post-steps:
	•	Run basic smoke tests on a Windows VM (launch app, version check).
	•	Publish artifacts + invalidate CDN.

⸻

7) Verification & Troubleshooting
	•	Signature check:

signtool verify /pa /all /v dist\Animica-Wallet_*.msix
signtool verify /pa /all /v dist\Animica-Wallet-Setup-*.exe


	•	SmartScreen warnings: EV certs help; reputation also builds with download volume and consistent signing.
	•	MSIX install fails: Validate that Publisher in manifest matches the certificate subject; check capabilities; ensure version strictly increases.
	•	MSIX updates not arriving: Confirm .appinstaller points to the new MSIX and Version is higher; check Windows Update service policies; winget can also install from MSIX feeds for testing.

⸻

8) Security Notes
	•	Never commit private keys. PFX is injected at runtime in CI and removed after the job.
	•	Use SHA-256 everywhere and timestamp signatures.
	•	Maintain channel isolation: stable users must not accidentally receive beta builds.

⸻

9) Directory Hints (to add with scripts)

installers/wallet/windows/
  msix/
    Package.appxmanifest.tmpl
    Assets/            # square icons, wide tiles, etc.
    VCLibs/            # optional, if statically not linked
  nsis/
    installer.nsi
    icons/


⸻

10) Quick Commands

# Build Flutter
flutter build windows --release

# Pack MSIX
MakeAppx pack /d dist\msix /p dist\Animica-Wallet_1.2.3.0_x64.msix

# Sign
signtool sign /fd sha256 /tr https://timestamp.digicert.com /td sha256 /n "Animica Labs, Inc." dist\Animica-Wallet_1.2.3.0_x64.msix

# Verify
signtool verify /pa /all /v dist\Animica-Wallet_1.2.3.0_x64.msix


⸻

Summary: Ship MSIX with an App Installer feed for native updates. Keep NSIS as a fallback. Sign every artifact, timestamp signatures, and publish per-channel.
