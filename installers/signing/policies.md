# Animica Signing Policies — Rotation, Revocation, Short-Lived Tokens

This document defines how we manage **code-signing and update/signature keys** across platforms, how we **rotate** them safely, how we **revoke** them when needed, and how CI obtains **short-lived credentials** at build time.

> Scope: Apple *Developer ID* & *Notarization* (wallet/explorer), **Sparkle Ed25519** update key, **Windows Code Signing** (MSIX/NSIS), and optional **Linux repo GPG** keys.

---

## 0) Principles

- **Least privilege:** separate keys per *product* and *channel* (stable/beta).
- **Short cryptoperiods:** rotate on a schedule or earlier if risk increases.
- **No long-lived secrets in runners:** prefer **OIDC → cloud KMS** or encrypted blobs + ephemeral keychains.
- **Two-person rule:** rotations and revocations require approval by at least two maintainers.
- **Provenance & audit:** record fingerprints, expirations, and change history.

---

## 1) Key Inventory & Cryptoperiods

Maintain `installers/signing/inventory.json` (not committed with private material) tracking:

- `purpose` (macos_app, macos_installer, sparkle, windows_pfx, deb_gpg, rpm_gpg)
- `fingerprint`/`serial`/`issuer`
- `created_at`, `expires_at`
- `channels` (stable, beta)
- `public_url` (where applicable; e.g., GPG pubkey)
- `status` (active, staged, deprecated, revoked)

**Recommended cryptoperiods & rotation windows**

| Key                               | Typical Expiry | Planned Rotation Window          |
|-----------------------------------|----------------|----------------------------------|
| Apple Developer ID (App/Installer)| 5y             | **90 days before** expiry        |
| Apple Notary API key (.p8)        | none (revocable) | **180 days** or on org policy  |
| Sparkle Ed25519 (updates)         | none           | **12–18 months** (bridge release)|
| Windows Code Signing (.pfx)       | 1–3y           | **60 days before** expiry        |
| Linux GPG (repo)                  | 1–2y           | **60 days before** expiry        |

> Sparkle key rotation is special: it requires a **bridge release** embedding the **new public key** before publishing updates signed with the new private key.

---

## 2) Rotation Playbooks

### 2.1 Apple (macOS)

1. **Request/issue new certificates** (Developer ID Application/Installer) in Apple Developer portal.
2. **Import to vault/KMS**, export a **password-protected `.p12`** for CI bootstrap.
3. **Stage in CI** (new secrets alongside old). Update:
   - `APPLE_DEV_ID_APP_P12_BASE64` / `APPLE_DEV_ID_INSTALLER_P12_BASE64`
   - `APPLE_P12_PASSWORD`
4. **Build a canary**: codesign + notarize with the **new** certs in non-public channel; verify with `installers/scripts/verify_signatures.sh`.
5. **Flip production** to new certs; keep old certs available **30 days** for rollback.
6. **Update inventory** with new fingerprints & expiry.

### 2.2 Sparkle Ed25519 (update signatures)

1. **Generate new keypair** (offline). Keep old key secured.
2. **Bridge release**: ship app embedding **both** public keys (Sparkle supports multiple).
3. Publish next updates signed with **new private key**.
4. After **2 minor releases** or **30 days**, remove the old public key from app and **archive old private key** (offline).
5. Update appcast metadata & docs.

### 2.3 Windows Code Signing

1. Obtain new `.pfx` (with strong password). If EV, plan offline signing or token integration.
2. Stage new secrets `WIN_CODESIGN_PFX_BASE64` / `WIN_CODESIGN_PFX_PASSWORD`.
3. Sign a **pre-release** build; verify with `Get-AuthenticodeSignature`.
4. Switch production signing; keep old cert for **timestamped validation** of previous releases.

### 2.4 Linux GPG (optional)

1. Generate new **ed25519 sign-only** key (2y) and **publish public key** at a stable URL.
2. For **APT**: produce metadata signed by **both keys** during a bridge window; then remove old key.
3. For **RPM**: sign RPMs and `repomd.xml` with the new key; keep old key available for historical repos.

---

## 3) Revocation Runbook

When compromise is **suspected** or **confirmed**, follow the matrix:

| Level | Scenario | Actions |
|------:|----------|---------|
| L1    | CI secret leak (no evidence of misuse) | Rotate CI secrets, invalidate runners, re-encrypt blobs, audit last 30 days. |
| L2    | Private key exposure (no release abuse) | **Revoke** affected cert/key, publish advisory, re-sign upcoming releases with replacement; for Sparkle prepare emergency bridge release. |
| L3    | Abuse in the wild (malicious release) | **Immediate revocation** + **takedown**; publish notices on site/app start; notarize & push **hotfix** signed with new key; coordinate with Microsoft/Apple trust programs. |

**Mechanics per key class**

- **Apple**: Revoke certificate in Developer portal; remove from keychain; regenerate notarization API key if suspected.
- **Sparkle**: Rotate appcast to **blocklisted versions**; force minimum version; ship release embedding **only new public key**.
- **Windows**: Contact CA for revocation; re-sign clean builds; ensure **RFC3161 timestamps** on good releases.
- **GPG**: Publish **revocation certificate**; replace repo metadata; communicate fingerprints prominently.

Document incident in `security/advisories/ADV-YYYY-MM-dd.md` (separate private repo if needed).

---

## 4) Short-Lived Tokens & CI Access

Prefer exchanging GitHub Actions **OIDC** → cloud provider to mint **ephemeral credentials** (≤ 1 hour):

- **AWS STS + KMS**: Sign artifacts with KMS keys; restrict to `repo: Animica/*`, `ref: refs/tags/*`.
- **GCP Workload Identity Federation + KMS**: Similar setup; per-environment trust.
- **Azure Federated Credentials + Key Vault**: Grant `sign` permission to release pipeline.

When native platform tools require local key material (e.g., Apple `.p12`):

- Use **ephemeral macOS keychain** (`installers/scripts/setup_keychain_macos.sh`).
- Import `.p12` only for job duration (`installers/scripts/import_p12_macos.sh`).
- **Mask** all passwords in logs; scrub build artifacts after job.

**Token TTL policy**
- Default TTL **≤ 60 minutes**; scope limited to signing tasks.
- No refresh/long-running jobs; split workflows (build → attest → sign).

---

## 5) Approvals, Change Control, and Audit

- **Approvals**: Rotations and revocations require
  - 1 owner from **Security/Release Eng** and
  - 1 owner from **Platform** (wallet/explorer).
- **Change records**: PR with summary, fingerprints, expiry, and links to CI runs.
- **Periodic review**: Quarterly review of inventory & expirations; automated reminders **T-120/T-90/T-30**.

---

## 6) Communications

- **User-visible**: Release notes + website updates for key rotations affecting updates (Sparkle).
- **Developers**: Slack #release-engineering with checklist; incident channel if revocation.
- **Partners**: Notify aggregators/package repos when GPG keys change.

---

## 7) Checklists

### Rotation (generic)
- [ ] New key/cert generated & stored safely
- [ ] CI secrets staged (do not remove old yet)
- [ ] Canary build signed & verified
- [ ] Production switch + monitoring
- [ ] Old key deprecated/archived
- [ ] Inventory updated

### Revocation (generic)
- [ ] Revoke at CA/portal/KMS
- [ ] Remove from CI and keychains
- [ ] Emergency build path validated
- [ ] Advisory drafted and published
- [ ] Post-mortem with action items

---

## 8) Implementation Notes (Repository)

- Scripts referenced:
  - `installers/scripts/setup_keychain_macos.sh`
  - `installers/scripts/import_p12_macos.sh`
  - `installers/wallet/macos/sign_and_notarize.sh`
  - `installers/updates/scripts/sign_appcast_macos.sh`
  - `installers/scripts/verify_signatures.sh`
- Config snippets:
  - `installers/signing/macos/{team_id.txt, issuer.txt, key_id.txt}`
  - `installers/signing/windows/{cert_subject.txt, timestamp_urls.txt}`
  - `installers/signing/linux/gpg/README.md`

---

## 9) Security Hardening

- Separate signing identities per **product** and **channel**.
- Enforce **protected environments** in CI for release jobs.
- Use **hardware tokens** (YubiKey, EV tokens) where practical; otherwise KMS + OIDC.
- Ensure all signed artifacts include **timestamps** (Apple notarization ticket, RFC3161 for Windows).
- Keep **deterministic, reproducible builds** to reduce trust in build hosts.

---

*Owner:* Release Engineering  
*Last updated:* YYYY-MM-DD
