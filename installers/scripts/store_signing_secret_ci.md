# Where to put CI signing secrets (and how to keep them least-privileged)

This note explains **where to store** the credentials used by our installer/signing scripts and how to configure CI so that secrets are **minimized, scoped, and auditable**.

The scripts that consume these secrets (already in this repo):

- `installers/scripts/setup_keychain_macos.sh` – creates an ephemeral macOS keychain in CI  
- `installers/scripts/import_p12_macos.sh` – imports a Developer ID `.p12` into that keychain  
- `installers/scripts/import_pfx_windows.ps1` – imports a Windows Code Signing `.pfx`  
- `installers/scripts/verify_signatures.sh` – verifies DMG/PKG/MSIX signatures (and notarization)

---

## TL;DR (short version)

- **Never commit** keys/certs; **never** put them in artifacts or caches.  
- Use your CI platform’s **encrypted secrets store** or, ideally, **OIDC ↔ KMS** to sign via a cloud HSM (no private key leaves KMS).  
- Gate all “release/sign” jobs with **protected branches/tags + required reviewers**.  
- Prefer **App Store Connect API keys** for notarization; avoid AppleID passwords.  
- Rotate secrets regularly; scope them to **release environments** only.

---

## What secrets we need (by platform)

### macOS (DMG/PKG + notarization)
Required for signing:
- `MACOS_SIGNING_CERT_BASE64` — base64 of a **Developer ID Application/Installer** PKCS#12 (`.p12`)
- `MACOS_SIGNING_CERT_PASSWORD` — password for the `.p12`
- `MACOS_CERT_CHAIN_BASE64` — (optional) base64 of PEM chain (intermediate/root)

Our scripts will put these into an **ephemeral keychain** and grant non-interactive access to `codesign`, `productsign`, etc.

For notarization (recommended):
- `ASC_API_KEY_P8_BASE64` — base64 of your App Store Connect API key (`.p8`)
- `ASC_KEY_ID` — key ID
- `ASC_ISSUER_ID` — issuer ID

> Why API key? It supports **least privilege**, and you can scope roles to just notarization. Avoid AppleID username/app-password flows.

### Windows (MSIX)
- `PFX_BASE64` — base64 of **Code Signing** certificate `.pfx`
- `PFX_PASSWORD` — password for the `.pfx`
- Optional: `IMPORT_CHAIN_BASE64` for intermediate/root certs

> For defense-in-depth: consider using an **EV Code Signing** cert in a hardware token/HSM and sign on a **self-hosted runner** with physical access. If not possible, gate cloud runners tightly (see below).

---

## Where to store these secrets

> Examples use **GitHub Actions**; the same ideas apply to GitLab, CircleCI, Azure Pipelines.

### 1) GitHub Actions — Environments (recommended)
Store secrets in **Environment**-scoped secrets (e.g., `release` environment):
- Settings → Environments → `release` → **Secrets**
- Add:
  - `MACOS_SIGNING_CERT_BASE64`, `MACOS_SIGNING_CERT_PASSWORD`, `MACOS_CERT_CHAIN_BASE64`
  - `ASC_API_KEY_P8_BASE64`, `ASC_KEY_ID`, `ASC_ISSUER_ID`
  - `PFX_BASE64`, `PFX_PASSWORD`
- Enable **Required reviewers** and restrict **deployment branches/tags** (e.g., only `v*` tags).  
- In workflow jobs, target `environment: release`. This ensures secrets are **only** available when the environment gate is passed.

**Least-privilege repo settings:**
- In workflow `permissions`, give only what’s needed:
  ```yaml
  permissions:
    contents: read
    id-token: write   # only if using OIDC → KMS (see below)

	•	Run signing on protected refs only (branch protection + tag protection).

2) GitHub Actions — Org secrets vs Repo secrets
	•	Prefer Environment secrets over global org secrets.
	•	If you must use org secrets, restrict access to specific repos and still gate with an environment in the repo.

3) GitHub Actions — OIDC to Cloud KMS (best security)

Instead of storing private keys in CI:
	•	Use GitHub OIDC to obtain short-lived tokens in CI,
	•	Allow those identities to use Cloud KMS/HSM (AWS KMS, Azure Key Vault, GCP KMS) to sign.
	•	Private keys never leave the HSM; you transmit hashes/blobs for signing.

This requires:
	•	Configure a trust relationship from your cloud KMS to your GitHub OIDC (repo/ref constraints).
	•	Update signing steps to call KMS sign APIs or a signing proxy.

⸻

Where not to store secrets
	•	Not in the repository (even encrypted).
	•	Not in Actions variables (they aren’t secret).
	•	Not in build caches or container layers.
	•	Not printed in logs (our scripts avoid echoing sensitive values).

⸻

How scripts read secrets (reference)

These env vars are consumed by our scripts:

macOS keychain/codesign
	•	MACOS_SIGNING_CERT_BASE64, MACOS_SIGNING_CERT_PASSWORD, MACOS_CERT_CHAIN_BASE64
	•	MACOS_KEYCHAIN_NAME, MACOS_KEYCHAIN_PASSWORD (optional overrides in CI)
	•	APPLE_TEAM_ID (optional verify gate in verify_signatures.sh)

Windows code signing
	•	PFX_BASE64 or PFX_PATH, PFX_PASSWORD
	•	IMPORT_CHAIN_BASE64 (optional)
	•	WIN_CERT_ORG, CODE_SIGN_CERT_THUMBPRINT (optional verify gates)

Notarization
	•	ASC_API_KEY_P8_BASE64, ASC_KEY_ID, ASC_ISSUER_ID

⸻

Example: GitHub Actions snippet

name: Release
on:
  push:
    tags: [ 'v*' ]

permissions:
  contents: read

jobs:
  sign-and-notarize:
    environment: release
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4

      - name: Setup ephemeral keychain
        run: installers/scripts/setup_keychain_macos.sh setup
        env:
          MACOS_SIGNING_CERT_BASE64: ${{ secrets.MACOS_SIGNING_CERT_BASE64 }}
          MACOS_SIGNING_CERT_PASSWORD: ${{ secrets.MACOS_SIGNING_CERT_PASSWORD }}
          MACOS_CERT_CHAIN_BASE64: ${{ secrets.MACOS_CERT_CHAIN_BASE64 }}

      - name: Import p12 (idempotent)
        run: installers/scripts/import_p12_macos.sh
        env:
          MACOS_SIGNING_CERT_BASE64: ${{ secrets.MACOS_SIGNING_CERT_BASE64 }}
          MACOS_SIGNING_CERT_PASSWORD: ${{ secrets.MACOS_SIGNING_CERT_PASSWORD }}

      # ... build, codesign, productsign, notarize ...

      - name: Verify artifacts
        run: installers/scripts/verify_signatures.sh dist/*.dmg dist/*.pkg
        env:
          APPLE_TEAM_ID: ABCDE12345

Windows (MSIX) example:

  sign-windows:
    environment: release
    runs-on: windows-2022
    steps:
      - uses: actions/checkout@v4
      - name: Import PFX
        shell: pwsh
        run: installers/scripts/import_pfx_windows.ps1
        env:
          PFX_BASE64: ${{ secrets.PFX_BASE64 }}
          PFX_PASSWORD: ${{ secrets.PFX_PASSWORD }}

      # signtool here (not shown), then:

      - name: Verify MSIX
        shell: bash
        run: installers/scripts/verify_signatures.sh dist/*.msix
        env:
          WIN_CERT_ORG: "Animica Labs"


⸻

Least-privilege checklist
	1.	Environment-scoped secrets with required reviewers and protected refs.
	2.	Separate staging vs release environments (different certs/keys).
	3.	Restrict workflow triggers (only tags or release branches).
	4.	Set job-level permissions minimal.
	5.	Mask secrets in logs (Actions does this; don’t echo the values).
	6.	Use ephemeral keychains (macOS) and cleanup after jobs.
	7.	Prefer HSM/KMS with OIDC where feasible.
	8.	Rotate cert passwords/API keys; set calendar reminders.
	9.	Keep chain/intermediate up-to-date; verify team ID/org at verify time.
	10.	Store certs in separate secrets from their passwords (two-person rule when possible).

⸻

Rotation & auditing
	•	Track expiration of Developer ID / Code Signing certs; renew early.
	•	Rotate ASC_API_KEY regularly; revoke unused keys.
	•	Use CI audit logs to track who deployed to the release environment and when.
	•	Periodically run verify_signatures.sh against published artifacts.

⸻

Local vs CI
	•	Local dev should sign with throwaway test certs, never with production keys.
	•	Production signing must occur only in gated CI or on tightly controlled build hosts.

⸻

Appendix: creating base64 blobs (one-liners)

# Convert p12 → base64
base64 -w0 DeveloperID.p12 > p12.b64

# Convert PEM chain → base64
cat chain.pem | base64 -w0 > chain.b64

# Convert App Store Connect API key (.p8) → base64
base64 -w0 AuthKey_ABC123XYZ.p8 > asc_key.b64

On macOS, -w0 may be -b 0 (BSD base64): base64 -b 0 file > out.b64.

⸻

If anything here conflicts with company policy, follow the stricter rule and update this doc accordingly.
