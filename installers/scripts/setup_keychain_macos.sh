#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# setup_keychain_macos.sh — Create an ephemeral signing keychain on macOS CI
#
# Usage:
#   installers/scripts/setup_keychain_macos.sh [setup|cleanup]
#
# Environment (inputs):
#   MACOS_SIGNING_CERT_BASE64   Base64-encoded PKCS#12 (.p12) Developer ID cert (optional)
#   MACOS_SIGNING_CERT_PASSWORD Password for the .p12 (optional; empty ok)
#   MACOS_CERT_CHAIN_BASE64     Base64-encoded PEM chain (optional)
#   KC_NAME                     Keychain name (default: animica-ci-<epoch>-<rand>)
#   KC_PASSWORD                 Keychain password (random if not set)
#   KC_TIMEOUT_SECS             Auto-lock timeout (default: 21600 = 6h)
#   CLEANUP_ON_EXIT             If "1", auto-delete keychain on script exit (default: 0)
#
# Outputs (exported / GitHub Actions compatible):
#   MACOS_KEYCHAIN_NAME         Keychain file name (e.g., animica-ci-... .keychain)
#   MACOS_KEYCHAIN_PATH         Absolute keychain path under ~/Library/Keychains
#   MACOS_KEYCHAIN_PASSWORD     Password to unlock keychain
#   CODE_SIGN_IDENTITY_HASH     First codesign identity hash (if imported)
#   CODE_SIGN_IDENTITY_NAME     First codesign identity name (CN) (if imported)
#
# Notes:
# - Safe to run with no certificate variables: creates an empty keychain for
#   API-key notarization workflows where local codesign occurs elsewhere.
# - Designed for GitHub Actions macOS runners.
# ------------------------------------------------------------------------------

set -Eeuo pipefail

# Source shared env (optional)
if [[ -z "${ROOT:-}" ]]; then
  if command -v git >/dev/null 2>&1 && git rev-parse --show-toplevel >/dev/null 2>&1; then
    ROOT="$(git rev-parse --show-toplevel)"
  else
    ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
  fi
fi
if [[ -f "$ROOT/installers/scripts/common_env.sh" ]]; then
  # shellcheck disable=SC1090
  source "$ROOT/installers/scripts/common_env.sh"
else
  log()  { echo "[INFO ] $*" >&2; }
  warn() { echo "[WARN ] $*" >&2; }
  err()  { echo "[ERROR] $*" >&2; }
  die()  { err "$*"; exit 1; }
fi

cmd="${1:-setup}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This script must run on macOS (Darwin). Current: $(uname -s)"
fi

require_cmd security
require_cmd base64

random_str() {
  # 16 chars alpha-num from /dev/urandom
  LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16
}

setup() {
  local now epoch rand
  epoch="$(date +%s)"
  rand="$(random_str)"

  local KC_NAME KC_PASSWORD KC_TIMEOUT_SECS
  KC_NAME="${KC_NAME:-animica-ci-${epoch}-${rand}}"
  KC_PASSWORD="${KC_PASSWORD:-$(random_str)$(random_str)}"
  KC_TIMEOUT_SECS="${KC_TIMEOUT_SECS:-21600}" # 6 hours

  local KC_FILE="${KC_NAME}.keychain"
  local KC_PATH="$HOME/Library/Keychains/${KC_FILE}"

  log "Creating keychain: $KC_FILE"
  security create-keychain -p "$KC_PASSWORD" "$KC_FILE"
  security set-keychain-settings -lut "$KC_TIMEOUT_SECS" "$KC_FILE"
  security unlock-keychain -p "$KC_PASSWORD" "$KC_FILE"

  # Add to search list and set as default
  # Capture existing, append our new one, and set
  local current_list
  current_list="$(security list-keychains -d user | sed -E 's/^[[:space:]]*\"?([^"]+)\"?/\1/g')"
  # shellcheck disable=SC2086
  security list-keychains -d user -s $current_list "$KC_PATH" || true
  security default-keychain -d user -s "$KC_FILE"

  # Import developer ID p12 if provided
  if [[ -n "${MACOS_SIGNING_CERT_BASE64:-}" ]]; then
    log "Importing Developer ID certificate into $KC_FILE"
    tmp_p12="$(mktemp -t animica-cert.XXXXXX.p12)"
    echo "$MACOS_SIGNING_CERT_BASE64" | base64 --decode > "$tmp_p12"

    local import_args=(-k "$KC_FILE" -f pkcs12 -P "${MACOS_SIGNING_CERT_PASSWORD:-}")
    # Allow codesign and security tools access without prompts
    import_args+=(-T /usr/bin/codesign -T /usr/bin/security)

    security import "$tmp_p12" "${import_args[@]}"
    rm -f "$tmp_p12"

    # Optional: import chain (PEM) if provided
    if [[ -n "${MACOS_CERT_CHAIN_BASE64:-}" ]]; then
      log "Importing certificate chain (PEM)"
      tmp_pem="$(mktemp -t animica-chain.XXXXXX.pem)"
      echo "$MACOS_CERT_CHAIN_BASE64" | base64 --decode > "$tmp_pem"
      security import "$tmp_pem" -k "$KC_FILE" -T /usr/bin/codesign -T /usr/bin/security || true
      rm -f "$tmp_pem"
    fi

    # Grant partition list permissions for codesign (no interactive prompt)
    security set-key-partition-list -S apple-tool:,apple: -s -k "$KC_PASSWORD" "$KC_FILE" || true
  else
    warn "MACOS_SIGNING_CERT_BASE64 is empty; creating keychain without identities."
  fi

  # Discover first codesign identity (if any) to help downstream steps
  local ID_HASH ID_NAME
  if security find-identity -v -p codesigning "$KC_FILE" >/dev/null 2>&1; then
    # Example line: 1) ABCDEF... "Developer ID Application: Company (TEAMID)"
    ID_HASH="$(security find-identity -v -p codesigning "$KC_FILE" | awk 'NR==1{print $2}')"
    ID_NAME="$(security find-identity -v -p codesigning "$KC_FILE" | sed -n '1s/.*"//;1s/"$//;1p')"
    if [[ -n "$ID_HASH" && -n "$ID_NAME" ]]; then
      log "Found codesign identity: $ID_NAME ($ID_HASH)"
      export CODE_SIGN_IDENTITY_HASH="$ID_HASH"
      export CODE_SIGN_IDENTITY_NAME="$ID_NAME"
    else
      warn "No codesign identities found in $KC_FILE"
    fi
  fi

  # Export outputs for subsequent CI steps
  export MACOS_KEYCHAIN_NAME="$KC_FILE"
  export MACOS_KEYCHAIN_PATH="$KC_PATH"
  export MACOS_KEYCHAIN_PASSWORD="$KC_PASSWORD"

  # GitHub Actions env propagation
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    {
      echo "MACOS_KEYCHAIN_NAME=$MACOS_KEYCHAIN_NAME"
      echo "MACOS_KEYCHAIN_PATH=$MACOS_KEYCHAIN_PATH"
      echo "MACOS_KEYCHAIN_PASSWORD=$MACOS_KEYCHAIN_PASSWORD"
      [[ -n "${CODE_SIGN_IDENTITY_HASH:-}" ]] && echo "CODE_SIGN_IDENTITY_HASH=$CODE_SIGN_IDENTITY_HASH"
      [[ -n "${CODE_SIGN_IDENTITY_NAME:-}" ]] && echo "CODE_SIGN_IDENTITY_NAME=$CODE_SIGN_IDENTITY_NAME"
    } >> "$GITHUB_ENV"
  fi

  # Optional cleanup on exit
  if [[ "${CLEANUP_ON_EXIT:-0}" == "1" ]]; then
    log "CLEANUP_ON_EXIT=1 — keychain will be removed when this script exits."
    trap 'cleanup_internal "$KC_FILE"' EXIT
  fi

  log "Keychain ready: $MACOS_KEYCHAIN_NAME"
  log "Default keychain: $(security default-keychain -d user 2>/dev/null || true)"
}

cleanup_internal() {
  local kc_file="$1"
  log "Deleting keychain: $kc_file"
  # Remove from default and search list first
  security default-keychain -d user -s login.keychain 2>/dev/null || true
  # Try both db and non-db names in list-keychains to be safe
  local kc_path="$HOME/Library/Keychains/$kc_file"
  local current_list
  current_list="$(security list-keychains -d user | sed -E 's/^[[:space:]]*\"?([^"]+)\"?/\1/g' | grep -v "$kc_file" || true)"
  # shellcheck disable=SC2086
  security list-keychains -d user -s $current_list 2>/dev/null || true
  # Finally delete
  security delete-keychain "$kc_file" 2>/dev/null || true
  log "Keychain deleted (if it existed)."
}

cleanup() {
  local kc_file="${MACOS_KEYCHAIN_NAME:-}"
  if [[ -z "$kc_file" ]]; then
    die "Set MACOS_KEYCHAIN_NAME=<name>.keychain to cleanup."
  fi
  cleanup_internal "$kc_file"
}

case "$cmd" in
  setup)   setup ;;
  cleanup) cleanup ;;
  *)       die "Unknown command: $cmd (expected: setup|cleanup)" ;;
esac
