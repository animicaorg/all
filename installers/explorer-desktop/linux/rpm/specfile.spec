# -----------------------------------------------------------------------------
# Animica Explorer — RPM spec
# Builds the Tauri-based desktop shell from source and installs the binary,
# desktop entry, and icon. Tested on Fedora 39/40 (should adapt to other RPM
# distros that ship WebKitGTK 4.1).
#
# How to build locally:
#   # Create a source tarball with the repo contents:
#   git archive --format=tar.gz --prefix=animica-explorer-0.1.0/ \
#     -o ~/rpmbuild/SOURCES/animica-explorer-0.1.0.tar.gz HEAD
#   # Then:
#   rpmbuild -ba installers/explorer-desktop/linux/rpm/specfile.spec
#
# Notes:
# - The Tauri Rust crate must live at installers/explorer-desktop/tauri/ in the
#   extracted source tree (as laid out in this repository).
# - If your distro uses different pkgconfig names for WebKitGTK, adjust the
#   BuildRequires pkgconfig(webkit2gtk-4.1) line accordingly.
# -----------------------------------------------------------------------------

Name:           animica-explorer
Version:        0.1.0
Release:        1%{?dist}
Summary:        Animica Explorer desktop shell (Tauri)

# Update to your actual project license
License:        MIT
URL:            https://animica.dev
Source0:        %{name}-%{version}.tar.gz

# Toolchain & helpers
BuildRequires:  cargo
BuildRequires:  rust
BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  desktop-file-utils
BuildRequires:  appstream

# GUI/WebKit stack via pkgconfig to stay portable across RPM distros
BuildRequires:  pkgconfig(gtk+-3.0)
BuildRequires:  pkgconfig(webkit2gtk-4.1)
BuildRequires:  pkgconfig(glib-2.0)
BuildRequires:  pkgconfig(gdk-pixbuf-2.0)
BuildRequires:  pkgconfig(pango)
BuildRequires:  pkgconfig(atk)
BuildRequires:  pkgconfig(xkbcommon)

# Scriptlet helpers (usually present by default)
Requires(post):   desktop-file-utils
Requires(post):   hicolor-icon-theme
Requires(postun): desktop-file-utils
Requires(postun): hicolor-icon-theme

%description
Animica Explorer is a lightweight desktop shell for the Animica blockchain
explorer, packaged as a native Tauri application. It runs the Explorer UI with
tight OS integration and sandbox-friendly defaults.

%prep
%autosetup -n %{name}-%{version}

%build
# Optimize for smaller binaries in release; honor reproducible builds if configured
export RUSTFLAGS="${RUSTFLAGS:-} -C debuginfo=0 -C link-arg=-Wl,-O1 -C lto=fat -C codegen-units=1"
cargo build --release --locked --manifest-path installers/explorer-desktop/tauri/Cargo.toml

%install
# Create target dirs
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/applications
install -d %{buildroot}%{_datadir}/icons/hicolor/512x512/apps

# Install binary
install -m 0755 installers/explorer-desktop/tauri/target/release/animica-explorer \
  %{buildroot}%{_bindir}/animica-explorer

# Desktop entry: use the one from the repo if present, otherwise generate
if [ -f installers/explorer-desktop/linux/deb/animica-explorer.desktop ]; then
  install -m 0644 installers/explorer-desktop/linux/deb/animica-explorer.desktop \
    %{buildroot}%{_datadir}/applications/io.animica.Explorer.desktop
else
  cat > %{buildroot}%{_datadir}/applications/io.animica.Explorer.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Animica Explorer
GenericName=Blockchain Explorer
Comment=Lightweight desktop shell for the Animica blockchain explorer
TryExec=animica-explorer
Exec=animica-explorer %U
Icon=io.animica.Explorer
Terminal=false
StartupNotify=true
Categories=Network;Utility;Development;
Keywords=Blockchain;Explorer;Animica;Blocks;Transactions;
StartupWMClass=animica-explorer
DESKTOP
fi

# Icon: prefer Tauri 512x512 icon from the repo
if [ -f installers/explorer-desktop/tauri/icons/512x512.png ]; then
  install -m 0644 installers/explorer-desktop/tauri/icons/512x512.png \
    %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/io.animica.Explorer.png
elif command -v convert >/dev/null 2>&1; then
  # Fallback: generate a placeholder if ImageMagick is available in the buildroot
  convert -size 512x512 xc:'#0F1115' -gravity center -pointsize 72 -fill '#FFFFFF' \
    -annotate 0 'A' %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/io.animica.Explorer.png || :
fi

%check
# Validate desktop entry (non-fatal if tool missing)
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate %{buildroot}%{_datadir}/applications/io.animica.Explorer.desktop
fi
# AppStream validation (relaxed) if present
if command -v appstream-util >/dev/null 2>&1; then
  appstream-util validate-relax --nonet %{buildroot}%{_datadir}/applications/io.animica.Explorer.desktop || :
fi

%post
# Update desktop database & icon cache
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q %{_datadir}/applications || :
fi
if [ -d %{_datadir}/icons/hicolor ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q %{_datadir}/icons/hicolor || :
fi

%postun
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q %{_datadir}/applications || :
fi
if [ -d %{_datadir}/icons/hicolor ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q %{_datadir}/icons/hicolor || :
fi

%files
%{_bindir}/animica-explorer
%{_datadir}/applications/io.animica.Explorer.desktop
%{_datadir}/icons/hicolor/512x512/apps/io.animica.Explorer.png

%changelog
* Wed Oct 08 2025 Animica Labs <support@animica.dev> - 0.1.0-1
- Initial stable release: Tauri-based desktop shell
- Installs binary, desktop entry, and 512x512 icon
- Scriptlets refresh desktop DB and icon cache
