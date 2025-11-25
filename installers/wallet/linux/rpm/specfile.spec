# Animica Wallet — RPM spec
# Build expects a prebuilt Flutter bundle in:
#   - build/linux/x86_64/release/bundle         (for x86_64)
#   - build/linux/arm64|aarch64/release/bundle  (for aarch64)
#
# CI example:
#   flutter build linux --release
#   rpmbuild -bb installers/wallet/linux/rpm/specfile.spec \
#            --define "wal_ver 1.2.3" --define "channel stable"
#
# You can override wal_ver/channel via --define as shown above.

%global wal_ver    0.0.0
%global channel    stable

# Prebuilt binary; skip debuginfo subpackages
%define debug_package %{nil}

Name:           animica-wallet
Version:        %{wal_ver}
Release:        1%{?dist}
Summary:        Animica Wallet (post-quantum) — desktop client for the Animica network
License:        Proprietary
URL:            https://animica.dev/wallet
ExclusiveArch:  x86_64 aarch64

# Basic runtime deps (Fedora/RHEL/SUSE naming). Adjust if your distro differs.
Requires:       glibc
Requires:       libstdc++
Requires:       zlib
Requires:       gtk3
Requires:       glib2
Requires:       gdk-pixbuf2
Requires:       pango
Requires:       atk
Requires:       at-spi2-atk
Requires:       libX11
Requires:       libxcb
Requires:       libxkbcommon
Requires:       libXrandr
Requires:       libXi
Requires:       libXtst
Requires:       libXext
Requires:       libXfixes
Requires:       wayland
Requires:       nss
Requires:       alsa-lib
Requires:       pulseaudio-libs
Requires:       mesa-libgbm
Requires:       libdrm

# Build helpers (best-effort validation)
BuildRequires:  desktop-file-utils
BuildRequires:  sed
BuildRequires:  coreutils
BuildRequires:  findutils

%description
Animica Wallet is a desktop wallet with post-quantum signature support,
deterministic transaction building, and a secure RPC client for the
Animica network. This package installs a prebuilt Flutter bundle under
/opt/AnimicaWallet, a launcher at /usr/bin/animica-wallet, a desktop
entry, and themed icons.

%prep
# No source unpack; we package a prebuilt bundle from the working tree.
:;

%build
# Nothing to build; the Flutter bundle is already compiled.
:;

%install
rm -rf %{buildroot}
# Directories
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/applications
install -d %{buildroot}%{_datadir}/icons/hicolor
install -d %{buildroot}%{_datadir}/licenses/%{name}
install -d %{buildroot}/opt/AnimicaWallet

# Choose bundle path based on target CPU
BND=""
case "%{_target_cpu}" in
  x86_64)
    BND="build/linux/x86_64/release/bundle"
    ;;
  aarch64)
    if [ -d "build/linux/arm64/release/bundle" ]; then
      BND="build/linux/arm64/release/bundle"
    else
      BND="build/linux/aarch64/release/bundle"
    fi
    ;;
  *)
    echo "Unsupported architecture: %{_target_cpu}"
    exit 2
    ;;
esac
test -d "$BND" || { echo "Flutter bundle not found at $BND"; exit 3; }

# Copy bundle into /opt/AnimicaWallet
cp -a "$BND/." %{buildroot}/opt/AnimicaWallet/

# Launcher wrapper
cat > %{buildroot}%{_bindir}/animica-wallet <<'SH'
#!/bin/sh
APP="/opt/AnimicaWallet/AnimicaWallet"
if [ ! -x "$APP" ]; then
  APP="$(find /opt/AnimicaWallet -maxdepth 1 -type f -executable | head -n1)"
fi
export GTK_USE_PORTAL="${GTK_USE_PORTAL:-1}"
exec "$APP" "$@"
SH
chmod 0755 %{buildroot}%{_bindir}/animica-wallet
# Convenience symlink
ln -sf animica-wallet %{buildroot}%{_bindir}/animica || true

# Desktop entry
if [ -f installers/wallet/linux/deb/animica-wallet.desktop ]; then
  # Ensure Exec line uses our wrapper name
  sed 's|^Exec=.*|Exec=animica-wallet %U|' installers/wallet/linux/deb/animica-wallet.desktop \
    > %{buildroot}%{_datadir}/applications/io.animica.Wallet.desktop
else
  cat > %{buildroot}%{_datadir}/applications/io.animica.Wallet.desktop <<'DESK'
[Desktop Entry]
Type=Application
Name=Animica Wallet
Comment=Post-quantum wallet for the Animica network
Exec=animica-wallet %U
Icon=animica-wallet
Terminal=false
Categories=Finance;Network;
MimeType=x-scheme-handler/animica;
DESK
fi

# Icons (expects hicolor/*/apps/animica-wallet.png)
if [ -d installers/wallet/linux/icons ]; then
  cp -a installers/wallet/linux/icons/. %{buildroot}%{_datadir}/icons/hicolor/
fi

# License (EULA)
if [ -f installers/wallet/EULA.txt ]; then
  install -m 0644 installers/wallet/EULA.txt %{buildroot}%{_datadir}/licenses/%{name}/EULA.txt
fi

# Validate desktop file (best-effort; don't fail build on warnings)
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate %{buildroot}%{_datadir}/applications/io.animica.Wallet.desktop || true
fi

%post
# Update desktop and icon caches (ignore failures on minimal systems)
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q %{_datadir}/applications || :
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q %{_datadir}/icons/hicolor || :
fi

%postun
# Refresh caches on uninstall/upgrade too
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q %{_datadir}/applications || :
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q %{_datadir}/icons/hicolor || :
fi

%files
%license %{_datadir}/licenses/%{name}/EULA.txt
%{_bindir}/animica-wallet
%{_bindir}/animica
%{_datadir}/applications/io.animica.Wallet.desktop
%{_datadir}/icons/hicolor/*/apps/animica-wallet.png
%dir /opt/AnimicaWallet
/opt/AnimicaWallet/*

%changelog
* Wed Oct 08 2025 Animica Labs CI <release@animica.dev> - %{wal_ver}-1
- Initial RPM packaging for Animica Wallet.
- Installs prebuilt Flutter bundle under /opt/AnimicaWallet.
- Adds desktop entry, icons, and launcher.
