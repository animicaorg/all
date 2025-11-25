# Animica Wallet — Windows Release QA Checklist

**Scope:** MSIX (primary) and optional NSIS installer builds for Windows 10/11 (x64).  
**Inputs:** a signed `.msix` built via `installers/wallet/windows/msix/MakeMSIX.ps1` and/or an NSIS `Setup.exe`.  
**Artifacts under test:**
- `dist/windows/<channel>/Animica-Wallet_<VER>_x64.msix`
- `dist/windows/<channel>/Animica-Wallet-Setup-<VER>.exe` (optional NSIS)
- `installers/wallet/windows/winget/manifest.yaml` (local test before PR)

---

## 0) Release metadata (fill before testing)

| Field | Value |
|---|---|
| Version (semver + build) | `1.0.0 (1.0.0.0 for MSIX Identity)` |
| Git commit / tag |  |
| Channel | stable / beta |
| Build runner | GitHub Actions / local |
| TSA URL | `https://timestamp.digicert.com` |
| Cert Subject (Publisher) | `CN=Animica Labs, Inc.` |
| Cert Thumbprint |  |
| Cert Expiration |  |
| MSIX SHA256 |  |
| PFN (Package Family Name) |  |

---

## 1) Build provenance (P0)

- [ ] Build produced by clean CI runner; no local uncommitted changes
- [ ] Reproducible flags documented; versions pinned
- [ ] `Package.appxmanifest` Identity `Publisher` matches cert subject
- [ ] Version scheme OK: **MSIX** requires 4-part `Major.Minor.Patch.Build`

**Capture:**
```powershell
git rev-parse HEAD
Get-Content installers/wallet/windows/msix/Package.appxmanifest


⸻

2) Integrity & signing (P0)

MSIX
	•	SHA256 matches release notes / pipeline output
	•	Signature present and valid (chain to trusted root)
	•	RFC3161 timestamp present (SHA-256)

# Hash
certutil -hashfile dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix SHA256

# Verify signature
signtool verify /pa /all /v dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix

NSIS (optional)
	•	Installer signed and timestamped

signtool verify /pa /all /v dist\windows\stable\Animica-Wallet-Setup-1.0.0.exe


⸻

3) Manifest sanity (P0)
	•	Identity Name, Publisher, Version, ProcessorArchitecture correct
	•	Applications/Application/Executable matches shipped EXE name
	•	Capabilities minimal: internetClient, runFullTrust only
	•	Logos exist at referenced paths (no broken tiles)

# PFN from package
(Get-AppPackage -Path dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix).PackageFamilyName

# Quick unpack to inspect files (optional)
MakeAppx unpack /p dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix /d .\_unpacked /o


⸻

4) Installation paths & ARP entry (P0)

MSIX
	•	Install via double-click (App Installer) completes with no UAC errors
	•	App appears in Start menu & launches
	•	winget install --manifest path works (local test)
	•	App is listed in Settings → Apps → Installed apps with correct version & publisher

Add-AppxPackage dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix
Get-AppxPackage -Name AnimicaWallet* | Select Name, PackageFullName, Publisher, Version

NSIS (optional)
	•	Default install path: C:\Program Files\Animica Wallet
	•	Start menu & desktop shortcuts created
	•	ARP keys present

reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\AnimicaWallet" /s


⸻

5) Functional smoke (P0)
	•	First launch succeeds; app window renders within 5s on cold start
	•	Basic navigation: Home → Send → Receive → Settings works
	•	Connects to RPC URL from config (default devnet or provided)
	•	Simulate or send a trivial tx using built-in flow (when devnet available)
	•	Logs are created (see section 10)

If RPC not available, verify network calls fail gracefully with actionable error.

⸻

6) Network & firewall prompts (P1)
	•	No unexpected Windows firewall prompts at launch
	•	All outbound calls are HTTPS (inspect via Fiddler/mitm disabled for prod cert pinning if applicable)
	•	CORS handled by node/services as expected (no in-app CORS errors)

⸻

7) Permissions & sandbox (P1)
	•	Capabilities limited to internetClient & runFullTrust
	•	No attempts to write outside user profile %LOCALAPPDATA% for app data
	•	No elevation requests during normal use

⸻

8) Update & upgrade paths (P1)
	•	WinGet:
	•	winget validate passes on our local manifest
	•	Local install via manifest works
	•	Upgrade flow simulated by bumping version locally and re-install

winget validate installers\wallet\windows\winget\manifest.yaml
winget install --manifest installers\wallet\windows\winget\manifest.yaml --silent
winget list | findstr /I "Animica"


⸻

9) Uninstall & cleanup (P0)

MSIX
	•	Uninstall from Settings removes package
	•	Reinstall works after uninstall (no dangling lock files)

Get-AppxPackage AnimicaWallet* | Remove-AppxPackage

NSIS (optional)
	•	Uninstall removes files, shortcuts, ARP entry

⸻

10) Logging & diagnostics (P1)
	•	App writes structured logs (or Windows Event Viewer entries)
	•	Crash dumps (if any) saved to %LOCALAPPDATA%\AnimicaWallet\Logs or similar
	•	QA can gather logs with a single zip command

$log = "$env:LOCALAPPDATA\AnimicaWallet\Logs"
if (Test-Path $log) { Compress-Archive -Path "$log\*" -DestinationPath ".\wallet-logs.zip" -Force }
Get-WinEvent -LogName "Application" -MaxEvents 50 | Where-Object { $_.Message -like "*Animica*" } | Format-List


⸻

11) Accessibility & i18n (P2)
	•	Keyboard navigation covers primary UI
	•	High-contrast mode: UI legible
	•	Strings render for en-US (other locales optional in this milestone)

⸻

12) Security checks (P0/P1)
	•	Defender scan clean (MpCmdRun.exe -Scan -ScanType 3 -File <artifact>)
	•	No embedded unsigned DLLs in shipped dir
	•	Cert chain trusted, no weak digest
	•	Timestamp present (prevents signature expiry breakage)

# Quick scan example (requires Defender CLI)
# "C:\Program Files\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix
Get-ChildItem "C:\Program Files\Animica Wallet" -Filter *.dll | ForEach-Object { signtool verify /pa /v $_.FullName | Out-Null }


⸻

13) Performance (P2)
	•	Cold start < 5s on Windows 10 (i5/8GB/SSD) baseline
	•	Subsequent start < 2s

Measure:

Measure-Command { Start-Process "C:\Program Files\Animica Wallet\Animica Wallet.exe" -PassThru | Wait-Process }


⸻

14) Test matrix (run subset on each)

OS	Build	Arch	Result	Notes
Windows 11 24H2	26100+	x64		
Windows 11 23H2	22631+	x64		
Windows 10 22H2	19045+	x64		


⸻

15) Winget publishing checklist (P0 for release, can be post-QA)
	•	PackageIdentifier = AnimicaLabs.AnimicaWallet
	•	PackageVersion = 1.0.0
	•	InstallerUrl points to public HTTPS
	•	InstallerSha256 matches MSIX
	•	PackageFamilyName filled from MSIX
	•	winget validate passes
	•	PR opened via wingetcreate submit

⸻

16) Rollback plan
	•	Previous stable MSIX link preserved
	•	Winget PR can be reverted; ensure PR number noted here: #
	•	CI can republish N-1 within 30 minutes

⸻

17) Known limitations (document)
	•	No auto-update aside from Winget upgrade (by design)
	•	First-run RPC must be reachable; else user sees offline banner
	•	(List any other deltas vs. spec)

⸻

18) Attachments (paste or link)
	•	MSIX SHA256
	•	signtool verify output (text)
	•	Screenshots: installation, ARP entry, app running, uninstall
	•	Logs archive (if issues)

⸻

Severity legend
	•	P0 — must pass before release
	•	P1 — should pass; fix prior to public announcement if possible
	•	P2 — nice to have; can defer with sign-off

⸻

Command Appendix

# Build MSIX (example)
pwsh installers/wallet/windows/msix/MakeMSIX.ps1 `
  -LayoutDir dist\windows\stable\msix-layout `
  -OutputMsix dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix `
  -PfxPath $env:CSC_PFX_PATH -PfxPassword $env:CSC_PFX_PASSWORD -VerifyAfter

# Sign any remaining EXE/DLLs (NSIS or portable)
pwsh installers/wallet/windows/codesign.ps1 -Path dist\windows\stable -Recurse -VerifyAfter

# Validate winget manifest locally
winget validate installers\wallet\windows\winget\manifest.yaml
winget install --manifest installers\wallet\windows\winget\manifest.yaml --silent

