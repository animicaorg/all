#!/usr/bin/env bash
# Animica Wallet — macOS build → sign → notarize → DMG/PKG
# Requires:
#   - macOS with Xcode CLT, Flutter SDK installed and configured for macOS.
#   - Developer ID certificates available in the active keychain.
#   - installers/wallet/macos/sign_and_notarize.sh present (called by this script).
#
# Usage:
#   ./installers/wallet/macos/scripts/build_release.sh \
#     --version 1.2.3 \
#     --build 123 \
#     --channel stable \
#     --identity "Developer ID Application: Animica Labs (ABCDE12345)" \
#     --installer-identity "Developer ID Installer: Animica Labs (ABCDE12345)"
#
# Optional env:
#   ASC_API_KEY_P8_BASE64, ASC_KEY_ID, ASC_ISSUER_ID  # notarization
#   SPARKLE_PUBLIC_ED25519                            # Ed25519 public key shown in Info.plist
#   CODE_SIGN_IDENTITY_NAME                           # default for --identity
#   APPLE_TEAM_ID                                     # used by verify_signatures.sh
#
# Notes:
#   - This script updates the built app's Info.plist with bundle id, version,
#     Sparkle feed URL for the selected channel, and the public update key.
set -euo pipefail

### ---------- helpers ----------
log() { printf "\033[1;34m[build]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err() { printf "\033[1;31m[err]\033[0m %s\n" "$*" >&2; }
die() { err "$*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing required tool: $1"; }

json_py() {
  /usr/bin/python3 - "$@" <<'PY'
import json, sys, pathlib
# args: <file> <json_pointer_like path parts...>
if len(sys.argv) < 3:
    sys.exit(1)
p = pathlib.Path(sys.argv[1]).read_text()
j = json.loads(p)
for k in sys.argv[2:]:
    j = j[k]
if isinstance(j, (dict, list)):
    print(json.dumps(j))
else:
    print(j)
PY
}

### ---------- defaults / args ----------
VERSION=""
BUILD_NUMBER=""
CHANNEL="stable"
IDENTITY="${CODE_SIGN_IDENTITY_NAME:-}"
INSTALLER_IDENTITY=""
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
BUILD_DIR="${PROJECT_ROOT}/build/macos/Build/Products/Release"
APP_METADATA="${PROJECT_ROOT}/installers/wallet/config/app_metadata.json"
CHANNELS_JSON="${PROJECT_ROOT}/installers/wallet/config/channels.json"
ENTITLEMENTS="${PROJECT_ROOT}/installers/wallet/macos/entitlements.plist"
SIGN_SCRIPT="${PROJECT_ROOT}/installers/wallet/macos/sign_and_notarize.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2;;
    --build) BUILD_NUMBER="$2"; shift 2;;
    --channel) CHANNEL="$2"; shift 2;;
    --identity) IDENTITY="$2"; shift 2;;
    --installer-identity) INSTALLER_IDENTITY="$2"; shift 2;;
    -h|--help)
      sed -n '1,120p' "$0"; exit 0;;
    *) die "Unknown arg: $1";;
  esac
done

[[ -n "$VERSION" ]] || die "--version is required"
[[ -n "$BUILD_NUMBER" ]] || die "--build is required (integer, e.g., CI run number)"
[[ "$CHANNEL" =~ ^(stable|beta)$ ]] || die "--channel must be stable|beta"
[[ -n "$IDENTITY" ]] || die "--identity (Developer ID Application) is required"
[[ -n "$INSTALLER_IDENTITY" ]] || warn "No --installer-identity provided; PKG will be skipped"

### ---------- tool checks ----------
need flutter
need plutil
need /usr/libexec/PlistBuddy
need xcrun
need hdiutil
need codesign
[[ -x "$SIGN_SCRIPT" ]] || die "Missing signer script: $SIGN_SCRIPT"

### ---------- read app metadata ----------
[[ -f "$APP_METADATA" ]] || die "Missing $APP_METADATA"
APP_NAME="$(json_py "$APP_METADATA" name)"
BUNDLE_ID="$(json_py "$APP_METADATA" bundle macos)"
HOMEPAGE="$(json_py "$APP_METADATA" homepage)"

[[ -n "$APP_NAME" && -n "$BUNDLE_ID" ]] || die "Invalid app_metadata.json (name/bundle.macos)"

### ---------- resolve Sparkle feed URL by channel ----------
[[ -f "$CHANNELS_JSON" ]] || die "Missing $CHANNELS_JSON"
FEED_URL="$(json_py "$CHANNELS_JSON" channels | /usr/bin/python3 - "$CHANNEL" <<'PY'
import json, sys
channels = json.loads(sys.stdin.read())
target = sys.argv[1]
for ch in channels:
    if ch.get("id")==target:
        print(ch["platforms"]["macos"]["feed"])
        sys.exit(0)
sys.exit(2)
PY
)"
[[ -n "$FEED_URL" ]] || die "Could not resolve macOS Sparkle feed for channel=$CHANNEL"

if [[ -z "${SPARKLE_PUBLIC_ED25519:-}" ]]; then
  warn "SPARKLE_PUBLIC_ED25519 not set; Info.plist will have empty SUPublicEDKey"
fi

### ---------- flutter build ----------
log "Building Flutter macOS release (VERSION=$VERSION, BUILD=$BUILD_NUMBER)"
pushd "$PROJECT_ROOT" >/dev/null
flutter build macos --release
popd >/dev/null

APP_PATH="${BUILD_DIR}/${APP_NAME}.app"
[[ -d "$APP_PATH" ]] || die "Built app not found at $APP_PATH"

### ---------- patch Info.plist ----------
PLIST="${APP_PATH}/Contents/Info.plist"
[[ -f "$PLIST" ]] || die "Missing Info.plist in app bundle"

log "Updating Info.plist identifiers/versions and Sparkle keys"
# Basic identity
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ${BUNDLE_ID}" "$PLIST" || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string ${BUNDLE_ID}" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleName ${APP_NAME}" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName ${APP_NAME}" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${VERSION}" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${BUILD_NUMBER}" "$PLIST"
# Category & minimum OS (guarded)
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.finance" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.0" "$PLIST" 2>/dev/null || true
# Sparkle settings
/usr/libexec/PlistBuddy -c "Add :SUEnableAutomaticChecks bool true" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :SUEnableAutomaticChecks true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :SUAllowsAutomaticUpdates bool true" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :SUAllowsAutomaticUpdates true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :SUFeedURL string ${FEED_URL}" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :SUFeedURL ${FEED_URL}" "$PLIST"
if [[ -n "${SPARKLE_PUBLIC_ED25519:-}" ]]; then
  /usr/libexec/PlistBuddy -c "Add :SUPublicEDKey string ${SPARKLE_PUBLIC_ED25519}" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :SUPublicEDKey ${SPARKLE_PUBLIC_ED25519}" "$PLIST"
fi
plutil -lint "$PLIST" >/dev/null

### ---------- sign + notarize + package ----------
OUT_DIR="${PROJECT_ROOT}/dist"
mkdir -p "$OUT_DIR"
DMG_PATH="${OUT_DIR}/Animica-Wallet-${VERSION}-macOS.dmg"

SIGN_ARGS=(
  --app "$APP_PATH"
  --identity "$IDENTITY"
  --entitlements "$ENTITLEMENTS"
  --out-dmg "$DMG_PATH"
)

# Build a PKG only if installer identity is provided
if [[ -n "$INSTALLER_IDENTITY" ]]; then
  PKG_PATH="${OUT_DIR}/Animica-Wallet-${VERSION}.pkg"
  SIGN_ARGS+=( --pkg-id "$BUNDLE_ID" --out-pkg "$PKG_PATH" --installer-identity "$INSTALLER_IDENTITY" )
else
  warn "No installer identity provided; skipping PKG packaging"
fi

log "Signing, packaging (DMG/PKG) and submitting for notarization"
"$SIGN_SCRIPT" "${SIGN_ARGS[@]}"

# Done
log "Artifacts ready in: $OUT_DIR"
ls -lh "$OUT_DIR" || true
