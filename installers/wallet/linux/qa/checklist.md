# Animica Wallet — Linux Packaging QA Checklist

**Artifacts under test:**
- AppImage (`Animica-Wallet_<version>_<arch>.AppImage` + `.zsync`)
- Flatpak (`io.animica.Wallet` repo/export or `.flatpak`)
- DEB (`animica-wallet_<version>_<arch>.deb`)
- RPM (`animica-wallet-<version>.<arch>.rpm`)

**Architectures:** x86_64, aarch64  
**Desktops:** GNOME (Wayland/X11), KDE, others (best-effort)  
**Distros (minimum matrix):**
- Ubuntu 22.04 LTS (x86_64)
- Debian 12 (x86_64)
- Fedora 40 (x86_64)
- openSUSE Tumbleweed (x86_64)
- Ubuntu 24.04 LTS (aarch64) or RPi OS (Bookworm, aarch64)

---

## 0) Preflight

- [ ] Verify release version / channel matches plan  
  `echo "<version>" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+(-beta\.[0-9]+)?$'`
- [ ] Checksums published and correct  
  `sha256sum -c SHA256SUMS`
- [ ] Signatures exist (where applicable) and validate
  - AppImage: `appimage-signature --check Animica-Wallet_...AppImage` (or embedded GPG)
  - Repo metadata (Flatpak/apt/dnf) signed & verifiable

---

## 1) AppImage

**Smoke**
- [ ] File type: `file Animica-Wallet_*.AppImage`
- [ ] Executable bit set
- [ ] `./Animica-Wallet_*.AppImage --version` prints semantic version
- [ ] Runs on Wayland and X11 (set `GDK_BACKEND` to force)

**Extraction & Runtime**
- [ ] `--appimage-extract` works; `squashfs-root/AppRun` exists
- [ ] `squashfs-root/usr/bin/AnimicaWallet` symlink resolves to bundle
- [ ] `ldd` on main binary shows no missing libs (expect system GL/audio/X/Wayland)

**Desktop Integration**
- [ ] Desktop entry appears after first run (if using AppImageLauncher) or manual install via `animica-wallet.desktop`
- [ ] Icon renders in menus; MIME handler `x-scheme-handler/animica` registers

**Updates**
- [ ] `.zsync` present; `appimageupdatetool Animica-Wallet_...AppImage` performs delta update

**Functional**
- [ ] Create/import wallet; send dummy tx against devnet
- [ ] Simulate call; receive events
- [ ] WebSocket subscriptions alive across sleep/wake

**Logs & Errors**
- [ ] App logs show no GTK/GL warnings on fresh systems
- [ ] Fail gracefully offline (clear error toast; no crash)

---

## 2) Flatpak

**Build/Install**
- [ ] `flatpak-builder --force-clean build installers/wallet/linux/flatpak/io.animica.Wallet.yml` succeeds
- [ ] `flatpak install --user io.animica.Wallet` installs without extra perms

**Run**
- [ ] `flatpak run io.animica.Wallet --version` returns version
- [ ] `flatpak run io.animica.Wallet` launches on Wayland and X11

**Permissions**
- [ ] Check `finish-args`:  
  `flatpak info --show-permissions io.animica.Wallet` matches `finish-args.txt`
- [ ] File chooser uses portal; downloads allowed

**Desktop Integration**
- [ ] Launcher present; icons display
- [ ] Scheme handler registered via desktop file

**Functional**
- [ ] Same functional flow as AppImage passes

**Quality**
- [ ] No sandbox denials in `journalctl --user -u flatpak*` during normal use

---

## 3) DEB

**Lint & Metadata**
- [ ] `lintian animica-wallet_*.deb` (warnings triaged)
- [ ] Control deps align with `installers/wallet/linux/deb/control`

**Install/Upgrade/Remove**
- [ ] `sudo dpkg -i animica-wallet_*.deb` (resolve deps via `apt -f install` if needed)
- [ ] `animica-wallet --version` works
- [ ] Upgrade path (`dpkg -i` newer) preserves settings
- [ ] `sudo apt remove animica-wallet` cleans desktop entry & icons caches refreshed

**Files**
- [ ] `dpkg -L animica-wallet` lists:
  - `/opt/AnimicaWallet/*`
  - `/usr/bin/animica-wallet` wrapper
  - `/usr/share/applications/io.animica.Wallet.desktop`
  - `/usr/share/icons/hicolor/*/apps/animica-wallet.png`

**Desktop Integration**
- [ ] `desktop-file-validate` passes
- [ ] `update-desktop-database` & `gtk-update-icon-cache` run (postinst)

**Functional**
- [ ] Same functional flow as AppImage passes

---

## 4) RPM

**Lint & Metadata**
- [ ] `rpmlint animica-wallet-*.rpm` (warnings triaged)
- [ ] Spec `Requires` satisfy runtime on Fedora/openSUSE

**Install/Upgrade/Remove**
- Fedora: `sudo dnf install animica-wallet-*.rpm`
- openSUSE: `sudo zypper in animica-wallet-*.rpm`
- [ ] `animica-wallet --version` works
- [ ] Upgrade preserves settings
- [ ] Remove cleans desktop & icon caches via `%postun`

**Files**
- [ ] `rpm -qlp animica-wallet-*.rpm` matches DEB layout (under `/opt/AnimicaWallet`)

**Functional**
- [ ] Same functional flow as AppImage passes

---

## 5) Functional Test Suite (All Formats)

- [ ] On first run, onboarding completes; mnemonic generation stable
- [ ] Import known test account; balance fetch works
- [ ] Build & submit devnet tx; receipt confirmed
- [ ] Network switch (test → devnet) updates RPC endpoints
- [ ] Wallet lock/unlock, auto-lock timer, and re-prompt for signing
- [ ] Deep links: `animica://send?to=<addr>&amount=1` launches and pre-fills
- [ ] Localization toggles English/Spanish without restart
- [ ] High DPI & scaling look correct
- [ ] Light/Dark theme switch persists

---

## 6) Reliability & Performance

- [ ] Cold start < 2.0s on x86_64 reference (±0.5s variance)
- [ ] CPU idle < 2% steady; memory steady without leaks across 20 min
- [ ] WS reconnect within 5s after network flap
- [ ] No excessive file watchers; no busy loops

---

## 7) Security & Policy

- [ ] No setuid/setcap binaries in package
- [ ] No world-writable dirs/files in install paths
- [ ] Does not write outside `$XDG_*` locations (Flatpak) or `$HOME/.config/animica` (others)
- [ ] No JIT or shell exec; subprocesses limited to system helpers (portals, icon cache updates)
- [ ] TLS certificates verified for RPC (pin or CA trust per policy)

---

## 8) Accessibility

- [ ] Keyboard navigation for primary flows
- [ ] Screen reader labels present on key controls (Orca on GNOME)
- [ ] Sufficient contrast in light/dark themes

---

## 9) Uninstall Hygiene

- [ ] Removing package leaves no orphaned desktop files/icons
- [ ] User data remains in `$HOME` (documented), not purged without explicit user action

---

## 10) Documentation & Support

- [ ] `--help` prints basic flags
- [ ] About dialog shows version, commit (if available), license
- [ ] Links to homepage, docs, issue tracker valid

---

## 11) Record Results

For each distro/arch/format, capture:

- Distro/arch/kernel/DE
- Artifact name & SHA256
- Pass/fail for each section
- Logs: app startup, functional run, sandbox denials (if any)
- Screenshots: launcher visibility, running app

_Template row:_

| Distro | Arch | Format | Version | Pass? | Notes |
|-------|------|--------|---------|-------|-------|
| Ubuntu 22.04 | x86_64 | AppImage | 1.2.3 | ✅ | — |

---

## Useful Commands (Cheat Sheet)

```bash
# Desktop caches
update-desktop-database -q /usr/share/applications
gtk-update-icon-cache -q /usr/share/icons/hicolor

# Flatpak perms
flatpak info --show-permissions io.animica.Wallet
flatpak override --user --show io.animica.Wallet

# RPM/DEB query
rpm -qlp animica-wallet-*.rpm
dpkg -L animica-wallet

# AppImage extract & run
./Animica-Wallet_*.AppImage --appimage-extract
squashfs-root/AppRun --version


⸻

Sign-off:
Release Owner: __________________ Date: __________
QA Lead: ________________________ Date: __________
