#!/usr/bin/env bash
# Animica Wallet — Linux bump version helper
#
# Purpose:
#   - Set the release version used by Linux packaging flows.
#   - Persist version & channel into installers/.env (consumed by build_release.sh).
#   - Optionally update Flatpak manifest and AppImage builder recipe if present.
#   - Optionally update AppStream appdata with a new <release>.
#
# Usage:
#   ./installers/wallet/linux/scripts/bump_version.sh 1.2.3 [--channel stable|beta|dev]
#
# Notes:
#   * This script does NOT tag git. Consider: `git tag v1.2.3 && git push --tags`.
#   * Keep CI in sync so it sources installers/.env before building.

set -Eeuo pipefail

### ───────────────────────────── ui helpers ─────────────────────────────
info() { printf "\033[1;34m[bump]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[err ]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

### ───────────────────────────── args & validation ──────────────────────
if [[ $# -lt 1 ]]; then
  cat <<USAGE
Usage: $0 <semver> [--channel stable|beta|dev]

Examples:
  $0 1.2.3
  $0 1.2.3 --channel beta
USAGE
  exit 1
fi

VERSION="$1"; shift || true
CHANNEL="${CHANNEL:-stable}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel)
      CHANNEL="${2:-}"; [[ -n "$CHANNEL" ]] || die "--channel requires a value"
      shift 2 ;;
    --channel=*)
      CHANNEL="${1#*=}"; shift ;;
    *)
      die "Unknown argument: $1" ;;
  esac
done

# semver-ish: MAJOR.MINOR.PATCH with optional pre-release/build meta
if [[ ! "$VERSION" =~ ^[0-9]+(\.[0-9]+){2}([\-+][0-9A-Za-z\.-]+)?$ ]]; then
  die "Version must look like semver, e.g. 1.2.3 or 1.2.3-rc.1 (+build ok). Got: '$VERSION'"
fi
if [[ ! "$CHANNEL" =~ ^(stable|beta|dev)$ ]]; then
  die "Channel must be one of: stable, beta, dev. Got: '$CHANNEL'"
fi

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT_DIR"

ENV_FILE="installers/.env"
FLATPAK_MANIFEST="installers/wallet/linux/flatpak/io.animica.Wallet.yml"
APPIMAGE_RECIPE="installers/wallet/linux/appimage/AppImageBuilder.yml"
APPDATA_XML_CANDIDATES=(
  "installers/wallet/linux/flatpak/io.animica.Wallet.appdata.xml"
  "installers/wallet/linux/appimage/io.animica.Wallet.appdata.xml"
  "installers/wallet/linux/appimage/animica-wallet.appdata.xml"
)

### ───────────────────────────── file helpers ───────────────────────────
# set or replace KEY=VALUE in a dotenv-style file
set_kv() {
  local file="$1" key="$2" value="$3"
  touch "$file"
  if grep -q -E "^[[:space:]]*${key}=" "$file"; then
    sed -i -E "s|^[[:space:]]*${key}=.*|${key}=${value}|g" "$file"
  else
    printf "%s=%s\n" "$key" "$value" >> "$file"
  fi
}

# in-place YAML kv replace if key exists; else append at end (top-level)
yaml_set_top_kv() {
  local file="$1" key="$2" value="$3"
  [[ -f "$file" ]] || return 0
  if grep -q -E "^[[:space:]]*${key}:" "$file"; then
    sed -i -E "s|^([[:space:]]*${key}:[[:space:]]*).*$|\1\"${value}\"|g" "$file"
  else
    echo "${key}: \"${value}\"" >> "$file"
  fi
}

# update/insert AppStream <release version="X" date="YYYY-MM-DD"/>
appdata_add_release() {
  local file="$1" ver="$2"
  [[ -f "$file" ]] || return 0
  local d; d="$(date +%Y-%m-%d)"
  # If a <releases> block exists, insert at top; else create block near end.
  if grep -q "<releases>" "$file"; then
    # Insert after <releases>
    awk -v ver="$ver" -v d="$d" '
      {print}
      $0 ~ /<releases>/ && !done {
        print "    <release version=\"" ver "\" date=\"" d "\"/>"
        done=1
      }
    ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
  else
    # Add full block before </component>
    awk -v ver="$ver" -v d="$d" '
      /<\/component>/ && !done {
        print "  <releases>"
        print "    <release version=\"" ver "\" date=\"" d "\"/>"
        print "  </releases>"
        done=1
      } {print}
    ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
  fi
}

### ───────────────────────────── perform updates ────────────────────────
info "Bumping version to ${VERSION} (channel: ${CHANNEL})"

# 1) Persist to installers/.env
mkdir -p "$(dirname "$ENV_FILE")"
set_kv "$ENV_FILE" "WALLET_VERSION" "$VERSION"
set_kv "$ENV_FILE" "CHANNEL" "$CHANNEL"
info "Updated ${ENV_FILE}"

# 2) Flatpak manifest (best-effort), store under x-animica-version
if [[ -f "$FLATPAK_MANIFEST" ]]; then
  yaml_set_top_kv "$FLATPAK_MANIFEST" "x-animica-version" "$VERSION"
  info "Updated Flatpak manifest: ${FLATPAK_MANIFEST} (x-animica-version: ${VERSION})"
else
  warn "Flatpak manifest not found at ${FLATPAK_MANIFEST} (skip)"
fi

# 3) AppImage builder recipe (best-effort)
if [[ -f "$APPIMAGE_RECIPE" ]]; then
  yaml_set_top_kv "$APPIMAGE_RECIPE" "version" "$VERSION"
  info "Updated AppImage recipe: ${APPIMAGE_RECIPE} (version: ${VERSION})"
else
  warn "AppImage recipe not found at ${APPIMAGE_RECIPE} (skip)"
fi

# 4) AppStream appdata (insert new <release/> tag)
added_appdata=0
for f in "${APPDATA_XML_CANDIDATES[@]}"; do
  if [[ -f "$f" ]]; then
    appdata_add_release "$f" "$VERSION"
    info "Updated AppStream appdata: ${f} (added <release version=\"${VERSION}\">)"
    added_appdata=1
  fi
done
[[ "$added_appdata" -eq 0 ]] && warn "No AppStream appdata XML found (optional)"

### ───────────────────────────── summary ────────────────────────────────
echo
info "Done. Summary:"
echo "  - Version:  $VERSION"
echo "  - Channel:  $CHANNEL"
echo "  - Env:      $ENV_FILE"
[[ -f "$FLATPAK_MANIFEST" ]] && echo "  - Flatpak:   $FLATPAK_MANIFEST (x-animica-version)"
[[ -f "$APPIMAGE_RECIPE"  ]] && echo "  - AppImage:  $APPIMAGE_RECIPE (version)"
if [[ "$added_appdata" -eq 1 ]]; then
  echo "  - AppStream: release node(s) updated"
fi

echo
info "Next:"
echo "  1) Commit changes: git add -A && git commit -m \"chore(release): bump linux version to ${VERSION}\""
echo "  2) (Optional) git tag v${VERSION} && git push && git push --tags"
echo "  3) Build: CHANNEL=${CHANNEL} WALLET_VERSION=${VERSION} ./installers/wallet/linux/scripts/build_release.sh"
