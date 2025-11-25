# Animica Wallet — WinGet submission flow & IDs

This doc shows how to publish **Animica Wallet** to the Windows Package Manager (**WinGet**), what IDs matter, and how to validate/test locally before opening a PR to the community manifest repo.

---

## 0) Prereqs

- **MSIX build** already packed and code-signed  
  Use: `installers/wallet/windows/msix/MakeMSIX.ps1`
- Windows 10/11 SDK (for `signtool`, `MakeAppx`)
- WinGet tooling:
  - `winget` (App Installer)
  - `wingetcreate` (helps author/submit manifests) — https://github.com/microsoft/winget-create
  - `winget validate` (part of winget)

> TIP: The **Publisher** in `AppxManifest.xml` **must match** the subject on your signing cert. The **PackageFamilyName** (PFN) is derived from Identity **Name** + **Publisher**.

---

## 1) Gather required IDs & hashes

### A) Compute file hash (required)
```powershell
certutil -hashfile .\dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix SHA256
# copy the hex digest (no spaces) → InstallerSha256

B) Get Package Family Name (PFN)

Option 1 (fastest):

(Get-AppPackage -Path .\dist\windows\stable\Animica-Wallet_1.0.0.0_x64.msix).PackageFamilyName

Option 2 (manual):
	•	Unpack or open AppxManifest.xml
	•	Read <Identity Name="…" Publisher="…">
	•	PFN is derived from those two; Get-AppPackage is the reliable method.

C) Choose the PackageIdentifier
	•	Convention: Publisher.Product
	•	Use: AnimicaLabs.AnimicaWallet
	•	For pre-release/beta channels, use a separate identifier to avoid upgrading stable users:
	•	AnimicaLabs.AnimicaWallet.Beta

⸻

2) Manifests: local vs community repo

There are two ways to structure manifests:

A) Singleton (simple, good for internal feeds or testing)

A single file like manifest.yaml (we ship a template here).
Update:
	•	PackageVersion
	•	Installers[0].InstallerUrl
	•	Installers[0].InstallerSha256
	•	Installers[0].PackageFamilyName

Validate locally:

winget validate installers\wallet\windows\winget\manifest.yaml
winget install --manifest installers\wallet\windows\winget\manifest.yaml --silent

B) Triplet (required for the winget-pkgs community repo)

Three files per version:

manifests/
  A/AnimicaLabs/AnimicaWallet/1.0.0/
    AnimicaLabs.AnimicaWallet.installer.yaml
    AnimicaLabs.AnimicaWallet.locale.en-US.yaml
    AnimicaLabs.AnimicaWallet.yaml

	•	.yaml (version): PackageIdentifier, PackageVersion, metadata
	•	.locale.en-US.yaml: descriptions, URLs, tags
	•	.installer.yaml: installers array (URL, SHA256, PFN, type=msix, arch)

You can author these with wingetcreate:

wingetcreate new AnimicaLabs.AnimicaWallet `
  --version 1.0.0 `
  --installerUrl https://downloads.animica.dev/wallet/stable/Animica-Wallet_1.0.0.0_x64.msix `
  --installerSha256 <HEX> `
  --packageName "Animica Wallet" `
  --publisher "Animica Labs, Inc." `
  --license "Proprietary" `
  --moniker animica-wallet `
  --shortDescription "Post-quantum wallet for the Animica network." `
  --packageUrl https://animica.dev/wallet `
  --installerType msix `
  --architecture x64

Then open/adjust fields (add PackageFamilyName, set MinimumOSVersion, etc.) and validate:

winget validate .\manifests\A\AnimicaLabs\AnimicaWallet\1.0.0


⸻

3) Submit to the community repository (winget-pkgs)

Option 1 — With wingetcreate (recommended)

# After `wingetcreate new`, run:
wingetcreate submit .\manifests\A\AnimicaLabs\AnimicaWallet\1.0.0 `
  --token <GITHUB_TOKEN_WITH_REPO_SCOPE>

This opens a PR in https://github.com/microsoft/winget-pkgs with automated checks.

Option 2 — Manual PR
	1.	Fork winget-pkgs
	2.	Create the path: manifests/A/AnimicaLabs/AnimicaWallet/1.0.0/
	3.	Add the triplet manifests
	4.	winget validate locally
	5.	Commit & open PR to microsoft/winget-pkgs

⸻

4) Test installation & upgrades

Local test (no PR required):

winget install --manifest .\manifests\A\AnimicaLabs\AnimicaWallet\1.0.0 --silent

After the PR merges and indexing finishes:

winget search Animica Wallet
winget show AnimicaLabs.AnimicaWallet
winget install AnimicaLabs.AnimicaWallet

For a new release, create a new folder …/1.0.1/ with updated installer URL & SHA256.
WinGet will then offer upgrades:

winget upgrade AnimicaLabs.AnimicaWallet


⸻

5) Versioning & channels
	•	Stable: AnimicaLabs.AnimicaWallet
	•	Beta/Insiders: prefer a separate identifier: AnimicaLabs.AnimicaWallet.Beta
	•	Prevents beta from replacing stable unexpectedly
	•	Same instructions apply; publish to winget-pkgs under the beta identifier

⸻

6) Common pitfalls
	•	SHA mismatch: Recompute after any re-signing:

certutil -hashfile <msix> SHA256


	•	PFN wrong/missing: Use Get-AppPackage -Path. Don’t guess.
	•	Publisher mismatch: The MSIX Publisher must match the cert subject used for signing.
	•	Unsigned or no timestamp: WinGet may install, but SmartScreen/enterprise policy can block. Always timestamp.

⸻

7) Quick checklist (stable)
	1.	Build & sign MSIX → Animica-Wallet_1.0.0.0_x64.msix
	2.	certutil -hashfile → update InstallerSha256
	3.	(Get-AppPackage -Path …).PackageFamilyName → update PFN
	4.	Author triplet manifests (or use wingetcreate new)
	5.	winget validate locally
	6.	Submit PR (wingetcreate submit or manual)

⸻

References
	•	WinGet manifest schema: https://learn.microsoft.com/windows/package-manager/package/manifest
	•	Community repo: https://github.com/microsoft/winget-pkgs
	•	wingetcreate: https://github.com/microsoft/winget-create

