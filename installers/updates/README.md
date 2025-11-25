# Updates & Release Hosting

This directory documents how we publish **signed, channelized updates** for Animica desktop apps (Wallet, Explorer). It covers hosting layouts, signing/appcast steps, and CI guidance across macOS, Windows, and Linux.

---

## What lives here

- **Channels**: `stable`, `beta`, `dev` — each has its own feed/endpoint.
- **Hosting**: GitHub Releases (public), or S3+CloudFront (private/CDN). Either works as long as feeds & artifacts are HTTPS.
- **Signatures**: 
  - macOS: Sparkle **Ed25519** signatures over update items (DMG/PKG) + Apple codesigning + notarization.
  - Windows: MSIX packages **codesigned** (the package itself is the update payload). Winget manifests optionally.
  - Linux: AppImage `.zsync` delta channel (optional **GPG** detached sigs), Flatpak (Flathub or custom repo), DEB/RPM (signed repos).

> Security model: **App binaries must be platform-signed** (Apple/Windows), and **feeds must be integrity-protected** (Sparkle Ed25519 for macOS; repo GPG keys for Linux; MSIX Authenticode for Windows). Serve feeds/artifacts via HTTPS with caching but immutable URLs.

---

## Channels & Versioning

- **stable**: GA for end users.
- **beta**: pre-release feature validation; smaller audience.
- **dev**: cutting-edge builds; breakers allowed.

**SemVer** per app (e.g., `1.4.2`). Pre-releases carry suffixes: `1.5.0-beta.2`.  
Each channel points to its own feed URL and only lists versions intended for that channel.

Recommended tags:
- stable: `v1.4.2`
- beta: `v1.5.0-beta.2`
- dev: `v1.6.0-dev.20251008`

---

## Hosting Layouts

### Option A — GitHub Releases
- Upload artifacts to a release per tag.
- Publish per-channel feed files (e.g., `appcast.xml`) as release assets **or** use a small GitHub Pages site that points to Releases.
- Pros: simple, good CDN, public.
- Cons: private channels require extra setup.

### Option B — S3 + CloudFront (recommended for private/beta)

s3://updates.animica.dev/
wallet/
stable/appcast.xml
stable/Wallet-1.4.2.dmg
stable/Wallet-1.4.2.dmg.sig
beta/appcast.xml
…
explorer/
stable/appcast.xml
stable/Animica-Explorer-1.0.0-x86_64.AppImage
stable/Animica-Explorer-1.0.0-x86_64.AppImage.zsync

- Serve via CloudFront with TLS, immutable cache for versioned files; shorter TTL for `appcast.xml`.

---

## macOS — Sparkle v2 (Ed25519)

We already keep:
- `installers/wallet/macos/sparkle/ed25519_public.pem`
- `installers/wallet/macos/sparkle/ed25519_private.pem.enc` (encrypted in repo)
- Appcast template: `installers/wallet/macos/sparkle/appcast.xml.tmpl`

**Feed URL** per channel (example):
- `https://updates.animica.dev/wallet/stable/appcast.xml`
- Embed feed URL in the app (`Info.plist` or config) for each channel build.

### Release Steps (CI)
1. **Build & Notarize**  
   Use `installers/wallet/macos/sign_and_notarize.sh` to codesign, `notarytool` submit, and staple.

2. **Sign update item (Sparkle)**  
   Use Sparkle's `generate_appcast` or `sign_update` to compute Ed25519 signature for the DMG/PKG.  
   The **public key** is embedded in the app; CI decrypts the private key (`.pem.enc`) with a CI secret.

3. **Generate appcast**  
   Fill `appcast.xml.tmpl` with:
   - version (`1.4.2`)
   - item URL (HTTPS link to DMG/PKG)
   - signature (`edSignature`)
   - length/sha256
   Then publish as `appcast.xml` to the channel path.

4. **Upload**  
   Upload DMG/PKG and `appcast.xml` to S3/GitHub Releases. Set long cache for DMG/PKG; short cache for `appcast.xml`.

> Sparkle verifies both **codesign/notarization** and **Ed25519** signature before prompting the user.

---

## Windows — MSIX & WinGet

- **MSIX** contains signature; updates can be delivered via an **App Installer** file or by user installing newer MSIX.
- Our pipeline provides:
  - `installers/wallet/windows/msix/Package.appxmanifest`
  - Build script: `installers/wallet/windows/scripts/build_release.ps1`
  - Codesign wrapper: `installers/wallet/windows/codesign.ps1`
  - Winget manifests under `installers/wallet/windows/winget/`

### Update Delivery Options
- **App Installer (.appinstaller)** (optional): host an XML that points to latest MSIX. Windows checks for updates.
- **Winget**: submit updated manifests to `winget-pkgs`. Users get `winget upgrade Animica.Wallet`.
- **Manual**: download newer MSIX from your site.

**CI**:
1. Build MSIX.
2. Sign MSIX (EV cert strongly recommended).
3. (Optional) Generate/host `.appinstaller` per channel.
4. (Optional) Update Winget manifest and submit PR.

---

## Linux — AppImage, Flatpak, DEB, RPM

### AppImage (+ zsync)
- Our AppImage recipe sets update info (`AppImageBuilder.yml`), producing `.AppImage` **and** `.AppImage.zsync`.
- Host both files. AppImageUpdate clients use `.zsync` for delta updates.
- Optional **GPG detached signature** is supported by our script: set `SIGN_APPIMAGE=1`.

### Flatpak
- Prefer **Flathub** for stable; use **separate branches** for beta/dev, or a custom Flatpak repo.
- Manifest: `installers/explorer-desktop/linux/flatpak/io.animica.Explorer.yml`

### DEB & RPM repos
- **DEB**: `reprepro` to publish, sign with repo GPG key.
- **RPM**: `createrepo_c`, sign packages and `repodata/repomd.xml`.
- Channelization via separate repos or distributions (e.g., `stable`, `beta`).

---

## Feeds & Endpoints

### Suggested layout

updates.animica.dev/
wallet/{stable|beta|dev}/appcast.xml
explorer/{stable|beta|dev}/appcast.xml       # optional if you enable Sparkle for explorer-mac
linux/
appimage/{stable|beta|dev}/…
deb/{dists, pool}/…
rpm/{releases, repodata}/…
windows/
msix/{stable|beta|dev}/…
winget/{stable|beta}/manifests/…

Set **channel** via CI env `CHANNEL=stable|beta|dev`. Make the app read the matching feed URL at build time.

---

## CI/CD Flow (outline)

Matrix build per OS/arch:

1. **Build**  
   - macOS: `sign_and_notarize.sh`
   - Windows: `build_release.ps1` + `codesign.ps1`
   - Linux: `build_release.sh` (AppImage/DEB/RPM), Flatpak builder if enabled

2. **Sign Feeds/Artifacts**
   - macOS: Sparkle Ed25519 signatures + notarized DMG/PKG
   - Windows: Authenticode MSIX
   - Linux: optional GPG (.AppImage.asc), repo GPG keys (DEB/RPM)

3. **Generate Feeds**
   - Sparkle `appcast.xml` per channel
   - AppImage `.zsync` already created by builder
   - App Installer XML (optional) for MSIX channels
   - Update apt/yum metadata if publishing repos

4. **Upload**
   - Push to S3/GitHub Releases with proper cache headers
   - (Optional) Invalidate CloudFront for `appcast.xml` / metadata objects

5. **Verify**
   - Run `installers/scripts/verify_signatures.sh`
   - Mac: `spctl -a -vv -t install *.dmg`
   - Win: `signtool verify /pa *.msix`
   - Linux: `sha256sum -c *.sha256`, `gpg --verify` if applicable

### Useful CI env vars

CHANNEL=stable|beta|dev
APP_NAME=wallet|explorer
S3_BUCKET=updates.animica.dev
CLOUDFRONT_DISTRIBUTION_ID=EXXXXXXX
SPARKLE_PRIV_KEY_B64=…              # used to decrypt ed25519_private.pem.enc
GPG_KEY_ID=…                         # for AppImage/repo signing
APPLE_ID=…                           # notarization (macOS)
APPLE_TEAM_ID=…
APPLE_APP_SPECIFIC_PASSWORD=…
WIN_CERT_PFX_B64=…
WIN_CERT_PASSWORD=…

---

## Security Checklist

- ✅ Serve feeds & artifacts over **HTTPS**.
- ✅ **Pin** Sparkle public key in the app; keep private key **encrypted** in CI; rotate with a plan.
- ✅ Notarize macOS builds; enforce **hardened runtime**.
- ✅ Use **EV** code signing on Windows where possible.
- ✅ Sign Linux repos; consider AppImage GPG.
- ✅ Immutable artifact URLs (content-addressed or versioned); short cache for index/feed files.
- ✅ Keep **channel separation** strict: stable must never list beta/dev artifacts.

---

## Local Dry Run

- Bump version (platform-specific bump scripts exist under `installers/*/scripts/`).
- Produce artifacts locally.
- Generate/sign appcast (macOS).
- Host locally (`python -m http.server`) and point the app to `http://127.0.0.1:8000/.../appcast.xml` for manual update testing.

---

## Troubleshooting

- **Sparkle says signature invalid**: confirm you used the **exact DMG/PKG bytes** that match the appcast signature. Redownload after upload to validate.
- **MSIX fails to install**: timestamp/cert chain issues or identity mismatch with existing install. Uninstall older app with different identity.
- **AppImage fails to update**: ensure `.AppImage.zsync` is adjacent and up-to-date; correct `update-information` string in the AppImage.
- **Repo install errors**: GPG or metadata stale — regenerate indices and verify client `sources.list` / `.repo` config.

---

## References

- Sparkle: https://sparkle-project.org/
- AppImage: https://appimage.org/ / https://github.com/AppImage/AppImageUpdate
- Winget: https://learn.microsoft.com/windows/package-manager/
- App Installer (MSIX): https://learn.microsoft.com/windows/msix/app-installer/
- Flathub: https://flathub.org/docs/for-app-authors/submission
- DEB (reprepro): https://mirrorer.org/reprepro/
- RPM (createrepo_c): https://github.com/rpm-software-management/createrepo_c

