#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# import_p12_macos.sh — Import an Apple Developer (.p12) certificate into a keychain
#
# Usage:
#   installers/scripts/import_p12_macos.sh
#
# Inputs (env):
#   # Provide the certificate either as BASE64 or as a file path:
#   MACOS_SIGNING_CERT_BASE64   Base64-encoded PKCS#12 (.p12) contents (optional)
#   P12_PATH                    Path to .p12 on disk (optional)
#   MACOS_SIGNING_CERT_PASSWORD Password for the .p12 (can be empty)
#
#   # Target keychain (created beforehand by setup_keychain_macos.sh or existing):
#   MACOS_KEYCHAIN_NAME         Keychain filename (e.g., animica-ci-XXXX.keychain). Default: login.keychain
#   MACOS_KEYCHAIN_PASSWORD     Keychain password (required for custom ephemeral keychains)
#
#   # Optional chain (intermediate/root) as base64 or PEM path:
#   MACOS_CERT_CHAIN_BASE64     Base64-encoded PEM bundle (optional)
#   CHAIN_PEM_PATH              Path to PEM bundle (optional)
#
# Outputs:
#   Exports CODE_SIGN_IDENTITY_HASH and CODE_SIGN_IDENTITY_NAME if found.
#   Writes to GITHUB_ENV when available.
#
# Notes:
#   - Safe to call multiple times; imports are idempotent.
#   - Grants partition list to avoid interactive prompts for codesign/security.
# ------------------------------------------------------------------------------

set -Eeuo pipefail

# ---- logging helpers / common env --------------------------------------------
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
  _log_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
  log()  { echo "[INFO ][$(_log_ts)] $*" >&2; }
  warn() { echo "[WARN ][$(_log_ts)] $*" >&2; }
  err()  { echo "[ERROR][$(_log_ts)] $*" >&2; }
  die()  { err "$*"; exit 1; }
  have_cmd() { command -v "$1" >/dev/null 2>&1; }
  require_cmd() { have_cmd "$1" || die "Missing command: $1"; }
fi

[[ "$(uname -s)" == "Darwin" ]] || die "This script must run on macOS (Darwin)."

require_cmd security
require_cmd base64

# ---- resolve inputs -----------------------------------------------------------
KEYCHAIN_FILE="${MACOS_KEYCHAIN_NAME:-login.keychain}"
KEYCHAIN_PATH="$HOME/Library/Keychains/$KEYCHAIN_FILE"
KEYCHAIN_PASS="${MACOS_KEYCHAIN_PASSWORD:-}"

P12_TMP=""
cleanup() {
  [[ -n "$P12_TMP" && -f "$P12_TMP" ]] && rm -f "$P12_TMP"
}
trap cleanup EXIT

if [[ -n "${MACOS_SIGNING_CERT_BASE64:-}" ]]; then
  P12_TMP="$(mktemp -t animica-cert.XXXXXX.p12)"
  echo "$MACOS_SIGNING_CERT_BASE64" | base64 --decode > "$P12_TMP"
  P12_SRC="$P12_TMP"
elif [[ -n "${P12_PATH:-}" ]]; then
  [[ -f "$P12_PATH" ]] || die "P12_PATH does not exist: $P12_PATH"
  P12_SRC="$P12_PATH"
else
  die "Provide MACOS_SIGNING_CERT_BASE64 or P12_PATH."
fi

CHAIN_TMP=""
if [[ -n "${MACOS_CERT_CHAIN_BASE64:-}" ]]; then
  CHAIN_TMP="$(mktemp -t animica-chain.XXXXXX.pem)"
  echo "$MACOS_CERT_CHAIN_BASE64" | base64 --decode > "$CHAIN_TMP"
  CHAIN_SRC="$CHAIN_TMP"
elif [[ -n "${CHAIN_PEM_PATH:-}" ]]; then
  [[ -f "$CHAIN_PEM_PATH" ]] || die "CHAIN_PEM_PATH does not exist: $CHAIN_PEM_PATH"
  CHAIN_SRC="$CHAIN_PEM_PATH"
else
  CHAIN_SRC=""
fi

# ---- keychain prepare ---------------------------------------------------------
# Unlock custom keychain if provided; ignore if login
if [[ "$KEYCHAIN_FILE" != "login.keychain" && -n "$KEYCHAIN_PASS" ]]; then
  log "Unlocking keychain $KEYCHAIN_FILE"
  security unlock-keychain -p "$KEYCHAIN_PASS" "$KEYCHAIN_FILE" || die "Failed to unlock $KEYCHAIN_FILE"
else
  log "Using keychain $KEYCHAIN_FILE"
fi

# Ensure keychain in search list
current_list="$(security list-keychains -d user | sed -E 's/^[[:space:]]*\"?([^"]+)\"?/\1/g')"
# shellcheck disable=SC2086
security list-keychains -d user -s $current_list "$KEYCHAIN_PATH" >/dev/null 2>&1 || true

# ---- import p12 ---------------------------------------------------------------
log "Importing .p12 into $KEYCHAIN_FILE"
IMPORT_ARGS=(-k "$KEYCHAIN_FILE" -f pkcs12 -P "${MACOS_SIGNING_CERT_PASSWORD:-}")
# Allow common tools to access key without GUI prompts
IMPORT_ARGS+=(-T /usr/bin/codesign -T /usr/bin/security -T /usr/bin/productsign -T /usr/bin/productbuild)

security import "$P12_SRC" "${IMPORT_ARGS[@]}"

# Import chain if provided
if [[ -n "$CHAIN_SRC" ]]; then
  log "Importing certificate chain (PEM) into $KEYCHAIN_FILE"
  security import "$CHAIN_SRC" -k "$KEYCHAIN_FILE" -T /usr/bin/codesign -T /usr/bin/security || true
fi

# Grant partition list for the imported keys (prevents interactive prompts)
if [[ -n "$KEYCHAIN_PASS" ]]; then
  security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYCHAIN_PASS" "$KEYCHAIN_FILE" || true
fi

# ---- discover identity --------------------------------------------------------
if security find-identity -v -p codesigning "$KEYCHAIN_FILE" >/dev/null 2>&1; then
  ID_HASH="$(security find-identity -v -p codesigning "$KEYCHAIN_FILE" | awk 'NR==1{print $2}')"
  ID_NAME="$(security find-identity -v -p codesigning "$KEYCHAIN_FILE" | sed -n '1s/.*"//;1s/"$//;1p')"
  if [[ -n "$ID_HASH" && -n "$ID_NAME" ]]; then
    log "Codesign identity: $ID_NAME ($ID_HASH)"
    export CODE_SIGN_IDENTITY_HASH="$ID_HASH"
    export CODE_SIGN_IDENTITY_NAME="$ID_NAME"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      {
        echo "CODE_SIGN_IDENTITY_HASH=$CODE_SIGN_IDENTITY_HASH"
        echo "CODE_SIGN_IDENTITY_NAME=$CODE_SIGN_IDENTITY_NAME"
      } >> "$GITHUB_ENV"
    fi
  else
    warn "No codesign identities found after import."
  fi
else
  warn "No codesign identities present in $KEYCHAIN_FILE"
fi

log "Import complete."
