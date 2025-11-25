#!/usr/bin/env bash
# Animica Explorer — macOS sign & notarize helper
# Usage:
#   ./sign_and_notarize.sh [-a /path/to/Animica\ Explorer.app] [-d /path/to/Animica-Explorer.dmg]
#                          [--identity "Developer ID Application: Your Org (TEAMID)"]
#                          [--entitlements /path/to/entitlements.plist]
#                          [--notary-profile PROFILE]  # xcrun notarytool keychain profile name
#                          [--issuer ISSUER --key-id KEYID --key P8_OR_JSON]  # ASC API key creds
#
# Environment alternatives:
#   CODESIGN_IDENTITY          Developer ID identity (if not passed via --identity)
#   ENTITLEMENTS               Path to entitlements (if not passed via --entitlements)
#   NOTARY_PROFILE             notarytool keychain profile (if not passed via --notary-profile)
#   ASC_ISSUER / ASC_KEY_ID / ASC_KEY
#   APPLE_ID / TEAM_ID / APP_PASSWORD  (App-specific password; least preferred)
#
# Notes:
#   - Tauri already signs bundles; this script can re-sign (force) & verify before notarization.
#   - It will staple the ticket to the .app and (if provided) the .dmg.
#   - Requires Xcode command line tools (codesign, notarytool, stapler).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TAURI_DIR="${ROOT_DIR}/tauri"

info()  { printf "\033[1;34m[i]\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m[!]\033[0m %s\n" "$*"; }
fail()  { printf "\033[1;31m[x]\033[0m %s\n" "$*"; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || fail "Missing tool: $1"; }

# --- Defaults & CLI parsing ----------------------------------------------------
APP_PATH=""
DMG_PATH=""
IDENTITY="${CODESIGN_IDENTITY:-}"
ENTITLEMENTS_PATH="${ENTITLEMENTS:-${SCRIPT_DIR}/entitlements.plist}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

ASC_ISSUER="${ASC_ISSUER:-}"
ASC_KEY_ID="${ASC_KEY_ID:-}"
ASC_KEY="${ASC_KEY:-}"

APPLE_ID="${APPLE_ID:-}"
TEAM_ID="${TEAM_ID:-}"
APP_PASSWORD="${APP_PASSWORD:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--app)            APP_PATH="$2"; shift 2 ;;
    -d|--dmg)            DMG_PATH="$2"; shift 2 ;;
    --identity)          IDENTITY="$2"; shift 2 ;;
    --entitlements)      ENTITLEMENTS_PATH="$2"; shift 2 ;;
    --notary-profile)    NOTARY_PROFILE="$2"; shift 2 ;;
    --issuer)            ASC_ISSUER="$2"; shift 2 ;;
    --key-id)            ASC_KEY_ID="$2"; shift 2 ;;
    --key)               ASC_KEY="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,80p' "$0"; exit 0 ;;
    *)
      fail "Unknown argument: $1" ;;
  esac
done

# Try to auto-locate the .app if not provided.
autodetect_app() {
  local search_dir="${TAURI_DIR}/target/release/bundle/macos"
  if [[ -d "$search_dir" ]]; then
    local latest
    latest="$(find "$search_dir" -maxdepth 1 -name '*.app' -type d -print0 | xargs -0 ls -t | head -n 1 || true)"
    if [[ -n "$latest" ]]; then
      echo "$latest"
      return 0
    fi
  fi
  return 1
}

if [[ -z "$APP_PATH" ]]; then
  if APP_PATH="$(autodetect_app)"; then
    info "Auto-detected app: $APP_PATH"
  else
    fail "Could not autodetect .app. Pass --app /path/to/app.bundle"
  fi
fi

[[ -d "$APP_PATH" ]] || fail "App bundle not found: $APP_PATH"

# DMG is optional; will staple if present.
if [[ -n "$DMG_PATH" && ! -f "$DMG_PATH" ]]; then
  fail "DMG not found: $DMG_PATH"
fi

[[ -f "$ENTITLEMENTS_PATH" ]] || fail "Entitlements not found: $ENTITLEMENTS_PATH"

# Identity is required for (re)sign.
[[ -n "$IDENTITY" ]] || fail "Set --identity or CODESIGN_IDENTITY (Developer ID Application: … (TEAMID))"

# --- Tooling checks ------------------------------------------------------------
need /usr/bin/codesign
need /usr/bin/xcrun
need /usr/bin/ditto
need /usr/bin/plutil || true  # optional
# notarytool & stapler are provided by Xcode CLI tools
xcrun notarytool -h >/dev/null || fail "xcrun notarytool unavailable. Install Xcode CLT."
xcrun stapler -h >/dev/null   || fail "xcrun stapler unavailable. Install Xcode CLT."

# --- Re-sign the app (force) with Hardened Runtime ----------------------------
info "Re-signing bundle with Hardened Runtime…"
# Sign inner helpers first (Frameworks, Helpers) if present.
sign_one() {
  local target="$1"
  /usr/bin/codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS_PATH" \
    --sign "$IDENTITY" "$target"
}

# Sign nested Frameworks / dylibs / apps / helpers if present
if [[ -d "$APP_PATH/Contents/Frameworks" ]]; then
  while IFS= read -r -d '' f; do
    sign_one "$f"
  done < <(find "$APP_PATH/Contents/Frameworks" -type f \( -name '*.dylib' -o -perm -111 \) -print0)
fi

if [[ -d "$APP_PATH/Contents/MacOS" ]]; then
  while IFS= read -r -d '' exe; do
    sign_one "$exe"
  done < <(find "$APP_PATH/Contents/MacOS" -type f -perm -111 -print0)
fi

# Finally sign the .app bundle
sign_one "$APP_PATH"

# Verify signature
info "Verifying code signature…"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_PATH"

# --- Create ZIP for notarization ----------------------------------------------
WORK_DIR="$(mktemp -d -t animica-notary-XXXXXX)"
ZIP_PATH="${WORK_DIR}/$(basename "$APP_PATH").zip"
trap 'rm -rf "$WORK_DIR"' EXIT

info "Creating notarization ZIP at: $ZIP_PATH"
/usr/bin/ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

# --- Submit for notarization ---------------------------------------------------
info "Submitting to Apple notary service…"
NOTARY_ARGS=(submit "$ZIP_PATH" "--wait")

# Prefer keychain profile
if [[ -n "$NOTARY_PROFILE" ]]; then
  NOTARY_ARGS+=("--keychain-profile" "$NOTARY_PROFILE")
elif [[ -n "$ASC_ISSUER" && -n "$ASC_KEY_ID" && -n "$ASC_KEY" ]]; then
  # App Store Connect API key mode
  NOTARY_ARGS+=("--issuer" "$ASC_ISSUER" "--key-id" "$ASC_KEY_ID" "--key" "$ASC_KEY")
elif [[ -n "$APPLE_ID" && -n "$TEAM_ID" && -n "$APP_PASSWORD" ]]; then
  # Legacy Apple ID app-specific password mode
  NOTARY_ARGS+=("--apple-id" "$APPLE_ID" "--team-id" "$TEAM_ID" "--password" "$APP_PASSWORD")
else
  warn "No NOTARY_PROFILE or ASC_* / APPLE_ID creds provided."
  warn "Attempting to use a default notarytool keychain profile named 'animica-notary'…"
  NOTARY_ARGS+=("--keychain-profile" "animica-notary")
fi

# Execute notarization (will block until complete)
/usr/bin/xcrun notarytool "${NOTARY_ARGS[@]}"

# --- Staple ticket -------------------------------------------------------------
info "Stapling notarization ticket to app…"
/usr/bin/xcrun stapler staple -v "$APP_PATH"

if [[ -n "${DMG_PATH:-}" ]]; then
  info "Stapling notarization ticket to DMG…"
/usr/bin/xcrun stapler staple -v "$DMG_PATH" || warn "Stapling DMG failed (continuing)."
fi

# --- Final verification --------------------------------------------------------
info "Gatekeeper assessment…"
/usr/sbin/spctl -a -vv --type exec "$APP_PATH" || warn "spctl reported warnings."

info "Stapler validate…"
/usr/bin/xcrun stapler validate -v "$APP_PATH" || warn "Stapler validate reported warnings."

info "✅ Sign & notarize complete."
printf "\nApp: %s\n" "$APP_PATH"
if [[ -n "${DMG_PATH:-}" ]]; then
  printf "DMG: %s\n" "$DMG_PATH"
fi
