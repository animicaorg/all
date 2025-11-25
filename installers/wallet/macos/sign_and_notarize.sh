#!/usr/bin/env bash
# Animica Wallet — macOS sign → notarize → staple helper
# Usage:
#   ./sign_and_notarize.sh \
#     --app "build/macos/Build/Products/Release/Animica Wallet.app" \
#     --identity "Developer ID Application: Animica Labs (ABCDE12345)" \
#     [--entitlements installers/wallet/macos/entitlements.plist] \
#     [--out-dmg "dist/Animica-Wallet-1.2.3-macOS.dmg"] \
#     [--pkg-id com.animica.wallet] [--out-pkg dist/Animica-Wallet-1.2.3.pkg] \
#     [--installer-identity "Developer ID Installer: Animica Labs (ABCDE12345)"] \
#     [--asc-key-file asc_key.p8]  # or provide ASC_API_KEY_P8_BASE64 env
#
# Required env for notarization (if --asc-key-file not provided):
#   ASC_API_KEY_P8_BASE64, ASC_KEY_ID, ASC_ISSUER_ID
#
# Optional env:
#   APPLE_TEAM_ID (used by post-verify script)
#   CODE_SIGN_IDENTITY_NAME (fallback for --identity)
#   NOTARY_ARTIFACT_PATH (overrides auto artifact selection)
#
# Notes:
# - Performs deep codesign with hardened runtime.
# - Can package .app into a DMG (preferred) and/or build+sign a PKG.
# - Submits artifact to Apple notarization and staples the ticket.
set -euo pipefail

### -------- helpers --------
log() { printf "\033[1;34m[sign]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[err]\033[0m %s\n" "$*" >&2; }
die() { err "$*"; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "Missing required tool: $1"; }

### -------- defaults --------
APP_PATH=""
ENTITLEMENTS=""
IDENTITY="${CODE_SIGN_IDENTITY_NAME:-}"
OUT_DMG=""
OUT_PKG=""
PKG_ID=""
INSTALLER_IDENTITY=""
ASC_KEY_FILE=""
TMP_ASC_KEY=""
MAKE_DMG=0
MAKE_PKG=0

### -------- parse args --------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP_PATH="$2"; shift 2;;
    --entitlements) ENTITLEMENTS="$2"; shift 2;;
    --identity) IDENTITY="$2"; shift 2;;
    --out-dmg) OUT_DMG="$2"; MAKE_DMG=1; shift 2;;
    --out-pkg) OUT_PKG="$2"; MAKE_PKG=1; shift 2;;
    --pkg-id) PKG_ID="$2"; shift 2;;
    --installer-identity) INSTALLER_IDENTITY="$2"; shift 2;;
    --asc-key-file) ASC_KEY_FILE="$2"; shift 2;;
    -h|--help)
      sed -n '1,70p' "$0"; exit 0;;
    *) die "Unknown arg: $1";;
  esac
done

### -------- sanity checks --------
need xcrun
need codesign
need plutil
need hdiutil

[[ -n "${APP_PATH}" ]] || die "--app is required"
[[ -d "${APP_PATH}" ]] || die "App bundle not found: ${APP_PATH}"
[[ -n "${IDENTITY}" ]] || die "--identity (Developer ID Application) is required (or set CODE_SIGN_IDENTITY_NAME)"
if [[ ${MAKE_PKG} -eq 1 ]]; then
  need pkgbuild
  need productsign
  [[ -n "${PKG_ID}" ]] || die "--pkg-id is required for PKG"
  [[ -n "${OUT_PKG}" ]] || die "--out-pkg is required when building PKG"
  [[ -n "${INSTALLER_IDENTITY}" ]] || die "--installer-identity (Developer ID Installer) is required"
fi
if [[ ${MAKE_DMG} -eq 1 && -z "${OUT_DMG}" ]]; then
  OUT_DMG="dist/$(basename "${APP_PATH%.*}")-macOS.dmg"
fi

mkdir -p dist

### -------- decode ASC key if needed --------
cleanup_key() { [[ -n "${TMP_ASC_KEY}" && -f "${TMP_ASC_KEY}" ]] && rm -f "${TMP_ASC_KEY}"; }
trap cleanup_key EXIT

if [[ -z "${ASC_KEY_FILE}" ]]; then
  if [[ -n "${ASC_API_KEY_P8_BASE64:-}" ]]; then
    TMP_ASC_KEY="$(mktemp -t asc_key.XXXXXX.p8)"
    echo "$ASC_API_KEY_P8_BASE64" | base64 --decode > "${TMP_ASC_KEY}"
    ASC_KEY_FILE="${TMP_ASC_KEY}"
  fi
fi

### -------- step 1: deep sign the .app --------
log "Signing app bundle with hardened runtime"
SIGN_ARGS=(--force --deep --options runtime --timestamp --sign "${IDENTITY}")
if [[ -n "${ENTITLEMENTS}" ]]; then
  [[ -f "${ENTITLEMENTS}" ]] || die "Entitlements not found: ${ENTITLEMENTS}"
  SIGN_ARGS+=(--entitlements "${ENTITLEMENTS}")
fi

codesign "${SIGN_ARGS[@]}" "${APP_PATH}"

log "Verifying code signature"
codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
spctl -a -vv "${APP_PATH}" || true

### -------- step 2a: DMG (optional) --------
ARTIFACT_PATH=""
if [[ ${MAKE_DMG} -eq 1 ]]; then
  log "Building DMG → ${OUT_DMG}"
  mkdir -p "$(dirname "${OUT_DMG}")"
  hdiutil create -volname "$(basename "${APP_PATH%.*}")" \
    -srcfolder "${APP_PATH}" -ov -format UDZO "${OUT_DMG}"

  log "Signing DMG"
  codesign --force --options runtime --timestamp --sign "${IDENTITY}" "${OUT_DMG}"

  ARTIFACT_PATH="${OUT_DMG}"
fi

### -------- step 2b: PKG (optional) --------
if [[ ${MAKE_PKG} -eq 1 ]]; then
  COMP_PKG="dist/$(basename "${OUT_PKG%.pkg}").component.pkg"
  log "Building component PKG → ${COMP_PKG}"
  pkgbuild --component "${APP_PATH}" \
    --install-location "/Applications/$(basename "${APP_PATH}")" \
    --identifier "${PKG_ID}" \
    --version "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${APP_PATH}/Contents/Info.plist")" \
    "${COMP_PKG}"

  log "Product signing → ${OUT_PKG}"
  productsign --sign "${INSTALLER_IDENTITY}" "${COMP_PKG}" "${OUT_PKG}"

  ARTIFACT_PATH="${OUT_PKG}"
fi

### -------- artifact selection for notarization --------
if [[ -n "${NOTARY_ARTIFACT_PATH:-}" ]]; then
  ARTIFACT_PATH="${NOTARY_ARTIFACT_PATH}"
fi
if [[ -z "${ARTIFACT_PATH}" ]]; then
  # If neither DMG nor PKG requested, notarize .app directly (less common)
  ARTIFACT_PATH="${APP_PATH}"
fi

[[ -e "${ARTIFACT_PATH}" ]] || die "Artifact not found for notarization: ${ARTIFACT_PATH}"

### -------- step 3: notarize --------
if [[ -n "${ASC_KEY_FILE}" && -n "${ASC_KEY_ID:-}" && -n "${ASC_ISSUER_ID:-}" ]]; then
  log "Submitting to Apple notarization (notarytool) and waiting for result"
  xcrun notarytool submit "${ARTIFACT_PATH}" \
    --key "${ASC_KEY_FILE}" \
    --key-id "${ASC_KEY_ID}" \
    --issuer "${ASC_ISSUER_ID}" \
    --wait
else
  log "Notarization credentials not set; skipping notarization step."
  log "Set ASC_API_KEY_P8_BASE64 or provide --asc-key-file and export ASC_KEY_ID, ASC_ISSUER_ID"
fi

### -------- step 4: staple (if DMG/PKG) --------
if [[ "${ARTIFACT_PATH}" == *.dmg || "${ARTIFACT_PATH}" == *.pkg ]]; then
  log "Stapling notarization ticket → ${ARTIFACT_PATH}"
  xcrun stapler staple "${ARTIFACT_PATH}" || true
fi

### -------- final verification --------
if [[ -f "installers/scripts/verify_signatures.sh" ]]; then
  log "Running post verification script"
  installers/scripts/verify_signatures.sh "${ARTIFACT_PATH}" || true
fi

log "Done ✔  Artifact: ${ARTIFACT_PATH}"
