#!/usr/bin/env bash
# Build the macOS Animica Miner GUI binary.
#
# Produces a PyInstaller onedir build wrapped in "AnimicaMiner.app" (via the
# BUNDLE() step in the shared spec), then packages it as a compressed DMG.
# A .zip of the .app is also produced as a portable fallback.
#
# For every artifact it computes SHA256 + size and appends a single JSON line
# to the per-artifact manifest log so make_manifest.py can aggregate them.
#
# IMPORTANT: macOS apps can ONLY be built on macOS (the Apple toolchain cannot
# be cross-compiled from Linux). This script therefore runs on the macOS CI
# runner in the release workflow.
#
# Usage:
#   ./build_macos.sh
#
# Env overrides:
#   ARTIFACT_LOG     path to the JSON-lines manifest log (default: dist/artifacts.jsonl)
#   ANIMICA_GUI_UPX  "1" to enable UPX (default off; UPX is unreliable on arm64)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/miner-gui
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"        # repo root
DIST_DIR="${ANIMICA_GUI_DIST_DIR:-${APP_DIR}/dist}"
BUILD_DIR="${ANIMICA_GUI_BUILD_DIR:-${APP_DIR}/build}"
SPEC_FILE="${SCRIPT_DIR}/animica-miner-gui.spec"
PYI_WORK="${BUILD_DIR}/pyinstaller-work"
ARTIFACT_LOG="${ARTIFACT_LOG:-${DIST_DIR}/artifacts.jsonl}"
APP_NAME="AnimicaMiner"

log()  { printf "\033[1;34m[build-macos]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "This script must be run on macOS (Apple toolchain cannot be cross-compiled)"
command -v hdiutil >/dev/null 2>&1 || die "hdiutil not found (macOS tool)"

ARCH="$(uname -m)"   # arm64 | x86_64

# ---- Python (prefer venv) ----
choose_python() {
    if [[ -n "${VIRTUAL_ENV:-}" ]] && [[ -x "${VIRTUAL_ENV}/bin/python3" ]]; then
        echo "${VIRTUAL_ENV}/bin/python3"; return
    fi
    if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
        echo "${REPO_ROOT}/.venv/bin/python3"; return
    fi
    command -v python3 >/dev/null 2>&1 || return 1
    command -v python3
}
PY="$(choose_python)" || die "Python 3 not found"
PY_VERSION="$("$PY" --version 2>&1 | awk '{print $2}')"
log "Using Python $PY_VERSION ($PY) on $ARCH"

# ---- Clean ----
log "Cleaning previous builds..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR" "$PYI_WORK"

# ---- Tooling + deps ----
log "Installing PyInstaller + GUI package..."
"$PY" -m pip install --upgrade pip setuptools wheel
"$PY" -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
"$PY" -m pip install -e "$APP_DIR"
if [[ -f "$REPO_ROOT/python/pyproject.toml" ]]; then
    "$PY" -m pip install -e "$REPO_ROOT/python" || warn "could not install animica package; bundle may be incomplete"
fi

# ---- Bundled animica version (hard-fails on drift) ----
BUNDLED_ANIMICA="$("$PY" "$SCRIPT_DIR/bundle_version.py")" || die "bundled animica version check failed"
log "Bundled animica: $BUNDLED_ANIMICA"

# ---- Version ----
VERSION="$("$PY" "$SCRIPT_DIR/gui_version.py")" || die "could not read the GUI version from pyproject.toml"
log "Building version: $VERSION"

# ---- PyInstaller ----
log "Running PyInstaller (onedir -> .app, windowed)..."
export ANIMICA_GUI_SPEC_DIR="$SCRIPT_DIR"
export ANIMICA_GUI_APP_DIR="$APP_DIR"
export ANIMICA_GUI_REPO_ROOT="$REPO_ROOT"
export ANIMICA_GUI_NAME="$APP_NAME"
export ANIMICA_GUI_VERSION="$VERSION"
cd "$APP_DIR"
"$PY" -m PyInstaller --clean --noconfirm \
    --distpath "$DIST_DIR" \
    --workpath "$PYI_WORK" \
    "$SPEC_FILE"

APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
    FOUND="$(find "$DIST_DIR" -maxdepth 3 -name "${APP_NAME}.app" -type d -print -quit || true)"
    [[ -n "$FOUND" ]] && APP_BUNDLE="$FOUND" || die "Failed to create .app bundle in $DIST_DIR"
fi
log ".app bundle created: $APP_BUNDLE"

# ---- Info.plist minimum-OS floor ----
# Hardcoding LSMinimumSystemVersion is a trap: Launch Services trusts it, so a
# too-low value lets macOS start the app and then dyld kills it while loading
# Qt frameworks built for a newer OS ("building for macOS X, which is newer
# than running OS") — before Python runs, so nothing is logged. Read the real
# floor off the bundled QtCore and write that instead.
QTCORE="$(find "$APP_BUNDLE/Contents" -name 'QtCore' -type f -path '*QtCore.framework*' -print -quit 2>/dev/null || true)"
if [[ -n "$QTCORE" ]] && command -v otool >/dev/null 2>&1; then
    QT_MIN_OS="$(otool -l "$QTCORE" | awk '/LC_BUILD_VERSION/{f=1} f&&/minos/{print $2; exit}')"
    if [[ -n "${QT_MIN_OS:-}" ]]; then
        log "Bundled Qt minimum OS: $QT_MIN_OS (was declaring $(defaults read "$APP_BUNDLE/Contents/Info" LSMinimumSystemVersion 2>/dev/null || echo '?'))"
        /usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $QT_MIN_OS" \
            "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null \
            || /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string $QT_MIN_OS" \
               "$APP_BUNDLE/Contents/Info.plist"
        # Editing the plist invalidates the ad-hoc signature PyInstaller applied.
        codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null 2>&1 \
            || warn "re-signing after Info.plist edit failed"
    else
        warn "could not read minos from $QTCORE; leaving LSMinimumSystemVersion as-is"
    fi
else
    warn "QtCore not found in bundle; leaving LSMinimumSystemVersion as-is"
fi
# Whatever happened above, advertise exactly what the plist says. Deriving the
# download-page string separately is how you end up promising macOS 11 while
# the bundle refuses to launch below 13.
if [[ -z "${QT_MIN_OS:-}" ]]; then
    QT_MIN_OS="$(defaults read "$APP_BUNDLE/Contents/Info" LSMinimumSystemVersion 2>/dev/null || echo "11.0")"
    warn "falling back to the plist value for the advertised minimum OS: $QT_MIN_OS"
fi

# ---- Developer ID signing (optional; ad-hoc otherwise) ----
#
# An ad-hoc signature is enough for arm64 macOS to *exec* the binary locally,
# but Gatekeeper refuses to run a quarantined (i.e. downloaded) app that is not
# both Developer ID signed and notarized — the user gets "Apple could not
# verify ... is free of malware" with no obvious way forward, and macOS 15
# removed the old right-click→Open bypass.
#
# Set APPLE_SIGNING_IDENTITY to a "Developer ID Application: ..." identity in
# the keychain to sign properly. Without it the build still succeeds, ad-hoc,
# and says so loudly rather than pretending it is distributable.
SIGNED_WITH_DEVELOPER_ID=false
if [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]] && command -v codesign >/dev/null 2>&1; then
    ENTITLEMENTS="${SCRIPT_DIR}/entitlements.plist"
    [[ -f "$ENTITLEMENTS" ]] || die "entitlements.plist not found at $ENTITLEMENTS"
    log "Signing with Developer ID: $APPLE_SIGNING_IDENTITY"
    # --options runtime enables the hardened runtime, which notarization requires.
    codesign --force --deep --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" \
        --sign "$APPLE_SIGNING_IDENTITY" "$APP_BUNDLE" \
        || die "Developer ID signing failed"
    SIGNED_WITH_DEVELOPER_ID=true
    log "Developer ID signature applied (hardened runtime)."
else
    log "No APPLE_SIGNING_IDENTITY set — shipping an ad-hoc signature."
fi

# A release build must not quietly produce something Gatekeeper will block.
# CI sets ANIMICA_REQUIRE_SIGNED=1 for tag pushes.
if [[ "${ANIMICA_REQUIRE_SIGNED:-0}" == "1" && "$SIGNED_WITH_DEVELOPER_ID" != "true" ]]; then
    die "ANIMICA_REQUIRE_SIGNED=1 but no Developer ID signature was applied; \
refusing to publish artifacts Gatekeeper will reject"
fi

# The bundle must carry a valid signature or arm64 macOS refuses to exec it.
if command -v codesign >/dev/null 2>&1; then
    # --deep on *verification* matters: `codesign --deep --sign` is a
    # best-effort convenience Apple discourages for nested code, and a shallow
    # verify would happily pass a bundle with an unsigned nested dylib that
    # notarization then rejects. Verify the whole tree.
    codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE" \
        || die "bundle signature is invalid (nested code may be unsigned)"
    if [[ "$SIGNED_WITH_DEVELOPER_ID" == "true" ]]; then
        log "Signature verified (Developer ID)."
    else
        warn "Signature verified but AD-HOC and NOT NOTARIZED — Gatekeeper will"
        warn "block this build for anyone who downloads it. Set"
        warn "APPLE_SIGNING_IDENTITY + notarization credentials to fix."
    fi
fi

# ---- Launch gate ----
# Deliberately AFTER signing. The hardened runtime + entitlements are what ship,
# and they are precisely what can break a PyInstaller/PyTorch app at runtime
# (blocked dlopen, refused JIT). Smoke-testing the ad-hoc build would prove
# nothing about the artifact users actually get.
log "Smoke-testing the frozen app (post-signing)..."
if ! QT_QPA_PLATFORM=offscreen "$APP_BUNDLE/Contents/MacOS/$APP_NAME" --smoke-test; then
    die "frozen app failed --smoke-test after signing; refusing to package it"
fi
log "Smoke test passed."

# ---- Artifact-record helper (SHA256 + size + JSON line) ----
emit_artifact() {
    # $1 = file path, $2 = platform tag, $3 = min_os
    local file="$1" platform="$2" min_os="$3"
    local name size sha
    name="$(basename "$file")"
    size="$(stat -f %z "$file" 2>/dev/null || stat -c %s "$file")"
    sha="$(shasum -a 256 "$file" | awk '{print $1}')"
    "$PY" - "$ARTIFACT_LOG" "$platform" "$name" "$VERSION" "$size" "$sha" "$min_os" "$BUNDLED_ANIMICA" <<'PYEOF'
import json, sys
log, platform, name, version, size, sha, min_os, bundled = sys.argv[1:9]
rec = {
    "platform": platform,
    "name": "AnimicaMiner",
    "version": version,
    "filename": name,
    "size_bytes": int(size),
    "sha256": sha,
    "min_os": min_os,
    "bundled_animica": bundled,
}
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec) + "\n")
print(f"[artifact] {platform} {name} {size} bytes sha256={sha}")
PYEOF
}

# ---- ZIP of the .app (portable fallback) ----
ZIP_NAME="AnimicaMiner-${VERSION}-macos-${ARCH}.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"
log "Creating .app zip..."
( cd "$DIST_DIR" && ditto -c -k --sequesterRsrc --keepParent "${APP_NAME}.app" "$ZIP_NAME" )
log "Zip: $ZIP_PATH"

# ---- DMG ----
DMG_NAME="AnimicaMiner-${VERSION}-macos-${ARCH}.dmg"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"
log "Creating DMG installer..."
hdiutil create -volname "Animica Miner" \
    -srcfolder "$APP_BUNDLE" \
    -ov -format UDZO \
    "$DMG_PATH"
log "DMG: $DMG_PATH"

# ---- Notarization (optional) ----
#
# Must run BEFORE the artifacts are recorded: stapling rewrites the files, so
# hashing them first would publish checksums that no longer match what users
# download.
#
# Credentials, either form:
#   App Store Connect API key: APPLE_NOTARY_KEY_P8 (path) + APPLE_NOTARY_KEY_ID
#                              + APPLE_NOTARY_ISSUER_ID
#   Apple ID:                  APPLE_ID + APPLE_APP_PASSWORD + APPLE_TEAM_ID
NOTARY_ARGS=()
if [[ -n "${APPLE_NOTARY_KEY_P8:-}" && -n "${APPLE_NOTARY_KEY_ID:-}" && -n "${APPLE_NOTARY_ISSUER_ID:-}" ]]; then
    NOTARY_ARGS=(--key "$APPLE_NOTARY_KEY_P8" --key-id "$APPLE_NOTARY_KEY_ID" --issuer "$APPLE_NOTARY_ISSUER_ID")
elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" && -n "${APPLE_TEAM_ID:-}" ]]; then
    NOTARY_ARGS=(--apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" --team-id "$APPLE_TEAM_ID")
fi

# Submit and require status == Accepted. `notarytool submit --wait` exits 0 for
# a submission that completed but was REJECTED, so trusting the exit code alone
# would staple nothing and ship an unnotarized DMG believing it was fine.
notarize_and_check() {
    local target="$1" out status
    out="$(xcrun notarytool submit "$target" --wait --timeout 30m \
             --output-format json "${NOTARY_ARGS[@]}")" || {
        err "notarytool submit failed to run"
        printf '%s\n' "$out" >&2
        return 1
    }
    status="$("$PY" -c "import json,sys; print(json.loads(sys.stdin.read()).get('status',''))" <<<"$out" 2>/dev/null || true)"
    log "notarytool status: ${status:-unknown}"
    if [[ "$status" != "Accepted" ]]; then
        local sub_id
        sub_id="$("$PY" -c "import json,sys; print(json.loads(sys.stdin.read()).get('id',''))" <<<"$out" 2>/dev/null || true)"
        err "notarization was not accepted (status=${status:-unknown})"
        [[ -n "$sub_id" ]] && xcrun notarytool log "$sub_id" "${NOTARY_ARGS[@]}" >&2 || true
        return 1
    fi
    return 0
}

if [[ "$SIGNED_WITH_DEVELOPER_ID" != "true" ]]; then
    warn "Skipping notarization: the app is not Developer ID signed."
elif [[ ${#NOTARY_ARGS[@]} -eq 0 ]]; then
    warn "No notarization credentials set; skipping. The DMG is signed but NOT"
    warn "notarized, so Gatekeeper will still warn on download."
elif ! command -v xcrun >/dev/null 2>&1; then
    warn "xcrun not available; cannot notarize. The DMG is signed but NOT notarized."
else
    # stapler attaches the ticket to a *signed* container; hdiutil output is
    # unsigned, so sign the DMG before submitting it.
    log "Signing the DMG..."
    codesign --force --timestamp --sign "$APPLE_SIGNING_IDENTITY" "$DMG_PATH" \
        || die "signing the DMG failed"

    log "Submitting DMG for notarization (this can take several minutes)..."
    notarize_and_check "$DMG_PATH" || die "notarization failed"

    xcrun stapler staple "$DMG_PATH" || die "stapling the DMG failed"
    # Staple the .app too and rebuild the zip from it: a stapled app validates
    # offline, an unstapled one needs Apple reachable on first launch.
    if xcrun stapler staple "$APP_BUNDLE"; then
        log "Re-creating the zip so it contains the stapled app..."
        rm -f "$ZIP_PATH"
        ( cd "$DIST_DIR" && ditto -c -k --sequesterRsrc --keepParent "${APP_NAME}.app" "$ZIP_NAME" )
    else
        die "stapling the .app failed; refusing to ship a zip that claims to be notarized"
    fi
    xcrun stapler validate "$DMG_PATH" || die "stapler validate failed on the DMG"
    log "Notarized, stapled and validated."
fi

# ---- Record artifacts ----
# Apple silicon only — Intel/x86_64 macs are deliberately not built. Report the
# floor actually enforced by the bundled Qt (detected above), not a guess: this
# string is what the download page shows users, and promising a version the
# binary cannot run on sends them to a crash.
MACOS_MIN_OS="macOS ${QT_MIN_OS:-11.0}+"
[[ "$ARCH" == "arm64" ]] && MACOS_MIN_OS="$MACOS_MIN_OS (Apple silicon)"
emit_artifact "$DMG_PATH" "macos" "$MACOS_MIN_OS"
emit_artifact "$ZIP_PATH" "macos" "$MACOS_MIN_OS"

log "Build complete. Artifacts in: $DIST_DIR"
log "Manifest log: $ARTIFACT_LOG"
log "Test: open \"$APP_BUNDLE\""
