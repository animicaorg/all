# Animica Installers — Signing & Keys (CI)

This document lists **which certificates/keys we use**, **what each one is for**, and **where they live in CI**. It also shows how our build workflows materialize keys at runtime and which helper scripts to call.

---

## 1) What we sign and with what

### macOS (Wallet & Explorer)
- **Developer ID Application** certificate (`.p12`)
  - Purpose: codesign app binaries (`codesign`).
- **Developer ID Installer** certificate (`.p12`)
  - Purpose: sign `.pkg` / notarized `.dmg` when needed.
- **Apple Notarization** (App Store Connect API Key, `.p8`)
  - Purpose: notarize the signed app; used by `xcrun notarytool`.
  - Triple: **Issuer ID**, **Key ID**, **Private key (.p8)**.
- **Sparkle v2 Update Key (Ed25519)**
  - Purpose: sign update artifacts (DMGs/PKGs) for Sparkle appcast `<enclosure sparkle:edSignature=…>`.
  - Pair: `ed25519_public.pem` (bundled with the app) + **private key** (kept secret).

### Windows (Wallet & Explorer)
- **Code Signing certificate** (`.pfx`) — Standard or EV
  - Purpose: sign MSIX (and optionally NSIS) via `signtool`.
  - Also use a timestamp server (e.g. `http://timestamp.digicert.com`).

### Linux
- **DEB/RPM GPG keys (optional if distributing via repos)**
  - Purpose: sign repository metadata / packages (`dpkg-sig`, `rpmsign`).
- **Flatpak**: signing handled by the target remote (we do not store its private key).
- **AppImage**: signing optional; not required in our current flow.

---

## 2) Where the secrets live in CI

We use **GitHub Actions** with **environments** and **protected secrets**. Long-lived private keys may be stored in a vault (e.g., 1Password/HashiCorp Vault) and fetched via OIDC; otherwise we store **base64-encoded** blobs in repository secrets.

**Secrets naming (convention):**

### macOS
- `APPLE_DEV_ID_APP_P12_BASE64` — base64 of Developer ID Application `.p12`
- `APPLE_DEV_ID_INSTALLER_P12_BASE64` — base64 of Developer ID Installer `.p12`
- `APPLE_P12_PASSWORD` — password for both `.p12` files (or split as needed)
- `APPLE_NOTARY_ISSUER_ID` — App Store Connect Issuer ID
- `APPLE_NOTARY_KEY_ID` — App Store Connect Key ID
- `APPLE_NOTARY_PRIVATE_KEY_P8_BASE64` — base64 of `.p8`
- `SPARKLE_ED25519_PRIV_PEM_ENC_BASE64` — base64 of **encrypted** `ed25519_private.pem.enc`
- `SPARKLE_PRIV_PASSPHRASE` — passphrase to decrypt PEM for signing (used by update/sign scripts)

### Windows
- `WIN_CODESIGN_PFX_BASE64` — base64 of code signing `.pfx`
- `WIN_CODESIGN_PFX_PASSWORD`
- `WIN_TIMESTAMP_URL` — e.g., `http://timestamp.digicert.com` (optional override)

### Linux (optional)
- `DEB_GPG_PRIVATE_KEY_ASC_BASE64` + `DEB_GPG_PASSPHRASE`
- `RPM_GPG_PRIVATE_KEY_ASC_BASE64` + `RPM_GPG_PASSPHRASE`

> **Principle of least privilege:** Secrets are scoped to **environment: release** and require approvals.

---

## 3) How CI reconstructs keys & imports them

### macOS keychain (ephemeral)  
Scripts you’ll use:
- `installers/scripts/setup_keychain_macos.sh`
- `installers/scripts/import_p12_macos.sh`
- `installers/wallet/macos/sign_and_notarize.sh`
- `installers/updates/scripts/sign_appcast_macos.sh`
- `installers/updates/scripts/update_appcast.py`

**Example GH Actions steps:**
```yaml
- name: Set up ephemeral keychain
  run: |
    bash installers/scripts/setup_keychain_macos.sh

- name: Import Apple Developer ID certs
  env:
    APPLE_DEV_ID_APP_P12_BASE64: ${{ secrets.APPLE_DEV_ID_APP_P12_BASE64 }}
    APPLE_DEV_ID_INSTALLER_P12_BASE64: ${{ secrets.APPLE_DEV_ID_INSTALLER_P12_BASE64 }}
    APPLE_P12_PASSWORD: ${{ secrets.APPLE_P12_PASSWORD }}
  run: |
    echo "$APPLE_DEV_ID_APP_P12_BASE64" | base64 --decode > app.p12
    echo "$APPLE_DEV_ID_INSTALLER_P12_BASE64" | base64 --decode > installer.p12
    bash installers/scripts/import_p12_macos.sh app.p12 "$APPLE_P12_PASSWORD"
    bash installers/scripts/import_p12_macos.sh installer.p12 "$APPLE_P12_PASSWORD"

- name: Write notarytool JSON (App Store Connect API key)
  env:
    APPLE_NOTARY_ISSUER_ID: ${{ secrets.APPLE_NOTARY_ISSUER_ID }}
    APPLE_NOTARY_KEY_ID: ${{ secrets.APPLE_NOTARY_KEY_ID }}
    APPLE_NOTARY_PRIVATE_KEY_P8_BASE64: ${{ secrets.APPLE_NOTARY_PRIVATE_KEY_P8_BASE64 }}
  run: |
    mkdir -p installers/wallet/macos
    echo "$APPLE_NOTARY_PRIVATE_KEY_P8_BASE64" | base64 --decode > key.p8
    cat > installers/wallet/macos/notarytool.json <<JSON
    { "issuer": "$APPLE_NOTARY_ISSUER_ID", "key-id": "$APPLE_NOTARY_KEY_ID", "key": "$(cat key.p8 | sed 's/$/\\n/' | tr -d '\n')" }
    JSON
    rm -f key.p8

- name: Decrypt Sparkle Ed25519 private key
  env:
    SPARKLE_ED25519_PRIV_PEM_ENC_BASE64: ${{ secrets.SPARKLE_ED25519_PRIV_PEM_ENC_BASE64 }}
    SPARKLE_PRIV_PASSPHRASE: ${{ secrets.SPARKLE_PRIV_PASSPHRASE }}
  run: |
    mkdir -p installers/wallet/macos/sparkle
    echo "$SPARKLE_ED25519_PRIV_PEM_ENC_BASE64" | base64 --decode > installers/wallet/macos/sparkle/ed25519_private.pem.enc
    # Decrypt however you encrypted it originally; for example using openssl enc:
    openssl enc -aes-256-cbc -md sha256 -d -in installers/wallet/macos/sparkle/ed25519_private.pem.enc \
      -out installers/wallet/macos/sparkle/ed25519_private.pem -pass env:SPARKLE_PRIV_PASSPHRASE

Windows certificate import

Scripts you’ll use:
	•	installers/scripts/import_pfx_windows.ps1
	•	installers/wallet/windows/codesign.ps1

Example GH Actions (windows-latest):

- name: Import code signing cert
  shell: pwsh
  env:
    WIN_CODESIGN_PFX_BASE64: ${{ secrets.WIN_CODESIGN_PFX_BASE64 }}
    WIN_CODESIGN_PFX_PASSWORD: ${{ secrets.WIN_CODESIGN_PFX_PASSWORD }}
  run: |
    [IO.File]::WriteAllBytes("codesign.pfx", [Convert]::FromBase64String($env:WIN_CODESIGN_PFX_BASE64))
    ./installers/scripts/import_pfx_windows.ps1 -PfxPath codesign.pfx -Password $env:WIN_CODESIGN_PFX_PASSWORD

Linux signing (optional)

If publishing signed repos:
	•	Import GPG keys using gpg --batch --import.
	•	Configure dpkg-sig / rpmsign in your packaging steps.

⸻

4) Where public material lives in the repo
	•	Sparkle public key (wallet): installers/wallet/macos/sparkle/ed25519_public.pem
— baked into the app; used by Sparkle to verify update signatures.
	•	Encrypted Sparkle private key: installers/wallet/macos/sparkle/ed25519_private.pem.enc
— encrypted blob, never usable without the CI passphrase.
	•	Appcast templates:
	•	Wallet: installers/updates/wallet/{stable,beta}/appcast.xml
	•	Explorer: installers/updates/explorer/{stable,beta}/appcast.xml

⸻

5) Build & sign flow (high level)

macOS:
	1.	Codesign app with Developer ID Application cert.
	2.	Create DMG/PKG; sign with Developer ID Installer if applicable.
	3.	Notarize via xcrun notarytool using installers/wallet/macos/notarytool.json.
	4.	Staple ticket (xcrun stapler staple).
	5.	Sign update (Ed25519) using:
	•	installers/updates/scripts/sign_appcast_macos.sh to get sparkle:edSignature
	•	OR update the feed automatically with installers/updates/scripts/update_appcast.py.

Windows:
	1.	Build MSIX/NSIS.
	2.	Sign with signtool using imported .pfx + timestamp.

Linux: package and (optionally) sign repos.

⸻

6) Rotation & revocation
	•	Apple certificates: rotate before expiry; update CI secrets; verify codesign -dv output; keep overlap window.
	•	Sparkle Ed25519: rotation breaks auto-update unless the app embeds the new public key. Plan a bridge release or ship both keys (Sparkle supports multiple keys).
	•	Windows PFX: EV tokens typically require hardware — we use a standard org cert in CI; EV signing is done offline if needed.

⸻

7) Verification commands
	•	macOS signatures:

bash installers/scripts/verify_signatures.sh path/to/App.app path/to/Installer.pkg


	•	Sparkle signature (manual):

base64 -d <sig.txt> | wc -c  # should be 64 bytes after base64 decode


	•	Windows:

Get-AuthenticodeSignature .\Animica-Wallet.msix



⸻

8) Access control & audit
	•	Secrets scoped to release environment with required reviewers.
	•	CI logs never print key material (use ::add-mask:: for any accidental echoes).
	•	Periodically validate notarization & signatures using the verification script.

⸻

9) Related scripts & docs
	•	installers/scripts/common_env.sh — shared env & sanity checks
	•	installers/wallet/macos/sign_and_notarize.sh — macOS sign→notarize→staple
	•	installers/updates/scripts/sign_appcast_macos.sh — Ed25519 signing
	•	installers/updates/scripts/update_appcast.py — appcast injection
	•	installers/scripts/verify_signatures.sh — verify outputs
	•	installers/scripts/store_signing_secret_ci.md — guidance on storing CI secrets

⸻

Questions? Ping #release-engineering.
