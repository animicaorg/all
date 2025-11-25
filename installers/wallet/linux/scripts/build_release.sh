#!/usr/bin/env bash
# Animica Wallet — Linux release build & packaging
# Formats: AppImage (primary), DEB, RPM, optional Flatpak
# Requirements:
#   - Flutter SDK in PATH
#   - linuxdeploy + appimagetool (for AppImage)   https://github.com/linuxdeploy/linuxdeploy
#   - fpm (for DEB/RPM)                            https://github.com/jordansissel/fpm
#   - flatpak-builder (optional, if manifest present)
#   - gpg (optional, for signing)
#
# Usage:
#   WALLET_VERSION=1.2.3 CHANNEL=stable ./installers/wallet/linux/scripts/build_release.sh
#   (env overrides below; sensible defaults if unset)

set -Eeuo pipefail

### ──────────────────────────── config (env overrides) ─────────────────────────
CHANNEL="${CHANNEL:-stable}"               # stable|beta|dev
APP_ID="${APP_ID:-io.animica.Wallet}"     # Flatpak/AppStream ID
APP_NAME="${APP_NAME:-Animica Wallet}"
APP_BIN_NAME="${APP_BIN_NAME:-AnimicaWallet}"  # Flutter binary name inside bundle
ORG_NAME="${ORG_NAME:-Animica Labs, Inc.}"
MAINTAINER="${MAINTAINER:-Animica Labs <support@animica.dev>}"
HOMEPAGE="${HOMEPAGE:-https://animica.dev/wallet}"
LICENSE_NAME="${LICENSE_NAME:-Proprietary}"

# If WALLET_VERSION unset, try git describe → 0.0.0+<hash>
WALLET_VERSION="${WALLET_VERSION:-}"
if [[ -z "${WALLET_VERSION}" ]]; then
  if git -C . rev-parse --git-dir >/dev/null 2>&1; then
    WALLET_VERSION="$(git describe --tags --abbrev=7 --dirty 2>/dev/null || true)"
  fi
  WALLET_VERSION="${WALLET_VERSION:-0.0.0+local}"
fi

# Optional signing (AppImage asc, Flatpak repo, DEB repo metadata out of scope here)
GPG_KEY_ID="${GPG_KEY_ID:-}"     # e.g. 0xDEADBEEFCAFEBABE
SIGN_APPIMAGE="${SIGN_APPIMAGE:-1}"

# Tooling (override to point to custom paths)
LINUXDEPLOY_BIN="${LINUXDEPLOY_BIN:-linuxdeploy}"
APPIMAGETOOL_BIN="${APPIMAGETOOL_BIN:-appimagetool}"
APPIMAGEUPDATE_TOOL="${APPIMAGEUPDATE_TOOL:-appimageupdate-tool}"
FPM_BIN="${FPM_BIN:-fpm}"
FLATPAK_MANIFEST="${FLATPAK_MANIFEST:-installers/wallet/linux/flatpak/io.animica.Wallet.yml}"

# Input desktop/icon defaults (will auto-generate if missing)
DESKTOP_SRC="${DESKTOP_SRC:-installers/wallet/linux/appimage/animica-wallet.desktop}"
ICONS_DIR_SRC="${ICONS_DIR_SRC:-installers/wallet/linux/icons}"

### ───────────────────────────────── helpers ──────────────────────────────────
log()  { printf "\033[1;34m[build]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn ]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Load optional env file
if [[ -f "installers/.env" ]]; then
  # shellcheck disable=SC1091
  source installers/.env
fi

ARCH_UNAME="$(uname -m)"
case "$ARCH_UNAME" in
  x86_64|amd64) ARCH="x86_64"; DEB_ARCH="amd64"; RPM_ARCH="x86_64" ;;
  aarch64|arm64) ARCH="aarch64"; DEB_ARCH="arm64"; RPM_ARCH="aarch64" ;;
  *) die "Unsupported arch: $ARCH_UNAME" ;;
esac

OUT_DIR="dist/linux/${CHANNEL}"
STAGE_DIR="$(mktemp -d -t animica-linux-stage-XXXXXX)"
APPDIR="$(mktemp -d -t animica-appdir-XXXXXX)"
trap 'rm -rf "$STAGE_DIR" "$APPDIR"' EXIT

### ───────────────────────────── Flutter build ────────────────────────────────
have flutter || die "Flutter not found in PATH."
log "Building Flutter Linux release (arch=$ARCH)…"
flutter --version || true
flutter build linux --release > /dev/null

BUNDLE_DIR="build/linux/${ARCH}/release/bundle"
[[ -d "$BUNDLE_DIR" ]] || die "Bundle not found at $BUNDLE_DIR"

# Detect executable
if [[ -x "${BUNDLE_DIR}/${APP_BIN_NAME}" ]]; then
  BIN_PATH="${BUNDLE_DIR}/${APP_BIN_NAME}"
else
  # fallback: first executable in bundle root
  BIN_CANDIDATE="$(find "$BUNDLE_DIR" -maxdepth 1 -type f -executable -printf '%f\n' | head -n1 || true)"
  [[ -n "$BIN_CANDIDATE" ]] || die "No executable found in bundle; set APP_BIN_NAME."
  BIN_PATH="${BUNDLE_DIR}/${BIN_CANDIDATE}"
  warn "APP_BIN_NAME (${APP_BIN_NAME}) not found; using detected executable: ${BIN_CANDIDATE}"
  APP_BIN_NAME="${BIN_CANDIDATE}"
fi

mkdir -p "$OUT_DIR"

### ────────────────────────────── AppImage build ──────────────────────────────
if have "$LINUXDEPLOY_BIN" && have "$APPIMAGETOOL_BIN"; then
  log "Packaging AppImage…"
  # Desktop entry (generate if not provided)
  DESKTOP_TMP="${STAGE_DIR}/animica-wallet.desktop"
  if [[ -f "$DESKTOP_SRC" ]]; then
    cp "$DESKTOP_SRC" "$DESKTOP_TMP"
  else
    cat > "$DESKTOP_TMP" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Comment=Post-quantum wallet for the Animica network
Exec=${APP_BIN_NAME} %U
Icon=animica-wallet
Terminal=false
Categories=Finance;Network;
MimeType=x-scheme-handler/animica;
EOF
  fi

  # Icons (copy if present)
  ICON_ROOT="${STAGE_DIR}/icons"
  if [[ -d "$ICONS_DIR_SRC" ]]; then
    cp -R "$ICONS_DIR_SRC" "$ICON_ROOT"
  else
    warn "No icon theme directory at ${ICONS_DIR_SRC}; AppImage will use fallback or none."
    mkdir -p "${ICON_ROOT}/hicolor/256x256/apps"
    # Optional: embed a tiny placeholder if you want; omitted to avoid ImageMagick dependency
  fi

  # Run linuxdeploy to assemble AppDir with dependencies
  "$LINUXDEPLOY_BIN" \
    --appdir "$APPDIR" \
    --executable "$BIN_PATH" \
    --desktop-file "$DESKTOP_TMP" \
    --icon-dir "$ICON_ROOT" \
    >/dev/null

  # Name & build
  APPIMAGE_NAME="Animica-Wallet_${WALLET_VERSION}_${ARCH}.AppImage"
  (cd "$OUT_DIR" && "$APPIMAGETOOL_BIN" "$APPDIR" "$APPIMAGE_NAME" >/dev/null)

  # Optional: zsync for delta updates
  if have "$APPIMAGEUPDATE_TOOL"; then
    (cd "$OUT_DIR" && "$APPIMAGEUPDATE_TOOL" --make-zsync "$APPIMAGE_NAME" >/dev/null || warn "zsync generation failed")
  fi

  # Optional sign
  if [[ -n "$GPG_KEY_ID" && "${SIGN_APPIMAGE}" = "1" ]] && have gpg; then
    log "Signing AppImage with GPG key ${GPG_KEY_ID}…"
    (cd "$OUT_DIR" && gpg --batch --yes --local-user "$GPG_KEY_ID" --armor --detach-sign "$APPIMAGE_NAME")
  else
    warn "Skipping AppImage signing (no GPG_KEY_ID or signing disabled)."
  fi
else
  warn "linuxdeploy/appimagetool not found; skipping AppImage build."
fi

### ─────────────────────────────── DEB / RPM via fpm ─────────────────────────
if have "$FPM_BIN"; then
  log "Packaging DEB/RPM with fpm…"
  # Stage filesystem layout
  # App payload → /opt/AnimicaWallet
  mkdir -p "${STAGE_DIR}/opt/AnimicaWallet"
  cp -a "${BUNDLE_DIR}/." "${STAGE_DIR}/opt/AnimicaWallet/"

  # Desktop & icons into shared paths
  mkdir -p "${STAGE_DIR}/usr/share/applications"
  cp "$DESKTOP_TMP" "${STAGE_DIR}/usr/share/applications/animica-wallet.desktop"

  if [[ -d "$ICON_ROOT" ]]; then
    mkdir -p "${STAGE_DIR}/usr/share/icons/hicolor"
    cp -a "${ICON_ROOT}/." "${STAGE_DIR}/usr/share/icons/hicolor/"
  fi

  # CLI convenience symlink
  mkdir -p "${STAGE_DIR}/usr/bin"
  ln -sf "/opt/AnimicaWallet/${APP_BIN_NAME}" "${STAGE_DIR}/usr/bin/animica-wallet"

  COMMON_FPM_ARGS=(
    -C "$STAGE_DIR"
    -s dir
    --name "animica-wallet"
    --version "$WALLET_VERSION"
    --maintainer "$MAINTAINER"
    --vendor "$ORG_NAME"
    --license "$LICENSE_NAME"
    --url "$HOMEPAGE"
    --description "Post-quantum wallet for the Animica network"
    --category "utils"
    --after-install <(cat <<'EOS'
#!/usr/bin/env bash
set -e
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q || true; fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then gtk-update-icon-cache -f /usr/share/icons/hicolor || true; fi
EOS
)
    --after-remove <(cat <<'EOS'
#!/usr/bin/env bash
set -e
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q || true; fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then gtk-update-icon-cache -f /usr/share/icons/hicolor || true; fi
EOS
)
  )

  # DEB
  "$FPM_BIN" "${COMMON_FPM_ARGS[@]}" \
    -t deb \
    --architecture "$DEB_ARCH" \
    --deb-no-default-config-files \
    -p "${OUT_DIR}/animica-wallet_${WALLET_VERSION}_${DEB_ARCH}.deb" \
    . >/dev/null

  # RPM
  "$FPM_BIN" "${COMMON_FPM_ARGS[@]}" \
    -t rpm \
    --rpm-os linux \
    --architecture "$RPM_ARCH" \
    -p "${OUT_DIR}/animica-wallet-${WALLET_VERSION}-1.${RPM_ARCH}.rpm" \
    . >/dev/null
else
  warn "fpm not found; skipping DEB/RPM packaging."
fi

### ─────────────────────────────── Flatpak (optional) ─────────────────────────
if [[ -f "$FLATPAK_MANIFEST" ]]; then
  if have flatpak-builder; then
    log "Building Flatpak (manifest: $FLATPAK_MANIFEST)…"
    FLAT_BUILD_DIR="${STAGE_DIR}/flatpak-build"
    FLAT_REPO_DIR="${OUT_DIR}/flatpak-repo"
    rm -rf "$FLAT_BUILD_DIR" "$FLAT_REPO_DIR"
    flatpak-builder --force-clean "$FLAT_BUILD_DIR" "$FLATPAK_MANIFEST" >/dev/null
    flatpak build-export ${GPG_KEY_ID:+--gpg-sign="$GPG_KEY_ID"} "$FLAT_REPO_DIR" "$FLAT_BUILD_DIR" >/dev/null
    log "Flatpak repo exported to: $FLAT_REPO_DIR"
  else
    warn "flatpak-builder not found; skipping Flatpak."
  fi
else
  log "No Flatpak manifest found at ${FLATPAK_MANIFEST}; skipping."
fi

### ─────────────────────────────── summary ────────────────────────────────────
log "Build complete."
log "Artifacts in: ${OUT_DIR}"
ls -lh "${OUT_DIR}" || true

cat > "${OUT_DIR}/BUILD_SUMMARY.txt" <<EOF
Animica Wallet — Linux Build Summary
Channel:   ${CHANNEL}
Version:   ${WALLET_VERSION}
Arch:      ${ARCH}
Date:      $(date -Is)
Repo:      $(git rev-parse --short HEAD 2>/dev/null || echo n/a)

Artifacts:
$(ls -1 "${OUT_DIR}" | sed 's/^/  - /')
EOF

log "Wrote ${OUT_DIR}/BUILD_SUMMARY.txt"
