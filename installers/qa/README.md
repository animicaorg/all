# Installers QA — Test Matrix & Smoke Steps

This guide defines **what we test**, **where we test it**, and the **exact smoke flows** to run for every tagged build of:
- **Wallet (Flutter, MV3 provider)**
- **Explorer Desktop (Tauri wrapper)**
- Shared **installers** and **update feeds** (Sparkle appcasts, WinGet, repo packages)

> Goal: catch packaging/signing/first-run/config regressions fast.  
> Scope: *install → first run → core features → update → uninstall*, across all supported OSes.

---

## 1) Builds Under Test (BUT)

Collect and record for each run:

- **Component**: Wallet | Explorer
- **Version / Tag**: e.g., `wallet-vX.Y.Z`, `explorer-vX.Y.Z`
- **Channel**: stable | beta
- **Artifacts**: filenames, sizes, SHA256 & SHA512
- **Git short SHA** and build toolchain versions (Rust/Node/Flutter/Tauri)

Use CI outputs (see `installers/ci/github/*` and artifact `SHA256SUMS.txt`, `SHA512SUMS.txt`).

---

## 2) Preflight Verification (All OSes)

**Checksums**
```bash
# macOS/Linux
shasum -a 256 <artifact>
shasum -a 512 <artifact>

# Windows (PowerShell)
Get-FileHash .\<artifact> -Algorithm SHA256

Code Signing
	•	macOS (DMG/APP/PKG):

spctl -a -vv <artifact_or_.app>
codesign --verify --deep --strict --verbose=2 <.app_or_.pkg>
xcrun stapler validate <.dmg_or_.pkg>   # notarization stapled


	•	Windows (MSIX/MSI/EXE):

signtool verify /pa /v .\<artifact>


	•	Linux:
	•	AppImage: checksum only.
	•	DEB/RPM: checksum; if repo-based, verify repo GPG metadata per platform docs.

If any verification fails, stop and file a blocker.

⸻

3) Test Matrix

OS	Versions	CPU	Wallet Artifacts	Explorer Artifacts	Notes
macOS	12 (Monterey), 13 (Ventura), 14 (Sonoma), 15 (Sequoia)	Intel & Apple Silicon	.dmg (signed+notarized), optional .pkg	.dmg (signed+notarized)	Sparkle appcast updates (stable & beta)
Windows	10 21H2+, 11 22H2/24H2	x64	.msix (preferred), optional NSIS .exe	.msi/.msix (per Tauri config)	WinGet manifest (stable)
Ubuntu	22.04 LTS, 24.04 LTS	x86_64	AppImage, .deb	AppImage, .deb	Wayland/X11 sanity
Debian	12	x86_64	.deb	.deb	
Fedora	39, 40	x86_64	.rpm	.rpm	
Flatpak	latest runtimes	x86_64	Flatpak	Flatpak	Portal perms check
Arch (optional)	rolling	x86_64	AppImage	AppImage	Smoke only

Run at least one platform per row per release; rotate full grid weekly.

⸻

4) Wallet — Smoke Steps (All OSes)

A. Install
	•	macOS: open DMG → drag to Applications; verify open gatekeeper prompt passes.
	•	Windows: install MSIX (allow sideloading if needed) or NSIS.
	•	Linux:
	•	AppImage: chmod +x *.AppImage && ./*.AppImage
	•	DEB: sudo apt install ./animica-wallet_*.deb
	•	RPM: sudo rpm -Uvh animica-wallet-*.rpm
	•	Flatpak: flatpak install --user <bundle>.flatpakref (if provided)

B. First Run & Onboarding
	1.	Launch app. Confirm version displayed.
	2.	Create new wallet (12/24-word mnemonic).
	3.	Verify:
	•	PQ key generated (Dilithium3 by default) and address format anim1… (bech32m).
	•	Lock/unlock cycle works; session PIN (macOS keychain/Windows DPAPI/Linux secret service) stores vault.

C. Network & RPC
	1.	Open Network settings:
	•	Default chainId and RPC URL populated.
	•	Press “Test Connection”: should fetch head height and params.
	2.	Subscribe to newHeads (if UI shows): height should tick over within the polling/WS interval.

D. Funds & Transfer (Dev/Test Network)
	1.	Get funds (via faucet CLI/service if available) or import a pre-funded test key.
	2.	Send a self-transfer (small amount):
	•	Preflight simulate (if supported) returns OK.
	•	Sign with PQ key; submit; see pending → confirmed.
	3.	Open Tx details:
	•	Receipt status=SUCCESS,
	•	Gas used present,
	•	Events/logs (if any) decode.

E. Backup & Restore
	1.	Export encrypted keystore (file) and/or show mnemonic (guarded).
	2.	Remove local account, then Import using keystore/mnemonic. Address must match.

F. Dapp Provider (MV3) — Basic
	1.	Open the demo dapp (from repo or hosted).
	2.	Connect via window.animica provider; approve session.
	3.	Call read (e.g., balance) and a sign request; verify prompts and results.

G. Update Flow
	•	macOS (Sparkle): from beta/stable prior version →
	•	Check for updates → download → install → relaunch.
	•	After update, network/account state persists; version increments; Sparkle signature trusted.
	•	Windows (WinGet or in-app):
	•	winget install --id Animica.Wallet (stable) or manual MSIX upgrade.
	•	Linux: AppImage: replace binary; DEB/RPM via package manager; Flatpak via flatpak update.

H. Uninstall & Residuals
	•	Remove the app; ensure:
	•	app binaries gone,
	•	user data either preserved in standard location (documented) or cleanly removed per policy,
	•	re-install starts clean when expected.

⸻

5) Explorer Desktop — Smoke Steps (All OSes)

A. Install

Follow OS-specific installer steps (as above).

B. Launch & URL Loader
	1.	On first run, app loads configured URL (from embedded default or config).
	2.	Toggle offline mode (if provided) → graceful message.
	3.	Switch network (if configurable) → height/chainId update.

C. Core Navigation
	1.	Blocks: latest head; drill into block → header/tx list/receipts.
	2.	Transactions: search by hash; receipt status and logs decode.
	3.	Addresses: balance & recent txs view.
	4.	WebSocket: live head updates (indicator should blink/tick).

D. Deep Links (if enabled)
	•	Open animica://tx/<hash> or animica://address/<bech32> → correct screen opens.

E. Update & Persist
	•	Perform the same update flow as Wallet (per OS).
	•	Verify window state/theme/network selection persist.

⸻

6) Negative & Edge Cases (Spot Check)
	•	Wrong chainId RPC → meaningful error surfaces; cannot send.
	•	Invalid address entry → client-side validation blocks.
	•	Locked vault → any sign/send requires unlock.
	•	Proxy/Firewall: app uses configured ports only; no unexpected outbound endpoints.
	•	Time skew: app still fetches heads; errors are understandable.

⸻

7) Regression Hotlist (Always Run)
	•	macOS notarization stapled on DMG.
	•	Windows signtool verify passes and MSIX installs without “Unknown publisher”.
	•	Linux AppImage starts on Wayland & X11 sessions.
	•	Wallet: Dilithium3 signing path used by default (not fallback).
	•	Explorer: WS newHeads subscription functional.

⸻

8) Update Feeds & Manifests
	•	Sparkle appcast (stable & beta) updated and signed (see installers/updates/**/appcast.xml).
	•	WinGet manifest updated for stable Wallet (see installers/wallet/windows/winget/).
	•	Checksums in feeds match uploaded artifacts.

⸻

9) Reporting Template

Copy into issue tracker:

Component: Wallet | Explorer
Version/Channel: vX.Y.Z (stable|beta)
OS/CPU: macOS 14 (M2) | Windows 11 23H2 (x64) | Ubuntu 24.04 (x86_64)
Installer: DMG | MSIX | AppImage | DEB | RPM | Flatpak
Steps:
1) …
2) …
Expected:
Actual:
Logs/Screens:
- codesign/signtool/spctl output (redacted)
- app console logs (if available)
Artifacts:
- filename + SHA256
Notes:
- Network used (dev/test/main), RPC URL, chainId


⸻

10) Automation Hooks
	•	Use project E2E suites where present:
	•	Wallet MV3 provider E2E (Playwright) — connect/send/basic call.
	•	Explorer smoke (Playwright) — loads, heads tick, block+tx navigation.
	•	Keep manual QA for installer/signing and OS integration; E2E covers in-app flows.

⸻

11) Quick Reference — Commands

macOS

spctl -a -vv <.app | .dmg | .pkg>
codesign --verify --deep --strict --verbose=2 <.app | .pkg>
xcrun stapler validate <.dmg | .pkg>

Windows

signtool verify /pa /v .\<artifact>
Get-FileHash .\<artifact> -Algorithm SHA256

Linux

# AppImage
chmod +x *.AppImage && ./*.AppImage
# DEB
sudo apt install ./animica-wallet_*.deb
# RPM
sudo rpm -Uvh animica-wallet-*.rpm


⸻

12) Exit Criteria

A build passes smoke if all of the following are true:
	•	✅ Checksums & signatures verified on at least one artifact per OS family.
	•	✅ Install → first run → key flows succeed:
	•	Wallet: create/import, RPC connect, send tx, see receipt SUCCESS.
	•	Explorer: loads, live heads, navigate block → tx → receipt.
	•	✅ Update flow succeeds on at least one OS per component (macOS Sparkle or Windows MSIX/WinGet).
	•	✅ Uninstall does not leave broken state; documented data persistence behavior holds.

If any signing/notarization or update signature check fails → blocker.

⸻

Keep this document updated alongside installer and CI workflow changes. Small drift in steps = bugs that reach users.
