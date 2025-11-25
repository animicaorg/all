#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Animica Installers — shared environment loader & sanity checks
# Source this from packaging scripts:
#   source "$(git rev-parse --show-toplevel)/installers/scripts/common_env.sh"
# ------------------------------------------------------------------------------

set -Eeuo pipefail

# ---- logging helpers ----------------------------------------------------------
_log_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log()  { echo "[INFO ][$(_log_ts)] $*" >&2; }
warn() { echo "[WARN ][$(_log_ts)] $*" >&2; }
err()  { echo "[ERROR][$(_log_ts)] $*" >&2; }
die()  { err "$*"; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }
require_cmd() { have_cmd "$1" || die "Missing required command: $1"; }

# ---- repo roots & paths -------------------------------------------------------
# Resolve repo root (prefer git); fallback to script-relative
if have_cmd git && git rev-parse --show-toplevel >/dev/null 2>&1; then
  ROOT="$(git rev-parse --show-toplevel)"
else
  # script is at installers/scripts/common_env.sh
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
fi
export ROOT

INSTALLERS_DIR="$ROOT/installers"
SCRIPTS_DIR="$INSTALLERS_DIR/scripts"
DIST_DIR="${DIST_DIR:-$INSTALLERS_DIR/dist}"
TMP_DIR="${TMP_DIR:-$INSTALLERS_DIR/.tmp}"
mkdir -p "$DIST_DIR" "$TMP_DIR"

export INSTALLERS_DIR SCRIPTS_DIR DIST_DIR TMP_DIR

# ---- OS / arch detection ------------------------------------------------------
UNAME_S="$(uname -s 2>/dev/null || echo unknown)"
UNAME_M="$(uname -m 2>/dev/null || echo unknown)"

case "$UNAME_S" in
  Linux)   OS=linux ;;
  Darwin)  OS=macos ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT) OS=windows ;;
  *)       OS=unknown ;;
esac

case "$UNAME_M" in
  x86_64|amd64)   ARCH=amd64 ;;
  aarch64|arm64)  ARCH=arm64 ;;
  *)              ARCH="$UNAME_M" ;;
esac

export OS ARCH

# ---- .env loader --------------------------------------------------------------
# Loads KEY=VAL pairs from installers/.env (preferred) then repo .env (if exists).
# Uses 'set -a; source file; set +a' to honor quotes/escapes.
load_env_file() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  # shellcheck disable=SC1090
  set -a
  source "$f"
  set +a
  log "Loaded env from $f"
}

ENV_USED=""
if load_env_file "$INSTALLERS_DIR/.env"; then
  ENV_USED="$INSTALLERS_DIR/.env"
elif load_env_file "$ROOT/.env"; then
  ENV_USED="$ROOT/.env"
else
  warn "No .env found; using defaults & CI environment"
fi

# ---- defaults & derived values ------------------------------------------------
CHANNEL="${CHANNEL:-nightly}"

# Version selection: explicit override > git describe > fallback
if [[ -n "${VERSION_OVERRIDE:-}" ]]; then
  VERSION="$VERSION_OVERRIDE"
else
  if have_cmd git && git -C "$ROOT" describe --tags --always --dirty >/dev/null 2>&1; then
    VERSION="$(git -C "$ROOT" describe --tags --always --dirty)"
  else
    VERSION="0.0.0+local"
  fi
fi
export CHANNEL VERSION

# Feeds & endpoints (safe defaults for local builds)
RELEASE_FEED_BASE="${RELEASE_FEED_BASE:-https://downloads.animica.org}"
RELEASE_FEED_NIGHTLY="${RELEASE_FEED_NIGHTLY:-$RELEASE_FEED_BASE/feeds/nightly.json}"
RELEASE_FEED_ALPHA="${RELEASE_FEED_ALPHA:-$RELEASE_FEED_BASE/feeds/alpha.json}"
RELEASE_FEED_BETA="${RELEASE_FEED_BETA:-$RELEASE_FEED_BASE/feeds/beta.json}"
RELEASE_FEED_STABLE="${RELEASE_FEED_STABLE:-$RELEASE_FEED_BASE/feeds/stable.json}"
ARTIFACT_CDN_BASE="${ARTIFACT_CDN_BASE:-https://cdn.animica.org/artifacts}"

DEFAULT_RPC_URL="${DEFAULT_RPC_URL:-https://rpc.animica.org}"
DEFAULT_CHAIN_ID="${DEFAULT_CHAIN_ID:-1}"
SERVICES_BASE_URL="${SERVICES_BASE_URL:-https://services.animica.org}"

export RELEASE_FEED_BASE RELEASE_FEED_NIGHTLY RELEASE_FEED_ALPHA RELEASE_FEED_BETA RELEASE_FEED_STABLE
export ARTIFACT_CDN_BASE DEFAULT_RPC_URL DEFAULT_CHAIN_ID SERVICES_BASE_URL

# Signing toggles
COSIGN_SIGN="${COSIGN_SIGN:-0}"
COSIGN_KEY="${COSIGN_KEY:-}"
COSIGN_ARGS="${COSIGN_ARGS:---yes}"
export COSIGN_SIGN COSIGN_KEY COSIGN_ARGS

# macOS signing defaults (placeholders for local builds)
APPLE_TEAM_ID="${APPLE_TEAM_ID:-}"
APPLE_API_KEY_ID="${APPLE_API_KEY_ID:-}"
APPLE_API_KEY="${APPLE_API_KEY:-}"
APPLE_API_ISSUER="${APPLE_API_ISSUER:-}"
MACOS_NOTARIZE="${MACOS_NOTARIZE:-1}"
MACOS_STAPLE="${MACOS_STAPLE:-1}"
export APPLE_TEAM_ID APPLE_API_KEY_ID APPLE_API_KEY APPLE_API_ISSUER MACOS_NOTARIZE MACOS_STAPLE

# Windows signing defaults
WIN_SIGNING_PROVIDER="${WIN_SIGNING_PROVIDER:-disabled}"
TIMESTAMP_SERVER_URL="${TIMESTAMP_SERVER_URL:-http://timestamp.digicert.com}"
export WIN_SIGNING_PROVIDER TIMESTAMP_SERVER_URL

# Optional native accelerator flags (for zk wheels/benches)
ZK_DISABLE_NATIVE="${ZK_DISABLE_NATIVE:-0}"
MATURIN_MANYLINUX="${MATURIN_MANYLINUX:-off}"
export ZK_DISABLE_NATIVE MATURIN_MANYLINUX

# ---- sanity checks ------------------------------------------------------------
# Basic toolchain checks used by most scripts
if [[ "$OS" != "windows" ]]; then
  require_cmd tar
  require_cmd gzip
fi

# Node package manager (pnpm > npm > yarn)
if have_cmd pnpm; then
  PKG_MGR=pnpm
elif have_cmd npm; then
  PKG_MGR=npm
elif have_cmd yarn; then
  PKG_MGR=yarn
else
  PKG_MGR=""
fi
export PKG_MGR

# zip utility is required by most bundling steps
if ! have_cmd zip; then
  warn "'zip' not found; some packaging targets may fail (please install 'zip')."
fi

# jq is used for reading package.json; optional but recommended
if ! have_cmd jq; then
  warn "'jq' not found; will fall back to defaults where JSON parsing is needed."
fi

# cosign presence if signing requested
if [[ "$COSIGN_SIGN" == "1" ]]; then
  have_cmd cosign || die "COSIGN_SIGN=1 but 'cosign' is not available in PATH"
  log "Cosign signing is ENABLED"
else
  log "Cosign signing is DISABLED (set COSIGN_SIGN=1 to enable)"
fi

# macOS notarization prerequisites (only check on macOS when enabled)
if [[ "$OS" == "macos" && "$MACOS_NOTARIZE" == "1" ]]; then
  for v in APPLE_TEAM_ID APPLE_API_KEY_ID APPLE_API_KEY APPLE_API_ISSUER; do
    if [[ -z "${!v:-}" ]]; then
      warn "Notarization var $v is empty; notarization may fail in CI."
    fi
  done
fi

# Windows signing provider note
if [[ "$OS" == "windows" && "$WIN_SIGNING_PROVIDER" != "disabled" ]]; then
  log "Windows signing provider: $WIN_SIGNING_PROVIDER (timestamp: $TIMESTAMP_SERVER_URL)"
fi

# ---- helpers: checksum, sign --------------------------------------------------
sha256_file() { sha256sum "$1" | awk '{print $1}'; }

write_checksum() {
  local file="$1"
  local sum
  sum="$(sha256_file "$file")"
  echo "$sum  $(basename "$file")" >> "$DIST_DIR/SHA256SUMS"
  log "SHA256 $(basename "$file"): $sum"
}

sign_blob_if_enabled() {
  local file="$1"
  if [[ "$COSIGN_SIGN" == "1" ]]; then
    local extra=()
    if [[ -n "$COSIGN_KEY" ]]; then
      extra+=(--key "$COSIGN_KEY")
    fi
    cosign sign-blob ${COSIGN_ARGS:-} "${extra[@]}" \
      --output-signature "$file.sig" \
      --output-certificate "$file.pem" \
      "$file"
    log "Cosigned $file"
  else
    log "Skipping cosign for $file (COSIGN_SIGN=0)"
  fi
}

# ---- summary ------------------------------------------------------------------
mask() {
  # Mask secrets (tokens/keys) for logs
  echo "$1" | sed -E 's/([A-Za-z0-9]{4})[A-Za-z0-9\-_]+/\1********/g'
}

print_env_summary() {
  echo ""
  echo "=== ENV SUMMARY ==="
  echo "Root:         $ROOT"
  echo "OS/Arch:      $OS/$ARCH"
  echo "Channel:      $CHANNEL"
  echo "Version:      $VERSION"
  echo "Dist Dir:     $DIST_DIR"
  echo "Tmp Dir:      $TMP_DIR"
  echo "Pkg Manager:  ${PKG_MGR:-<none>}"
  echo "Feeds:        $RELEASE_FEED_BASE"
  echo "Artifacts:    $ARTIFACT_CDN_BASE"
  echo "RPC URL:      $DEFAULT_RPC_URL"
  echo "Chain ID:     $DEFAULT_CHAIN_ID"
  echo "Services URL: $SERVICES_BASE_URL"
  echo "Cosign:       sign=${COSIGN_SIGN} key=$(mask "${COSIGN_KEY:-}")"
  echo "Notarize:     macOS=${MACOS_NOTARIZE} staple=${MACOS_STAPLE}"
  [[ -n "$ENV_USED" ]] && echo ".env file:    $ENV_USED"
  echo "===================="
  echo ""
}
print_env_summary

# If executed directly, just print summary; when sourced, functions/exports remain.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  log "common_env.sh executed directly; nothing else to do."
fi
