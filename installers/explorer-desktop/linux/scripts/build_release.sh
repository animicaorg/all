#!/usr/bin/env bash
# Animica Explorer — Linux Release Builder for Tauri desktop shell
# Packages AppImage/DEB/RPM via Tauri bundler, collects artifacts, hashes, and optional GPG signatures.
#
# Usage:
#   ./installers/explorer-desktop/linux/scripts/build_release.sh
#
# Environment overrides:
#   CHANNEL=stable|beta|dev            # default: stable (used only for output paths/names)
#   TARGETS="x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu"
#   OUTDIR="./dist/explorer-linux"     # final artifacts root
#   SIGN_APPIMAGE=0|1                  # sign AppImage with GPG (default: 0)
#   SIGN_PACKAGES=0|1                  # sign .deb/.rpm via GPG detached sig (default: 0)
#   GPG_KEY_ID="KEYID"                 # required if SIGN_* = 1
#   GPG_SIGN_ARGS="--pinentry-mode loopback"  # extra args (set passphrase handling here if needed)
#   TAURI_BUILD_IGNORE_GLOBALS=1       # typical tauri env passthrough; leave as-is
#
# Prereqs:
#   - rustc, cargo, and tauri-cli (`cargo tauri -V`)
#   - For bundling: linux dependencies per Tauri docs (appimagetool, desktop-file-utils, etc.)
#   - jq (recommended; falls back to grep if missing)
set -euo pipefail

CHANNEL="${CHANNEL:-stable}"
OUTDIR="${OUTDIR:-./dist/explorer-linux}"
TARGETS="${TARGETS:-x86_64-unknown-linux-gnu}"
SIGN_APPIMAGE="${SIGN_APPIMAGE:-0}"
SIGN_PACKAGES="${SIGN_PACKAGES:-0}"
GPG_KEY_ID="${GPG_KEY_ID:-}"
GPG_SIGN_ARGS="${GPG_SIGN_ARGS:---pinentry-mode loopback}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
TAURI_DIR="$REPO_ROOT/installers/explorer-desktop/tauri"

msg() { printf '\033[1;36m[i]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
req() { command -v "$1" >/dev/null 2>&1 || err "Missing dependency: $1"; }

# --- Sanity checks ---
req cargo
req rustc
if ! cargo tauri -V >/dev/null 2>&1; then
  err "cargo tauri not found. Install with: cargo install tauri-cli --locked"
fi

if [[ ! -d "$TAURI_DIR" ]]; then
  err "Tauri project not found at: $TAURI_DIR"
fi

# jq is optional; used to read version
VERSION="0.0.0"
if command -v jq >/dev/null 2>&1 && [[ -f "$TAURI_DIR/tauri.conf.json" ]]; then
  VERSION="$(jq -r '.package.version // empty' "$TAURI_DIR/tauri.conf.json" || true)"
fi
if [[ -z "$VERSION" ]]; then
  # fallback: Cargo.toml package version
  if [[ -f "$TAURI_DIR/Cargo.toml" ]]; then
    VERSION="$(grep -m1 -E '^version\s*=\s*"' "$TAURI_DIR/Cargo.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
  fi
fi
[[ -z "$VERSION" ]] && warn "Could not detect version; will continue."

msg "Repo root: $REPO_ROOT"
msg "Tauri dir: $TAURI_DIR"
msg "Channel:   $CHANNEL"
msg "Version:   ${VERSION:-unknown}"
msg "Targets:   $TARGETS"

# --- Optional frontend install (if a package.json exists next to tauri) ---
if [[ -f "$TAURI_DIR/../package.json" ]]; then
  FRONTEND_DIR="$(cd "$TAURI_DIR/.." && pwd)"
  if command -v pnpm >/dev/null 2>&1; then
    msg "Installing frontend deps with pnpm…"
    (cd "$FRONTEND_DIR" && pnpm install --frozen-lockfile)
  elif command -v npm >/dev/null 2>&1; then
    msg "Installing frontend deps with npm ci…"
    (cd "$FRONTEND_DIR" && npm ci)
  elif command -v yarn >/dev/null 2>&1; then
    msg "Installing frontend deps with yarn…"
    (cd "$FRONTEND_DIR" && yarn --frozen-lockfile)
  else
    warn "No npm/yarn/pnpm found; assuming frontend is not required."
  fi
fi

# --- Build per target ---
mkdir -p "$OUTDIR"
ART_LIST=()

for TARGET in $TARGETS; do
  msg "Building Tauri bundle for target: $TARGET"
  (cd "$TAURI_DIR" && cargo tauri build --target "$TARGET")

  BUNDLE_DIR="$TAURI_DIR/target/$TARGET/release/bundle"
  [[ -d "$BUNDLE_DIR" ]] || err "Bundle output not found for $TARGET at $BUNDLE_DIR"

  case "$TARGET" in
    x86_64-unknown-linux-gnu) ARCH="x86_64" ;;
    aarch64-unknown-linux-gnu) ARCH="aarch64" ;;
    *) ARCH="$TARGET" ;;
  esac

  DEST_DIR="$OUTDIR/$CHANNEL/$ARCH"
  mkdir -p "$DEST_DIR"

  # Collect artifacts
  shopt -s nullglob
  for f in "$BUNDLE_DIR/appimage/"*.AppImage \
           "$BUNDLE_DIR/deb/"*.deb \
           "$BUNDLE_DIR/rpm/"*.rpm; do
    base="$(basename "$f")"
    msg "Collect: $base"
    cp -f "$f" "$DEST_DIR/"
    ART_LIST+=("$DEST_DIR/$base")
  done
  shopt -u nullglob
done

# --- Hash and optional sign ---
sha_file() {
  local file="$1"
  local sum
  if command -v sha256sum >/dev/null 2>&1; then
    sum="$(sha256sum "$file" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    sum="$(shasum -a 256 "$file" | awk '{print $1}')"
  else
    err "No sha256sum/shasum available."
  fi
  printf "%s  %s\n" "$sum" "$(basename "$file")" | tee >(cat > "${file}.sha256") >/dev/null
}

maybe_gpg_sign() {
  local file="$1"
  local kind="$2" # appimage|pkg
  local do_sign=0
  [[ "$kind" == "appimage" && "$SIGN_APPIMAGE" == "1" ]] && do_sign=1
  [[ "$kind" == "pkg" && "$SIGN_PACKAGES" == "1" ]] && do_sign=1
  if [[ "$do_sign" == "1" ]]; then
    [[ -z "$GPG_KEY_ID" ]] && err "SIGN_*=1 but GPG_KEY_ID not set."
    req gpg
    msg "Signing (gpg) → ${file}.asc"
    gpg --batch --yes --armor --local-user "$GPG_KEY_ID" $GPG_SIGN_ARGS \
        --output "${file}.asc" --detach-sign "$file"
  fi
}

msg "Hashing & signing artifacts…"
for a in "${ART_LIST[@]}"; do
  case "$a" in
    *.AppImage) maybe_gpg_sign "$a" "appimage" ;;
    *.deb|*.rpm) maybe_gpg_sign "$a" "pkg" ;;
    *) ;;
  esac
  sha_file "$a"
done

# --- Summary ---
echo
msg "✅ Build complete. Artifacts:"
for a in "${ART_LIST[@]}"; do
  printf "  - %s\n" "$a"
  [[ -f "${a}.asc" ]] && printf "    * sig: %s\n" "${a}.asc"
  printf "    * sha: %s\n" "${a}.sha256"
done

echo
msg "Output root: $(cd "$OUTDIR/$CHANNEL" && pwd)"
msg "Done."
