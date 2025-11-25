# Animica Wallet — Linux Packaging Overview
**Targets:** AppImage (primary portable), Flatpak (sandboxed), DEB (Debian/Ubuntu), RPM (Fedora/RHEL).  
**Architectures:** `x86_64` (primary), `aarch64/arm64` (best-effort where toolchain supports).

This document explains how we build, sign, test, and publish Linux packages. It also calls out sandbox & dependency nuances so users get a consistent, secure experience.

---

## 0) Common prerequisites

- **Built app** directory: `build/linux/<arch>/release/AnimicaWallet` (binary) + `resources/` (icons, locales).
- **Versioning:** semantic `MAJOR.MINOR.PATCH`. For DEB/RPM, CI may append distro build metadata.
- **Desktop integration:**
  - `animica-wallet.desktop` (Exec, Name, Icon)  
  - Icons under `icons/hicolor/{16,32,48,64,128,256,512}/apps/animica-wallet.png`
  - Optional AppStream: `io.animica.Wallet.appdata.xml`
- **Licenses/EULA:** ship `LICENSE`, `EULA.txt` (if applicable).
- **Signing keys** (stored in CI):
  - GPG (AppImage detached sigs, APT repo, Flatpak OSTree).
  - RPM macros (`~/.rpmmacros`) and key for `rpmsign`.

Repo layout (suggested):

installers/
wallet/
linux/
appimage/        # builder config, hooks
flatpak/         # manifest + export scripts
deb/             # control templates, postinst/prerm
rpm/             # .spec or fpm config, post scripts
README.md        # (this file)

---

## 1) AppImage (portable, no root)

**Why:** Fastest path to “download & run”, good for power users and CI artifacts.  
**Runtime:** Relies on FUSE (Type 2 AppImage). On some distros you need `libfuse2` (Ubuntu 22.04) or `fuse` compat package.

### Build options
1) **linuxdeploy + appimagetool** (classic)

linuxdeploy –appdir AppDir 
–executable build/linux/x86_64/release/AnimicaWallet 
–desktop-file installers/wallet/linux/appimage/animica-wallet.desktop 
–icon-file installers/wallet/linux/appimage/icons/animica-wallet.png
appimagetool AppDir Animica-Wallet-x86_64.AppImage

2) **appimage-builder** (YAML-driven, captures deps)

appimage-builder –recipe installers/wallet/linux/appimage/AppImageBuilder.yml

**AppDir structure (key bits):**

AppDir/
usr/bin/AnimicaWallet          # main binary
usr/share/applications/animica-wallet.desktop
usr/share/icons/hicolor/…/animica-wallet.png
AppRun                          # optional launcher (or use desktop Exec)
animica-wallet.desktop

### Signing & updates
- **Sign:** `gpg --detach-sign --armor Animica-Wallet-x86_64.AppImage` → publish `.asc`.
- **Delta updates:** generate `.zsync` for AppImageUpdate:

appimageupdate-tool –make-zsync Animica-Wallet-x86_64.AppImage

### QA smoke

chmod +x Animica-Wallet-x86_64.AppImage
./Animica-Wallet-x86_64.AppImage –version
./Animica-Wallet-x86_64.AppImage –help

---

## 2) Flatpak (sandboxed, store-friendly)

**Why:** Strong sandbox (bwrap/portal), consistent runtime across distros.  
**ID:** `io.animica.Wallet`  
**Runtime:** `org.freedesktop.Platform//23.08` (or `org.kde.Platform` if Qt), SDK mirrors for builds.

### Manifest (high-level)
- Place `io.animica.Wallet.yml` in `installers/wallet/linux/flatpak/`
- Minimal permissions:
  - network: **yes**
  - filesystem: `xdg-download` (optional), otherwise none
  - `--device=all` **not** required
  - portals for file pickers, clipboard
- Build:

flatpak-builder –force-clean build-dir installers/wallet/linux/flatpak/io.animica.Wallet.yml
flatpak-builder –user –install –force-clean build-dir installers/wallet/linux/flatpak/io.animica.Wallet.yml
flatpak run io.animica.Wallet

### Repo & signing
- Create local OSTree repo and sign with GPG:

flatpak build-export –gpg-sign= repo build-dir

- Publish `repo/` over HTTPS; users add remote:

flatpak remote-add –if-not-exists animica https://downloads.animica.dev/flatpak/repo
flatpak install animica io.animica.Wallet

---

## 3) DEB (Debian/Ubuntu)

**Install paths**
- Binary: `/opt/AnimicaWallet/AnimicaWallet` **or** `/usr/lib/animica-wallet/AnimicaWallet`
- Desktop file: `/usr/share/applications/animica-wallet.desktop`
- Icons: `/usr/share/icons/hicolor/.../apps/animica-wallet.png`
- Symlink for CLI (optional): `/usr/bin/animica-wallet` → main binary

**Control template (key fields)**

Package: animica-wallet
Version: 1.2.3-1
Section: utils
Priority: optional
Architecture: amd64
Depends: libstdc++6, libgtk-3-0 | libgtk-4-1, libfuse2 | fuse
Maintainer: Animica Labs support@animica.dev
Description: Post-quantum wallet for the Animica network

**Build with fpm (simple)**

fpm -s dir -t deb -n animica-wallet -v 1.2.3 
–prefix /opt/AnimicaWallet 
build/linux/x86_64/release/AnimicaWallet=/opt/AnimicaWallet/AnimicaWallet 
installers/wallet/linux/desktop/animica-wallet.desktop=/usr/share/applications/animica-wallet.desktop 
installers/wallet/linux/icons/=/usr/share/icons/hicolor/

**APT repo**
- Use `reprepro` or `aptly` to host pool & metadata.
- **Sign** Release files (InRelease) with GPG.
- Users add:

echo “deb [signed-by=/usr/share/keyrings/animica.gpg] https://downloads.animica.dev/apt stable main” | sudo tee /etc/apt/sources.list.d/animica.list
sudo curl -fsSL https://downloads.animica.dev/keys/animica.gpg -o /usr/share/keyrings/animica.gpg
sudo apt update && sudo apt install animica-wallet

---

## 4) RPM (Fedora/RHEL/openSUSE)

**Install paths**
- Binary: `/opt/AnimicaWallet/AnimicaWallet` or `/usr/lib64/animica-wallet/AnimicaWallet`
- Desktop/icons same as DEB runes.

**Build with rpmbuild or fpm**
- **Spec file** (recommended) in `installers/wallet/linux/rpm/animica-wallet.spec`, or:

fpm -s dir -t rpm -n animica-wallet -v 1.2.3 –rpm-os linux 
–prefix /opt/AnimicaWallet 
build/linux/x86_64/release/AnimicaWallet=/opt/AnimicaWallet/AnimicaWallet 
installers/wallet/linux/desktop/animica-wallet.desktop=/usr/share/applications/animica-wallet.desktop 
installers/wallet/linux/icons/=/usr/share/icons/hicolor/

**Sign RPMs & repo**

rpmsign –addsign *.rpm
createrepo_c repo/

Users add `.repo`:

sudo tee /etc/yum.repos.d/animica.repo <<EOF
[animica]
name=Animica Wallet
baseurl=https://downloads.animica.dev/rpm/$basearch
enabled=1
gpgcheck=1
gpgkey=https://downloads.animica.dev/keys/animica-rpm.pub
EOF

sudo dnf install animica-wallet

---

## 5) Sandboxing & permissions

| Format    | Sandbox | Notes |
|----------|---------|------|
| AppImage | none    | Full system access of invoking user; simplest; needs FUSE compat. |
| Flatpak  | strong  | Use portals; restrict filesystem; network allowed. |
| DEB/RPM  | none    | Classic install; follow XDG & least privilege; no setuid. |

**Wallet guidance**
- No raw device, no elevated privileges.
- Network only to configured RPC/WS endpoints (HTTPS where available).
- Data under `~/.config/AnimicaWallet` and `~/.local/share/AnimicaWallet` (XDG), or Flatpak’s `$HOME/.var/app/io.animica.Wallet`.

---

## 6) CI skeleton (high-level)

1. Build Linux binaries (`x86_64`, optional `aarch64`).
2. Package:
   - AppImage: linuxdeploy/appimagetool → `.AppImage` + `.zsync` + `.asc`.
   - Flatpak: `flatpak-builder` → export OSTree repo (GPG-signed).
   - DEB/RPM: `fpm` or native builders → sign artifacts; update repo metadata.
3. Publish artifacts & indexes to `downloads.animica.dev`.
4. Smoke tests in containers/VMs (Ubuntu LTS, Fedora stable).
5. Post QA, update channels (“stable”, “beta”) symlinks.

---

## 7) QA quick checklist

- **AppImage**
  - `--version` runs; desktop file launches via double-click
  - `gpg --verify` passes; `.zsync` present
- **Flatpak**
  - Installs from local repo; launches; portal file picker works
  - Permissions reviewed (`flatpak info --show-permissions io.animica.Wallet`)
- **DEB**
  - `apt install` succeeds; desktop entry present; uninstall clean
  - Repository `InRelease` signature valid
- **RPM**
  - `dnf install` succeeds; GPG key installed; uninstall clean
  - Repo metadata current (`createrepo_c` rerun on publish)

---

## 8) Troubleshooting

- **AppImage fails to mount:** install `libfuse2` (Ubuntu 22.04) or enable FUSE; try `--appimage-extract`.
- **Wayland issues:** ensure `XDG_SESSION_TYPE` respected; fall back to X11 if needed.
- **Missing icons/desktop entry:** verify paths and update icon cache:
  - `gtk-update-icon-cache -f /usr/share/icons/hicolor`
  - `update-desktop-database` (DEB) or `update-desktop-database` via RPM post script.
- **Flatpak network blocked:** confirm `--share=network` in manifest and no extra denies.

---

## 9) Security & signing model (Linux)

- **AppImage:** publish detached `*.AppImage.asc` (GPG). Document the fingerprint.
- **Flatpak:** OSTree commits signed; publish GPG public key; pin remote.
- **DEB:** APT repo `InRelease` (clearsigned). Don’t rely on `.deb` inline signatures.
- **RPM:** `rpmsign` each `.rpm` and publish repo GPG key; `gpgcheck=1` required.

**Key hygiene:** keys live in CI secrets; short-lived runners; no dev machines.

---

## 10) Example desktop file (reference)

```ini
[Desktop Entry]
Type=Application
Name=Animica Wallet
Comment=Post-quantum wallet for the Animica network
Exec=AnimicaWallet %U
Icon=animica-wallet
Terminal=false
Categories=Finance;Network;
MimeType=x-scheme-handler/animica;


⸻

11) Distro matrix (support policy)

Distro	Min version	Format
Ubuntu LTS	22.04	AppImage, DEB, Flatpak
Debian stable	current	AppImage, DEB, Flatpak
Fedora	39+	AppImage, RPM, Flatpak
openSUSE Tumble.	current	AppImage, RPM, Flatpak
Arch/Manjaro	rolling	AppImage, Flatpak


⸻

Command appendix

Sign AppImage:

gpg --armor --detach-sign Animica-Wallet-x86_64.AppImage
sha256sum Animica-Wallet-x86_64.AppImage

Flatpak local run:

flatpak-builder --user --install --force-clean build-dir installers/wallet/linux/flatpak/io.animica.Wallet.yml
flatpak run io.animica.Wallet

DEB install (local):

sudo apt install ./animica-wallet_1.2.3_amd64.deb

RPM install (local):

sudo dnf install ./animica-wallet-1.2.3-1.x86_64.rpm


⸻

Maintain this README alongside packaging scripts. If you change install paths or permissions, update all four formats and QA scripts in lockstep.
