#!/usr/bin/env bash
# Animica Wallet — helper to staple notarization tickets to artifacts
# Supports: .dmg, .pkg, .app (zip may not staple; prefer stapling the inner .app or DMG/PKG)
#
# Usage:
#   ./staple.sh path/to/Animica-Wallet-1.2.3-macOS.dmg [more artifacts...]
#   ./staple.sh --strict dist/*.dmg
#   NOTARY_ARTIFACT_PATH=dist/Animica-Wallet.dmg ./staple.sh
#
# Options:
#   --strict         Exit on first failure (default: continue and report)
#   --validate-only  Only run `xcrun stapler validate` (skip stapling)
#   --spctl          After stapling, run `spctl -a -vv` Gatekeeper check
#
# Notes:
#   - Requires Xcode CLT (`xcrun stapler`).
#   - For ZIPs, Apple may not permit stapling; staple the .app inside or distribute DMG/PKG.
set -euo pipefail

### helpers
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing tool: $1" >&2; exit 1; }; }
log()  { printf "\033[1;34m[staple]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[err]\033[0m %s\n" "$*" >&2; }

need xcrun

STRICT=0
VALIDATE_ONLY=0
DO_SPCTL=0
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict) STRICT=1; shift;;
    --validate-only) VALIDATE_ONLY=1; shift;;
    --spctl) DO_SPCTL=1; shift;;
    -h|--help)
      sed -n '1,200p' "$0"; exit 0;;
    *) ARGS+=("$1"); shift;;
  esac
done

if [[ ${#ARGS[@]} -eq 0 && -n "${NOTARY_ARTIFACT_PATH:-}" ]]; then
  ARGS+=("$NOTARY_ARTIFACT_PATH")
fi

[[ ${#ARGS[@]} -gt 0 ]] || { err "No artifacts provided. Pass paths or set NOTARY_ARTIFACT_PATH"; exit 1; }

FAILED=0

staple_one() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    err "Not found: $path"
    return 1
  fi

  local ext="${path##*.}"
  case "$ext" in
    dmg|pkg|app) ;;
    zip)
      warn "ZIP may not support stapling; prefer stapling the contained .app or DMG/PKG → $path"
      ;;
    *)
      warn "Unknown extension '.$ext' — attempting staple anyway: $path"
      ;;
  esac

  if [[ $VALIDATE_ONLY -eq 0 ]]; then
    log "Stapling ticket → $path"
    if ! xcrun stapler staple "$path"; then
      err "Staple failed: $path"
      return 1
    fi
  fi

  log "Validating ticket → $path"
  if ! xcrun stapler validate "$path"; then
    err "Stapler validation failed: $path"
    return 1
  fi

  if [[ $DO_SPCTL -eq 1 ]]; then
    # Gatekeeper assessment (best-effort)
    log "Gatekeeper check (spctl) → $path"
    if ! spctl -a -vv "$path"; then
      warn "spctl reported issues (may be expected for DMG without mount); ensure notarization completed."
    fi
  fi

  log "OK: $path"
  return 0
}

for p in "${ARGS[@]}"; do
  if ! staple_one "$p"; then
    FAILED=$((FAILED+1))
    if [[ $STRICT -eq 1 ]]; then
      err "Fail-fast (--strict): aborting after error."
      exit 1
    fi
  fi
done

if [[ $FAILED -gt 0 ]]; then
  err "Completed with $FAILED failure(s)."
  exit 2
fi

log "All artifacts processed successfully."
