#!/bin/bash
# build-mac-cross.sh — cross-compile the Qt wallet for macOS (aarch64-darwin)
# from Linux using osxcross + Qt6 macOS prefix.
#
# Notes
# - This produces a non-codesigned .app bundle. Use the native release-mac.sh
#   on a real Mac if you need signing / notarization.
# - The bundled-Python node runtime is intentionally skipped (Linux Python
#   venvs aren't valid Mac Python venvs).
# - Requires:
#     /opt/osxcross  (toolchain + SDK)
#     /opt/qt-mac/6.7.0/macos  (Qt mac universal binaries)
#     /opt/qt-linux/6.7.0/gcc_64  (host moc/rcc/uic shim source)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

OSXCROSS_ROOT="${OSXCROSS_ROOT:-/opt/osxcross}"
QT_MAC_ROOT="${QT_MAC_ROOT:-/opt/qt-mac/6.7.0/macos}"
QT_HOST_PATH="${QT_HOST_PATH:-/opt/qt-linux/6.7.0/gcc_64}"
ARCH="${ARCH:-arm64}"      # arm64 or x86_64
SDK_DIR="$(echo "$OSXCROSS_ROOT"/SDK/MacOSX*.sdk | awk '{print $1}')"
OSXCROSS_TARGET="$(echo "$OSXCROSS_ROOT"/bin/${ARCH/arm64/aarch64}-apple-darwin*-clang | awk '{print $1}' | sed -E 's/.*-apple-(darwin[0-9.]+)-clang$/\1/')"

JOBS="${JOBS:-$(nproc)}"
BUILD_DIR="$WALLET_ROOT/build/mac-cross-$ARCH"
APP_NAME="AnimicaWallet.app"
STAGE_DIR="$BUILD_DIR/stage"

case "$ARCH" in
    arm64)    HOST_TRIPLE="aarch64-apple-$OSXCROSS_TARGET" ;;
    x86_64)   HOST_TRIPLE="x86_64-apple-$OSXCROSS_TARGET" ;;
    *) echo "Unknown ARCH=$ARCH" >&2; exit 1 ;;
esac

export OSXCROSS_HOST="$HOST_TRIPLE"
export OSXCROSS_TARGET_DIR="$OSXCROSS_ROOT"
export OSXCROSS_TARGET
export OSXCROSS_SDK="$SDK_DIR"
export PATH="$OSXCROSS_ROOT/bin:$PATH"

echo "[mac-cross] arch:           $ARCH"
echo "[mac-cross] triple:         $HOST_TRIPLE"
echo "[mac-cross] osxcross SDK:   $SDK_DIR"
echo "[mac-cross] Qt mac prefix:  $QT_MAC_ROOT"
echo "[mac-cross] Qt host tools:  $QT_HOST_PATH"

[ -x "$OSXCROSS_ROOT/bin/${HOST_TRIPLE}-clang++" ] || { echo "missing clang++ for $HOST_TRIPLE"; exit 1; }
[ -d "$SDK_DIR" ] || { echo "missing SDK at $SDK_DIR"; exit 1; }
[ -d "$QT_MAC_ROOT/lib/QtCore.framework" ] || { echo "missing Qt mac at $QT_MAC_ROOT"; exit 1; }

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$STAGE_DIR"

cmake -S "$WALLET_ROOT" -B "$BUILD_DIR" \
    -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$OSXCROSS_ROOT/toolchain.cmake" \
    -DCMAKE_OSX_ARCHITECTURES="$ARCH" \
    -DCMAKE_PREFIX_PATH="$QT_MAC_ROOT" \
    -DQT_HOST_PATH="$QT_HOST_PATH" \
    -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
    -DCMAKE_FIND_ROOT_PATH="$QT_MAC_ROOT;$SDK_DIR" \
    -DOPENSSL_USE_STATIC_LIBS=ON \
    -DOPENSSL_ROOT_DIR=/opt/openssl-mac-arm64 \
    -DOPENSSL_INCLUDE_DIR=/opt/openssl-mac-arm64/include \
    -DOPENSSL_CRYPTO_LIBRARY=/opt/openssl-mac-arm64/lib/libcrypto.a \
    -DOPENSSL_SSL_LIBRARY=/opt/openssl-mac-arm64/lib/libssl.a \
    -DWALLET_BUNDLE_PYTHON_RUNTIME=OFF \
    -DWALLET_ENABLE_QT_INSTALL_DEPLOYMENT=OFF \
    -DBUILD_TESTING=OFF

cmake --build "$BUILD_DIR" --parallel "$JOBS"
cmake --install "$BUILD_DIR" --prefix "$STAGE_DIR"

# Locate the produced .app bundle
APP_PATH="$(find "$BUILD_DIR" -maxdepth 4 -name "$APP_NAME" -type d -print -quit)"
if [ -z "$APP_PATH" ]; then
    APP_PATH="$STAGE_DIR/$APP_NAME"
fi
echo "[mac-cross] app at: $APP_PATH"

# Copy Qt frameworks alongside the binary (manual deploy — macdeployqt is Mac-only)
APP_FRAMEWORKS="$APP_PATH/Contents/Frameworks"
APP_PLUGINS="$APP_PATH/Contents/PlugIns"
mkdir -p "$APP_FRAMEWORKS" "$APP_PLUGINS/platforms" "$APP_PLUGINS/styles" "$APP_PLUGINS/imageformats" "$APP_PLUGINS/tls" "$APP_PLUGINS/sqldrivers"

# Use `cp -a` (not -RL) so framework symlinks are preserved. Apple's
# framework layout depends on Versions/Current → A and top-level
# Foo → Versions/Current/Foo symlinks; dereferencing them creates a
# duplicate, bloated bundle that fails codesign because rcodesign
# treats both copies as binaries.
for fw in QtCore QtGui QtWidgets QtNetwork QtSql QtConcurrent QtSvg QtDBus; do
    if [ -d "$QT_MAC_ROOT/lib/$fw.framework" ]; then
        cp -a "$QT_MAC_ROOT/lib/$fw.framework" "$APP_FRAMEWORKS/" 2>/dev/null || true
    fi
done

# Plugins — dylibs, no nested symlinks to preserve, plain copy is fine.
cp -a "$QT_MAC_ROOT/plugins/platforms/libqcocoa.dylib" "$APP_PLUGINS/platforms/" 2>/dev/null || true
cp -a "$QT_MAC_ROOT/plugins/styles/." "$APP_PLUGINS/styles/" 2>/dev/null || true
cp -a "$QT_MAC_ROOT/plugins/imageformats/." "$APP_PLUGINS/imageformats/" 2>/dev/null || true
cp -a "$QT_MAC_ROOT/plugins/tls/." "$APP_PLUGINS/tls/" 2>/dev/null || true
cp -a "$QT_MAC_ROOT/plugins/sqldrivers/." "$APP_PLUGINS/sqldrivers/" 2>/dev/null || true

# Drop .dSYM debug bundles — they contain stub Mach-O DWARF binaries
# that rcodesign tries to sign and fails on. Not needed at runtime.
find "$APP_FRAMEWORKS" "$APP_PLUGINS" -name "*.dSYM" -type d -exec rm -rf {} + 2>/dev/null || true

# Write qt.conf so the app finds bundled plugins
cat > "$APP_PATH/Contents/Resources/qt.conf" <<'EOF'
[Paths]
Plugins = PlugIns
EOF

# Stage Info.plist if missing
if [ ! -f "$APP_PATH/Contents/Info.plist" ]; then
    cat > "$APP_PATH/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>animica-wallet</string>
    <key>CFBundleIdentifier</key><string>org.animica.wallet</string>
    <key>CFBundleName</key><string>Animica Wallet</string>
    <key>CFBundleDisplayName</key><string>Animica Wallet</string>
    <key>CFBundleVersion</key><string>0.1.1</string>
    <key>CFBundleShortVersionString</key><string>0.1.1</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF
fi

# Mach-O fixes
# -----------
# The linker baked in three things that make the bundle fail on real macOS:
#   1. No LC_RPATH — the binary references @rpath/Qt*.framework/... but
#      dyld has nothing to substitute for @rpath, so every framework
#      fails to load and Gatekeeper reports "AnimicaWallet is damaged".
#   2. AGL.framework — CMake's WrapOpenGL pulls in deprecated AGL on
#      macOS; the SDK we cross-link against has a stub AGL.tbd, but
#      modern macOS doesn't ship AGL at the canonical path. Rewrite
#      every AGL load command to OpenGL.framework which IS present.
#   3. No code signature — Apple Silicon Macs reject unsigned binaries
#      as "damaged" outright. Ad-hoc sign every Mach-O with rcodesign.
INT="$OSXCROSS_ROOT/bin/$HOST_TRIPLE-install_name_tool"
BIN="$APP_PATH/Contents/MacOS/$(basename "$APP_PATH" .app)"

echo "[mac-cross] adding LC_RPATH @executable_path/../Frameworks"
"$INT" -add_rpath "@executable_path/../Frameworks" "$BIN" 2>/dev/null || true

echo "[mac-cross] rewriting AGL.framework refs -> OpenGL.framework"
find "$APP_PATH" -type f \( -name "*.dylib" -o -path "*/Versions/A/*" \) \
                 ! -name "Info.plist" ! -path "*.dSYM*" > /tmp/macho-files-$ARCH.txt
"$INT" -change "/System/Library/Frameworks/AGL.framework/Versions/A/AGL" \
                "/System/Library/Frameworks/OpenGL.framework/Versions/A/OpenGL" \
                "$BIN" 2>/dev/null || true
while read f; do
  "$INT" -change "/System/Library/Frameworks/AGL.framework/Versions/A/AGL" \
                  "/System/Library/Frameworks/OpenGL.framework/Versions/A/OpenGL" \
                  "$f" 2>/dev/null || true
done < /tmp/macho-files-$ARCH.txt

# Bundle the self-contained Python runtime (animica) into the .app so the wallet
# runs standalone — mirrors the Windows WINDOWS_NODE_BUNDLE path. MAC_NODE_BUNDLE
# is a dir containing venv/ . Done BEFORE signing so the signature covers it.
if [ -n "${MAC_NODE_BUNDLE:-}" ] && [ -d "${MAC_NODE_BUNDLE}/venv" ]; then
  echo "[mac-cross] bundling Python runtime from ${MAC_NODE_BUNDLE}"
  rm -rf "$APP_PATH/Contents/Resources/node"
  mkdir -p "$APP_PATH/Contents/Resources/node"
  cp -a "${MAC_NODE_BUNDLE}/." "$APP_PATH/Contents/Resources/node/"
  # The bundled Python's Mach-O files must be signed too; add them to the list.
  find "$APP_PATH/Contents/Resources/node" -type f \( -name '*.so' -o -name '*.dylib' -o -name 'python*' \) >> /tmp/macho-files-$ARCH.txt 2>/dev/null || true
fi

# Ad-hoc sign — rcodesign is a pure-Rust reimplementation of `codesign`
# that runs on Linux. Required for Apple Silicon to accept the bundle.
if command -v rcodesign >/dev/null 2>&1; then
  # Drop any stale signatures from the cross-link or earlier passes.
  find "$APP_PATH" -name "_CodeSignature" -type d -exec rm -rf {} + 2>/dev/null || true
  echo "[mac-cross] ad-hoc signing bundle"
  rcodesign sign "$APP_PATH" 2>&1 | tail -3
else
  echo "[mac-cross] WARNING: rcodesign not on PATH — bundle will be UNSIGNED" >&2
  echo "[mac-cross] install with:" >&2
  echo "  curl -sL https://github.com/indygreg/apple-platform-rs/releases/latest -O ..." >&2
fi

# Package as .zip + .dmg-like layout (dmg requires hdiutil which is Mac-only,
# so we ship .zip and leave dmg creation to the native release-mac.sh)
DIST_DIR="$REPO_ROOT/dist/wallet-qt/v0.1.1-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)/macos"
mkdir -p "$DIST_DIR"
ZIP_NAME="animica-wallet-macos-$ARCH.zip"
rm -f "$DIST_DIR/$ZIP_NAME"
( cd "$(dirname "$APP_PATH")" && zip -ryq "$DIST_DIR/$ZIP_NAME" "$(basename "$APP_PATH")" )
sha256sum "$DIST_DIR/$ZIP_NAME" | awk '{print $1}' > "$DIST_DIR/$ZIP_NAME.sha256"

echo ""
echo "[mac-cross] OK"
echo "[mac-cross] App:  $APP_PATH"
echo "[mac-cross] Zip:  $DIST_DIR/$ZIP_NAME"
echo "[mac-cross] sha:  $(cat "$DIST_DIR/$ZIP_NAME.sha256")"
