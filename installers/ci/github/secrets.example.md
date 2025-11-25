# CI Secrets & Permissions (Examples)

This file documents **which secrets and repository permissions** are required by our GitHub Actions CI pipelines under `installers/ci/github/*`. Use it as a checklist when bootstrapping a new repo or rotating credentials.

> **Principles**
> - Keep certs out of the repo. Provide **base64-encoded** blobs via GitHub **_Environment_** or **_Repository_** secrets.
> - Grant the **least privileges** needed per workflow.
> - Prefer **App Store Connect API keys** over Apple ID passwords for notarization.
> - Prefer **hardware-backed** or organization-managed code-signing certs where possible.

---

## 1) Required Secrets by Workflow

### Explorer — macOS (`explorer-macos.yml`)
**Purpose:** Build Tauri app for macOS, codesign, notarize, staple, publish.

Secrets (Environment: `release`):
- `MACOS_CERT_P12_BASE64` — base64-encoded Apple Developer **Developer ID Application** `.p12`.
- `MACOS_CERT_PASSWORD` — password for the `.p12`.
- **App Store Connect API (notarytool)**:
  - `AC_API_ISSUER_ID` — Issuer UUID.
  - `AC_API_KEY_ID` — Key ID.
  - `AC_API_PRIVATE_KEY_BASE64` — base64 of the `.p8` contents.
  - _Alternative (not recommended):_ `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_SPECIFIC_PASSWORD`.
- **Sparkle appcast signing (optional if updating feeds in CI):**
  - `SPARKLE_ED25519_PRIV_PEM_PASSPHRASE` — passphrase to decrypt `ed25519_private.pem.enc` in repo **or**
  - `SPARKLE_ED25519_PRIV_PEM_BASE64` — base64 private key provided directly via secret.

Permissions:
- `contents: write` (create releases / upload assets).

---

### Explorer — Windows (`explorer-windows.yml`)
**Purpose:** Build Tauri app for Windows, code sign (MSI/EXE/MSIX), publish.

Secrets:
- `WINDOWS_CERT_PFX_BASE64` — base64-encoded **Code Signing** `.pfx`.
- `WINDOWS_CERT_PASSWORD` — password for the `.pfx`.
- `WINDOWS_CERT_SUBJECT` — (optional) exact subject (e.g., `CN=Animica, O=Animica Inc, C=US`) to pick cert if multiple installed.

Optional repo file (already present): `installers/signing/windows/timestamp_urls.txt`  
_No secret required_; edit URLs if needed.

Permissions:
- `contents: write`.

---

### Explorer — Linux (`explorer-linux.yml`)
**Purpose:** Build AppImage / DEB / RPM bundles, publish.

Secrets: _none required_ by default.

**Optional (repo signing):**
- `LINUX_REPO_GPG_PRIVATE_KEY_BASE64` — base64 of ASCII-armored GPG private key.
- `LINUX_REPO_GPG_PASSPHRASE` — passphrase for the key.

Permissions:
- `contents: write`.

---

### Wallet — macOS (`wallet-macos.yml`)
**Purpose:** Build Flutter app for macOS, codesign, notarize, DMG, appcast.

Secrets:
- `MACOS_CERT_P12_BASE64`
- `MACOS_CERT_PASSWORD`
- `AC_API_ISSUER_ID`, `AC_API_KEY_ID`, `AC_API_PRIVATE_KEY_BASE64`
- **Sparkle (if appcast signed in CI):**
  - `SPARKLE_ED25519_PRIV_PEM_PASSPHRASE` **or** `SPARKLE_ED25519_PRIV_PEM_BASE64`

Permissions:
- `contents: write`.

---

### Wallet — Windows (`wallet-windows.yml`)
**Purpose:** Build Flutter for Windows, MSIX/NSIS, codesign, publish.

Secrets:
- `WINDOWS_CERT_PFX_BASE64`
- `WINDOWS_CERT_PASSWORD`
- `WINDOWS_CERT_SUBJECT` (optional)

Permissions:
- `contents: write`.

---

### Wallet — Linux (`wallet-linux.yml`)
**Purpose:** Build AppImage / Flatpak / DEB / RPM.

Secrets: _none_ unless signing repositories:
- `LINUX_REPO_GPG_PRIVATE_KEY_BASE64`
- `LINUX_REPO_GPG_PASSPHRASE`

Permissions:
- `contents: write`.

---

### Appcast & Update Signing (if run in CI)
Scripts under `installers/updates/scripts/`:
- `update_appcast.py` — no secrets; reads files/hashes.
- `sign_appcast_macos.sh` — requires Sparkle key:
  - `SPARKLE_ED25519_PRIV_PEM_PASSPHRASE` (to decrypt `ed25519_private.pem.enc`) **or**
  - `SPARKLE_ED25519_PRIV_PEM_BASE64` (direct key content).

**Never** commit the raw private key.

---

## 2) Creating Base64 Blobs

**macOS Developer ID (P12):**
```bash
# Export from Keychain Access as .p12 (with password), then:
base64 -w0 DeveloperID.p12 > macos.p12.b64
# Upload content of macos.p12.b64 into secret MACOS_CERT_P12_BASE64

App Store Connect private key (.p8):

base64 -w0 AuthKey_ABC123.p8 > ac_api_key.p8.b64

Windows Code Signing (PFX):

# PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("codesign.pfx")) | Set-Content pfx.b64

GPG (Linux repo):

# export secret key (ASCII armored) then base64
gpg --export-secret-keys --armor KEYID > repo.key.asc
base64 -w0 repo.key.asc > repo.key.asc.b64


⸻

3) GitHub Permissions & Environments

Repository → Settings → Actions → General
	•	Workflow permissions: Read and write permissions.
	•	Allow GitHub Actions to create and upload releases.

Environments
	•	Create an environment named release:
	•	Add the secrets listed above.
	•	Optionally require manual approval / branch protections.

Job-level permissions (already set in workflows):

permissions:
  contents: write

OIDC / Cloud KMS (optional)
If you fetch secrets from a cloud secret manager using OIDC, add:

permissions:
  id-token: write
  contents: read

And configure the provider’s trust relationship accordingly.

⸻

4) Rotation & Revocation
	•	macOS: Revoke compromised App Store Connect keys in App Store Connect; rotate AC_API_* and replace P12 if needed.
	•	Windows: Reissue new EV/OV Code Signing cert; update WINDOWS_CERT_PFX_BASE64/WINDOWS_CERT_PASSWORD.
	•	Sparkle: Rotate Ed25519 keys; publish new public key in app; sign feeds with new private key; overlap keys during transition.
	•	GPG: Publish updated repo metadata with new signing key; maintain transitional trust if possible.

See installers/signing/policies.md for detailed procedures.

⸻

5) Secret Naming Summary

Secret	Used By	Notes
MACOS_CERT_P12_BASE64	macOS workflows	Base64 Developer ID .p12
MACOS_CERT_PASSWORD	macOS workflows	Password for P12
AC_API_ISSUER_ID	macOS workflows	Notarization (notarytool)
AC_API_KEY_ID	macOS workflows	Notarization (notarytool)
AC_API_PRIVATE_KEY_BASE64	macOS workflows	Base64 of .p8 key
WINDOWS_CERT_PFX_BASE64	Windows workflows	Base64 Code Signing .pfx
WINDOWS_CERT_PASSWORD	Windows workflows	Password for PFX
WINDOWS_CERT_SUBJECT	Windows (optional)	Explicit subject selector
SPARKLE_ED25519_PRIV_PEM_PASSPHRASE	macOS update signing	Decrypts ed25519_private.pem.enc
SPARKLE_ED25519_PRIV_PEM_BASE64	macOS update signing	Direct key (alternative)
LINUX_REPO_GPG_PRIVATE_KEY_BASE64	Linux repo signing	Optional; ASCII-armored base64
LINUX_REPO_GPG_PASSPHRASE	Linux repo signing	Optional


⸻

6) CI Safety Tips
	•	Never echo secrets to logs. Use actions/github-script or files in $RUNNER_TEMP.
	•	Remove imported cert files immediately after use.
	•	For Windows signing, verify with signtool verify /pa /v.
	•	For macOS, verify with spctl -a -vv and xcrun stapler validate.
	•	Keep team id, issuer, key id in tracked files under installers/signing/* (already present), but not private keys.

⸻

7) Troubleshooting
	•	Invalid notarization credentials — ensure AC_API_* belong to the correct Apple Team ID (see installers/signing/macos/team_id.txt).
	•	“No certificates found” — P12 import failed; password wrong; check Keychain import step.
	•	Timestamp failures — primary TSA down; maintain multiple URLs in installers/signing/windows/timestamp_urls.txt.
	•	Sparkle signature mismatch — ensure app uses the public key matching the private key used in CI.

⸻

This is a living document. Update it when workflows or signing models change.
