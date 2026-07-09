#!/bin/bash
# release-linux.sh - Build and package Linux release for Animica Wallet
#
# Creates:
# - AppImage (portable desktop distribution)
# - .deb package (Ubuntu/Debian install)
# - tar.gz bundle with bundled Qt/runtime libs
#
# Usage:
#   ./scripts/release-linux.sh [--appimage-only] [--deb-only] [--tarball-only] [--portable-only] [--debug]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

BUILD_TYPE="Release"
BUILD_APPIMAGE=true
BUILD_DEB=true
BUILD_TARBALL=true
CLEAN_BUILD=false
OUTPUT_DIR="$REPO_ROOT/dist/wallet-qt"
JOBS="$(nproc 2>/dev/null || echo 4)"
RELEASE_APPIMAGE=""
RELEASE_DEB=""
RELEASE_TARBALL=""

usage() {
    cat <<'EOF'
Usage: ./scripts/release-linux.sh [options]

Options:
  --appimage-only  Build only the AppImage
  --deb-only       Build only the .deb package
  --tarball-only   Build only the portable tar.gz bundle
  --portable-only  Build the AppImage and portable tar.gz bundle
  --clean          Remove the previous linux-release build directory first
  --debug          Build Debug artifacts instead of Release
  --help           Show this help
EOF
}

log_section() {
    echo ""
    echo "======================================"
    echo "$1"
    echo "======================================"
}

find_qmake() {
    if command -v qmake6 >/dev/null 2>&1; then
        command -v qmake6
    elif command -v qmake >/dev/null 2>&1; then
        command -v qmake
    fi
}

find_qt_plugin_path() {
    if command -v qtpaths6 >/dev/null 2>&1; then
        qtpaths6 --query QT_INSTALL_PLUGINS 2>/dev/null || true
        return
    fi

    if command -v qtpaths >/dev/null 2>&1; then
        qtpaths --query QT_INSTALL_PLUGINS 2>/dev/null || true
        return
    fi

    local qmake_bin
    qmake_bin="$(find_qmake || true)"
    if [ -n "$qmake_bin" ]; then
        "$qmake_bin" -query QT_INSTALL_PLUGINS 2>/dev/null || true
    fi
}

prepare_portable_stage() {
    local appdir="$1"
    local portable_launcher="$appdir/animica-wallet"
    local root_desktop="$appdir/animica-wallet.desktop"
    local root_icon="$appdir/animica-wallet.png"
    local qt_plugin_path=""
    local qmake_bin=""
    rm -rf "$appdir"
    mkdir -p "$appdir"
    cp -a "$INSTALL_STAGE/." "$appdir/"

    cp "$appdir/usr/share/applications/animica-wallet.desktop" "$root_desktop"
    cp "$WALLET_ROOT/resources/icons/hicolor/256x256/apps/animica-wallet.png" "$root_icon"
    ln -sfn "animica-wallet.png" "$appdir/.DirIcon"

    cat > "$portable_launcher" <<'EOF'
#!/bin/sh
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ -x "$HERE/AppRun" ]; then
    exec "$HERE/AppRun" "$@"
fi

exec "$HERE/usr/bin/animica-wallet" "$@"
EOF
    chmod +x "$portable_launcher"

    qmake_bin="$(find_qmake || true)"
    if [ -n "$qmake_bin" ]; then
        export QMAKE="$qmake_bin"
    fi

    qt_plugin_path="$(find_qt_plugin_path || true)"
    if [ -n "$qt_plugin_path" ] && [ -d "$qt_plugin_path" ]; then
        export QT_PLUGIN_PATH="$qt_plugin_path"
    fi

    # Python C-extension wheels bundled in the venv (e.g. psycopg[binary]) ship
    # private "*.libs" dirs whose sibling .so files are referenced by mangled
    # SONAMEs (libkeyutils-<hash>.so, libkrb5support-<hash>.so, …) that are not
    # on the default loader path. linuxdeployqt runs ldd over everything under
    # usr/lib and aborts if any dependency is unresolved, so make those private
    # dirs discoverable to ldd first.
    _venv_libdirs="$(find "$appdir" -type d -name '*.libs' 2>/dev/null | tr '\n' ':')"
    if [ -n "$_venv_libdirs" ]; then
        export LD_LIBRARY_PATH="${_venv_libdirs}${LD_LIBRARY_PATH:-}"
        echo "linuxdeployqt: added bundled venv lib dirs to LD_LIBRARY_PATH"
    fi

    # NVIDIA GPUDirect Storage over RDMA (libcufile_rdma + friends) hard-links to
    # Mellanox InfiniBand userspace (libmlx5.so.1), absent on the build runner and
    # on any desktop. linuxdeployqt's ldd sweep aborts on that unresolved dep.
    # GPUDirect-over-InfiniBand is a multi-node datacenter feature — the bundled
    # node's local mining / AI useful-work / ENA training runs on regular CUDA and
    # never loads the RDMA variant — so drop just those RDMA libs. torch + CUDA
    # compute (and thus in-wallet training) stay fully intact.
    _rdma_libs="$(find "$appdir" -type f -path '*nvidia*' \( -name 'libcufile_rdma.so*' -o -name '*_rdma.so*' \) 2>/dev/null)"
    if [ -n "$_rdma_libs" ]; then
        echo "linuxdeployqt: pruning unused NVIDIA GPUDirect-RDMA libs (require InfiniBand/libmlx5):"
        printf '  %s\n' $_rdma_libs
        find "$appdir" -type f -path '*nvidia*' \( -name 'libcufile_rdma.so*' -o -name '*_rdma.so*' \) -delete 2>/dev/null || true
    fi

    if ! APPIMAGE_EXTRACT_AND_RUN=1 linuxdeployqt "$appdir/usr/share/applications/animica-wallet.desktop" \
        -appimage \
        -bundle-non-qt-libs \
        -no-translations \
        -verbose=1; then
        echo "linuxdeployqt failed while preparing portable Linux artifacts." >&2
        echo "If this host uses glibc newer than Ubuntu 22.04 (2.35), build AppImage/tarball artifacts on an older Linux runner or container." >&2
        exit 1
    fi

    python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform linux --path "$appdir"
}

build_deb_package() {
    local deb_stage="$1"
    local debian_dir="$deb_stage/DEBIAN"
    local installed_size
    local deb_name
    local deb_path

    rm -rf "$deb_stage"
    mkdir -p "$deb_stage"
    cp -a "$INSTALL_STAGE/." "$deb_stage/"

    python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform linux --path "$deb_stage/usr"

    mkdir -p "$debian_dir"
    installed_size="$(du -sk "$deb_stage/usr" | cut -f1)"

    cat > "$debian_dir/control" <<EOF
Package: animica-wallet
Version: $PACKAGE_VERSION
Section: net
Priority: optional
Architecture: $DEB_ARCH
Depends: libc6, libqt6core6, libqt6gui6, libqt6widgets6, libqt6network6, libqt6sql6, libssl3
Installed-Size: $installed_size
Maintainer: Animica Team <support@animica.org>
Description: Animica remote desktop wallet
 A cross-platform desktop wallet for Animica mainnet accounts.
 The Qt wallet connects directly to the hosted RPC endpoint
 https://rpc.animica.org/rpc and does not run a local node.
EOF

    deb_name="animica-wallet_${PACKAGE_VERSION}_${DEB_ARCH}.deb"
    deb_path="$BUILD_DIR/$deb_name"

    dpkg-deb --build "$deb_stage" "$deb_path"
    RELEASE_DEB="$DIST_DIR/$deb_name"
    cp "$deb_path" "$RELEASE_DEB"

    echo "✓ DEB created: $RELEASE_DEB"
    echo "DEB package info:"
    dpkg-deb --info "$deb_path" | grep -E "Package|Version|Architecture|Depends|Description"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --appimage-only)
            BUILD_DEB=false
            BUILD_TARBALL=false
            shift
            ;;
        --deb-only)
            BUILD_APPIMAGE=false
            BUILD_TARBALL=false
            shift
            ;;
        --tarball-only)
            BUILD_APPIMAGE=false
            BUILD_DEB=false
            shift
            ;;
        --portable-only)
            BUILD_DEB=false
            shift
            ;;
        --clean)
            CLEAN_BUILD=true
            shift
            ;;
        --debug)
            BUILD_TYPE="Debug"
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [ "$BUILD_APPIMAGE" = false ] && [ "$BUILD_DEB" = false ] && [ "$BUILD_TARBALL" = false ]; then
    echo "No Linux release artifact selected." >&2
    exit 1
fi

BUILD_PORTABLE=false
if [ "$BUILD_APPIMAGE" = true ] || [ "$BUILD_TARBALL" = true ]; then
    BUILD_PORTABLE=true
fi

echo "======================================"
echo "Animica Wallet - Linux Release Build"
echo "======================================"
echo "Build type: $BUILD_TYPE"
echo "Build AppImage: $BUILD_APPIMAGE"
echo "Build DEB: $BUILD_DEB"
echo "Build tarball: $BUILD_TARBALL"
echo "Clean build: $CLEAN_BUILD"
echo ""

ARCH="$(uname -m)"
echo "Building for architecture: $ARCH"

case "$ARCH" in
    x86_64)
        DEB_ARCH="amd64"
        ;;
    aarch64)
        DEB_ARCH="arm64"
        ;;
    *)
        DEB_ARCH="$ARCH"
        ;;
esac

cd "$REPO_ROOT"
if git describe --tags --exact-match >/dev/null 2>&1; then
    VERSION="$(git describe --tags --exact-match)"
else
    VERSION="$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.1.1")"
    COMMIT="$(git rev-parse --short HEAD)"
    VERSION="${VERSION}-${COMMIT}"
fi
PACKAGE_VERSION="${VERSION#v}"

echo "Version: $VERSION"
echo ""

log_section "Checking Prerequisites"

if [ "$BUILD_PORTABLE" = true ] && ! command -v linuxdeployqt >/dev/null 2>&1; then
    echo "linuxdeployqt is required for AppImage and portable tarball builds." >&2
    echo "Download from: https://github.com/probonopd/linuxdeployqt/releases" >&2
    exit 1
fi

if [ "$BUILD_DEB" = true ] && ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb is required for .deb packaging." >&2
    exit 1
fi

echo "Checking icons..."
cd "$WALLET_ROOT"
if ! python3 scripts/gen-icons.py --check >/dev/null 2>&1; then
    echo "Generating icons..."
    python3 scripts/gen-icons.py
fi

BUILD_DIR="$WALLET_ROOT/build/linux-release"
INSTALL_STAGE="$BUILD_DIR/install-stage"
APPDIR="$BUILD_DIR/AppDir"
DEB_STAGE="$BUILD_DIR/deb-stage"
PORTABLE_ROOT_NAME="AnimicaWallet-${VERSION}-linux-${ARCH}"
PORTABLE_ROOT="$BUILD_DIR/$PORTABLE_ROOT_NAME"
DIST_DIR="$OUTPUT_DIR/$VERSION/linux"
WEBSITE_WALLET_DIR="$REPO_ROOT/website/public/wallet"

if [ "$CLEAN_BUILD" = true ]; then
    rm -rf "$BUILD_DIR"
fi
mkdir -p "$BUILD_DIR" "$DIST_DIR"

log_section "Configuring Build"
cd "$BUILD_DIR"
cmake "$WALLET_ROOT" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DWALLET_BUNDLE_PYTHON_RUNTIME=ON \
    -DBUILD_TESTING=OFF

log_section "Building Wallet"
cmake --build . --config "$BUILD_TYPE" -j"$JOBS"

WALLET_EXE="$BUILD_DIR/bin/animica-wallet"
[ -f "$WALLET_EXE" ] || { echo "Executable not found at $WALLET_EXE" >&2; exit 1; }

log_section "Validating Build"
echo "✓ Executable created: $WALLET_EXE"

echo "Checking dynamic libraries of wallet binary..."
ldd "$WALLET_EXE" | grep -E "Qt|libssl|liboqs" || true

log_section "Staging Install Tree"
rm -rf "$INSTALL_STAGE"
mkdir -p "$INSTALL_STAGE"
DESTDIR="$INSTALL_STAGE" cmake --install "$BUILD_DIR"
python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform linux --path "$INSTALL_STAGE/usr"
echo "✓ Installed runtime staged at $INSTALL_STAGE/usr"

if [ "$BUILD_PORTABLE" = true ]; then
    log_section "Creating Portable Runtime"
    prepare_portable_stage "$APPDIR"

    GENERATED_APPIMAGE="$(find "$BUILD_DIR" -maxdepth 1 -name "*.AppImage" | head -1 || true)"
    if [ "$BUILD_APPIMAGE" = true ]; then
        if [ -n "$GENERATED_APPIMAGE" ] && [ -f "$GENERATED_APPIMAGE" ]; then
            RELEASE_APPIMAGE="$DIST_DIR/AnimicaWallet-${VERSION}-linux-${ARCH}.AppImage"
            mv "$GENERATED_APPIMAGE" "$RELEASE_APPIMAGE"
            chmod +x "$RELEASE_APPIMAGE"
            echo "✓ AppImage created: $RELEASE_APPIMAGE"
        else
            echo "AppImage creation failed" >&2
            exit 1
        fi
    elif [ -n "$GENERATED_APPIMAGE" ] && [ -f "$GENERATED_APPIMAGE" ]; then
        rm -f "$GENERATED_APPIMAGE"
    fi

    if [ "$BUILD_TARBALL" = true ]; then
        rm -rf "$PORTABLE_ROOT"
        mkdir -p "$PORTABLE_ROOT"
        cp -a "$APPDIR/." "$PORTABLE_ROOT/"

        RELEASE_TARBALL="$DIST_DIR/${PORTABLE_ROOT_NAME}.tar.gz"
        tar -czf "$RELEASE_TARBALL" -C "$BUILD_DIR" "$PORTABLE_ROOT_NAME"
        echo "✓ Portable tarball created: $RELEASE_TARBALL"
    fi
fi

if [ "$BUILD_DEB" = true ]; then
    log_section "Creating DEB Package"
    build_deb_package "$DEB_STAGE"
fi

if [ -d "$REPO_ROOT/website/public" ] && [ -z "${WALLET_SKIP_WEBSITE_PUBLISH:-}" ]; then
    log_section "Refreshing Website Downloads"
    publish_args=(
        --platform linux
        --version "$VERSION"
        --website-dir "$WEBSITE_WALLET_DIR"
        --raw "$INSTALL_STAGE/usr/bin/animica-wallet"
    )
    if [ -n "$RELEASE_APPIMAGE" ] && [ -f "$RELEASE_APPIMAGE" ]; then
        publish_args+=(--appimage "$RELEASE_APPIMAGE")
    fi
    if [ -n "$RELEASE_DEB" ] && [ -f "$RELEASE_DEB" ]; then
        publish_args+=(--deb "$RELEASE_DEB")
    fi
    if [ -n "$RELEASE_TARBALL" ] && [ -f "$RELEASE_TARBALL" ]; then
        publish_args+=(--tarball "$RELEASE_TARBALL")
    fi
    "$SCRIPT_DIR/publish-wallet-downloads.sh" "${publish_args[@]}"
fi

log_section "Generating Checksums"
cd "$DIST_DIR"
find . -type f \( -name "*.AppImage" -o -name "*.deb" -o -name "*.tar.gz" \) -exec sha256sum {} \; > SHA256SUMS
cat SHA256SUMS

log_section "Release Build Complete"
echo "Version: $VERSION"
echo "Architecture: $ARCH (DEB: $DEB_ARCH)"
echo "Output directory: $DIST_DIR"
echo ""
echo "Artifacts:"
ls -lh "$DIST_DIR"
echo ""
echo "Portable Linux artifact suggestions:"
if [ -n "$RELEASE_APPIMAGE" ] && [ -f "$RELEASE_APPIMAGE" ]; then
    echo "  AppImage:   $RELEASE_APPIMAGE"
fi
if [ -n "$RELEASE_TARBALL" ] && [ -f "$RELEASE_TARBALL" ]; then
    echo "  Tarball:    tar -xzf $RELEASE_TARBALL && ./$PORTABLE_ROOT_NAME/animica-wallet"
fi
if [ -n "$RELEASE_DEB" ] && [ -f "$RELEASE_DEB" ]; then
    echo "  Debian:     sudo dpkg -i $RELEASE_DEB"
fi
