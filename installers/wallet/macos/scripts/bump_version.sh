#!/usr/bin/env bash
# Animica Wallet — bump CFBundleShortVersionString in an Info.plist
#
# Usage:
#   # Set an explicit version:
#   ./bump_version.sh --plist path/to/Info.plist --set 1.2.3
#
#   # Or bump existing version:
#   ./bump_version.sh --plist path/to/Info.plist --bump patch   # major|minor|patch
#
#   # Convenience if you pass an .app bundle:
#   ./bump_version.sh --app "dist/Animica Wallet.app" --set 1.2.3
#
# Notes:
#   - Only touches CFBundleShortVersionString (marketing version).
#   - Does not modify CFBundleVersion (build). Use your CI to set build separately.
set -euo pipefail

### helpers
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing tool: $1" >&2; exit 1; }; }
die() { echo "error: $*" >&2; exit 1; }
log() { printf "\033[1;34m[bump]\033[0m %s\n" "$*"; }

need /usr/libexec/PlistBuddy
need plutil

PLIST=""
APP=""
SET_VER=""
BUMP_KIND=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plist) PLIST="$2"; shift 2;;
    --app) APP="$2"; shift 2;;
    --set) SET_VER="$2"; shift 2;;
    --bump) BUMP_KIND="$2"; shift 2;;
    -h|--help)
      sed -n '1,80p' "$0"; exit 0;;
    *) die "Unknown arg: $1";;
  endesac || true
done

if [[ -n "$APP" ]]; then
  [[ -d "$APP" ]] || die "App bundle not found: $APP"
  PLIST="${APP%/}/Contents/Info.plist"
fi

[[ -n "$PLIST" ]] || die "--plist or --app is required"
[[ -f "$PLIST" ]] || die "Info.plist not found: $PLIST"

is_semver() {
  [[ "$1" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]
}

semver_bump() {
  local cur="$1" kind="$2"
  if ! is_semver "$cur"; then
    die "Current version '$cur' is not simple semver (X.Y.Z) — use --set"
  fi
  local major minor patch
  IFS='.' read -r major minor patch <<<"$cur"
  case "$kind" in
    major) major=$((major+1)); minor=0; patch=0;;
    minor) minor=$((minor+1)); patch=0;;
    patch) patch=$((patch+1));;
    *) die "--bump must be major|minor|patch";;
  esac
  echo "${major}.${minor}.${patch}"
}

# read current version (may not exist)
current=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST" 2>/dev/null || true)

if [[ -n "$SET_VER" && -n "$BUMP_KIND" ]]; then
  die "Use either --set or --bump, not both"
fi

if [[ -n "$SET_VER" ]]; then
  if ! is_semver "$SET_VER"; then
    die "Provided --set version '$SET_VER' must be X.Y.Z"
  fi
  new="$SET_VER"
elif [[ -n "$BUMP_KIND" ]]; then
  [[ -n "$current" ]] || die "CFBundleShortVersionString missing; use --set to initialize"
  new="$(semver_bump "$current" "$BUMP_KIND")"
else
  die "Specify --set X.Y.Z or --bump {major|minor|patch}"
fi

log "Info.plist: $PLIST"
log "Current CFBundleShortVersionString: ${current:-<none>}"
log "New CFBundleShortVersionString: $new"

# write key (add if missing)
if /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST" >/dev/null 2>&1; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $new" "$PLIST"
else
  /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $new" "$PLIST"
fi

plutil -lint "$PLIST" >/dev/null
log "Done ✔"
