#!/bin/bash
# release-mac.sh - Build and package a native macOS release for Animica Wallet
#
# Usage:
#   ./scripts/release-mac.sh [--dmg] [--sign] [--adhoc-sign] [--notarize] [--debug]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

BUILD_TYPE="Release"
CREATE_DMG=false
SIGN_APP=false
ADHOC_SIGN=false
NOTARIZE_APP=false
OUTPUT_DIR="$REPO_ROOT/dist/wallet-qt"

while [[ $# -gt 0 ]]; do
    case $1 in
        --dmg)
            CREATE_DMG=true
            shift
            ;;
        --sign)
            SIGN_APP=true
            shift
            ;;
        --adhoc-sign)
            ADHOC_SIGN=true
            shift
            ;;
        --notarize)
            NOTARIZE_APP=true
            SIGN_APP=true
            shift
            ;;
        --debug)
            BUILD_TYPE="Debug"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--dmg] [--sign] [--adhoc-sign] [--notarize] [--debug]"
            exit 1
            ;;
    esac
done

if [[ "$SIGN_APP" == "true" && "$ADHOC_SIGN" == "true" ]]; then
    echo "Choose either --sign or --adhoc-sign, not both."
    exit 1
fi

ARCH="$(uname -m)"
BUILD_DIR="$WALLET_ROOT/build/mac-release"
INSTALL_DIR="$BUILD_DIR/stage"

cd "$REPO_ROOT"
if git describe --tags --exact-match >/dev/null 2>&1; then
    VERSION="$(git describe --tags --exact-match)"
else
    BASE_VERSION="$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.1.0")"
    VERSION="${BASE_VERSION}-$(git rev-parse --short HEAD)"
fi

echo "======================================"
echo "Animica Wallet - macOS Release Build"
echo "======================================"
echo "Build type: $BUILD_TYPE"
echo "Architecture: $ARCH"
echo "Version: $VERSION"
echo "Create DMG: $CREATE_DMG"
echo "Developer ID signing: $SIGN_APP"
echo "Ad-hoc signing: $ADHOC_SIGN"
echo "Notarization: $NOTARIZE_APP"
echo ""

python3 "$WALLET_ROOT/scripts/gen-icons.py" --check >/dev/null 2>&1 || python3 "$WALLET_ROOT/scripts/gen-icons.py"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

if [[ -z "${CMAKE_PREFIX_PATH:-}" ]]; then
    for candidate in /opt/homebrew/opt/qt@6 /usr/local/opt/qt@6 /opt/homebrew/opt/qt /usr/local/opt/qt; do
        if [[ -d "$candidate" ]]; then
            export CMAKE_PREFIX_PATH="$candidate"
            break
        fi
    done
fi

cmake -S "$WALLET_ROOT" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DCMAKE_OSX_ARCHITECTURES="$ARCH" \
    -DWALLET_REMOTE_RPC_ONLY=OFF \
    -DBUILD_TESTING=OFF

cmake --build "$BUILD_DIR" --config "$BUILD_TYPE" -j"$(sysctl -n hw.ncpu)"

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cmake --install "$BUILD_DIR" --config "$BUILD_TYPE" --prefix "$INSTALL_DIR"

STAGED_APP="$INSTALL_DIR/AnimicaWallet.app"
python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform macos --path "$STAGED_APP"

DIST_DIR="$OUTPUT_DIR/$VERSION/macos"
mkdir -p "$DIST_DIR"

RELEASE_APP="$DIST_DIR/AnimicaWallet-${VERSION}-macos-${ARCH}.app"
rm -rf "$RELEASE_APP"
cp -R "$STAGED_APP" "$RELEASE_APP"

sign_app() {
    local app_bundle="$1"

    if [[ "$ADHOC_SIGN" == "true" ]]; then
        echo "Applying ad-hoc signing fallback..."
        codesign --force --deep --sign - "$app_bundle"
        codesign --verify --verbose=2 "$app_bundle"
        return
    fi

    if [[ "$SIGN_APP" == "true" ]]; then
        if [[ -z "${CODESIGN_IDENTITY:-}" ]]; then
            echo "CODESIGN_IDENTITY is required for --sign"
            exit 1
        fi

        find "$app_bundle/Contents/Resources/node" -type f \( -name "*.so" -o -name "*.dylib" \) \
            -exec codesign --force --sign "$CODESIGN_IDENTITY" {} \;
        codesign --force --sign "$CODESIGN_IDENTITY" "$app_bundle/Contents/Resources/node/venv/bin/python"
        codesign --force --deep --sign "$CODESIGN_IDENTITY" \
            --options runtime \
            --entitlements "$WALLET_ROOT/resources/macos/entitlements.plist" \
            "$app_bundle"
        codesign --verify --verbose=2 "$app_bundle"
        spctl --assess --verbose=2 "$app_bundle"
    fi
}

sign_app "$RELEASE_APP"

if [[ "$NOTARIZE_APP" == "true" ]]; then
    if [[ -z "${APPLE_ID:-}" || -z "${APPLE_TEAM_ID:-}" ]]; then
        echo "APPLE_ID and APPLE_TEAM_ID are required for notarization."
        exit 1
    fi

    NOTARIZE_ZIP="$BUILD_DIR/AnimicaWallet-notarize.zip"
    rm -f "$NOTARIZE_ZIP"
    ditto -c -k --keepParent "$RELEASE_APP" "$NOTARIZE_ZIP"
    xcrun notarytool submit "$NOTARIZE_ZIP" \
        --apple-id "$APPLE_ID" \
        --team-id "$APPLE_TEAM_ID" \
        --password "@keychain:AC_PASSWORD" \
        --wait
    xcrun stapler staple "$RELEASE_APP"
    spctl --assess --type execute --verbose=2 "$RELEASE_APP"
fi

if [[ "$CREATE_DMG" == "true" ]]; then
    DMG_PATH="$DIST_DIR/AnimicaWallet-${VERSION}-macos-${ARCH}.dmg"
    rm -f "$DMG_PATH"

    if command -v create-dmg >/dev/null 2>&1; then
        create-dmg \
            --volname "Animica Wallet" \
            --volicon "$WALLET_ROOT/resources/icons/animica.icns" \
            --window-pos 200 120 \
            --window-size 640 420 \
            --icon-size 100 \
            --icon "AnimicaWallet.app" 180 150 \
            --hide-extension "AnimicaWallet.app" \
            --app-drop-link 460 150 \
            "$DMG_PATH" \
            "$RELEASE_APP"
    else
        hdiutil create -volname "Animica Wallet" \
            -srcfolder "$RELEASE_APP" \
            -ov -format UDZO \
            "$DMG_PATH"
    fi
fi

(
    cd "$DIST_DIR"
    find . -type f \( -name "*.app" -o -name "*.dmg" \) -exec shasum -a 256 {} \; > SHA256SUMS
)

echo ""
echo "======================================"
echo "Release Build Complete"
echo "======================================"
echo "Staged app: $RELEASE_APP"
if [[ "$CREATE_DMG" == "true" ]]; then
    echo "DMG: $DIST_DIR/AnimicaWallet-${VERSION}-macos-${ARCH}.dmg"
fi
echo "Smoke test: $SCRIPT_DIR/smoke-test-mac.sh \"$RELEASE_APP\""
echo "Ad-hoc signing fallback: ./scripts/release-mac.sh --adhoc-sign --dmg"
echo "Developer ID signing: export CODESIGN_IDENTITY='Developer ID Application: ...'"
echo "Notarization placeholder: export APPLE_ID=... APPLE_TEAM_ID=..."
