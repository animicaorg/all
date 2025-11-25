#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# verify_signatures.sh — Verify DMG / PKG / MSIX signatures (and optional notarization)
#
# Usage:
#   installers/scripts/verify_signatures.sh path/to/artifact1 [artifact2 ...]
#
# Environment (optional):
#   APPLE_TEAM_ID              Expected macOS Team ID (e.g., ABCDE12345). If set, enforce match.
#   WIN_CERT_ORG               Expected Windows signer subject substring (e.g., "Animica Labs").
#   CODE_SIGN_CERT_THUMBPRINT  Expected Windows signer thumbprint (strict match if set).
#
# Notes:
#   • macOS:
#       - DMG:  spctl -a -vv -t open <dmg>
#       - PKG:  pkgutil --check-signature <pkg>  and spctl -a -vv -t install <pkg>
#       - Notarization: stapler validate <dmg|pkg> (if available)
#   • Windows:
#       - MSIX: PowerShell Get-AuthenticodeSignature (pwsh or powershell), status must be Valid.
#         Optional subject/Thumbprint checks if WIN_CERT_ORG / CODE_SIGN_CERT_THUMBPRINT are set.
#   • Linux runners can still verify MSIX if PowerShell 7 (pwsh) is available.
# ------------------------------------------------------------------------------

set -Eeuo pipefail

# ---- shared logging helpers ---------------------------------------------------
_log_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log()  { echo "[INFO ][$(_log_ts)] $*" >&2; }
warn() { echo "[WARN ][$(_log_ts)] $*" >&2; }
err()  { echo "[ERROR][$(_log_ts)] $*" >&2; }
die()  { err "$*"; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# Try to source common env (for pretty logs, not required)
if [[ -z "${ROOT:-}" ]]; then
  if have_cmd git && git rev-parse --show-toplevel >/dev/null 2>&1; then
    ROOT="$(git rev-parse --show-toplevel)"
  else
    ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
  fi
fi
if [[ -f "$ROOT/installers/scripts/common_env.sh" ]]; then
  # shellcheck disable=SC1090
  source "$ROOT/installers/scripts/common_env.sh" || true
fi

# ---- OS detect ---------------------------------------------------------------
UNAME_S="$(uname -s 2>/dev/null || echo unknown)"
case "$UNAME_S" in
  Darwin)  OS=macos ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT) OS=windows ;;
  Linux)   OS=linux ;;
  *)       OS=unknown ;;
esac

# ---- helpers: parse / enforce Team ID from spctl/pkgutil output --------------
_expect_team_id="${APPLE_TEAM_ID:-}"

enforce_team_id() {
  local found="$1"
  if [[ -z "$_expect_team_id" ]]; then
    return 0
  fi
  if [[ "$found" != "$_expect_team_id" ]]; then
    err "Team ID mismatch: expected '${_expect_team_id}', got '${found}'"
    return 1
  fi
  log "Team ID matches expected '${_expect_team_id}'"
}

# Extract team id from a line containing '(TEAMID)'
extract_team_id_paren() {
  sed -nE 's/.*\(([A-Z0-9]{10})\).*/\1/p'
}

# Extract "origin=... (TEAMID)" from spctl verbose line
extract_team_id_spctl() {
  sed -nE 's/.*origin=.*\(([A-Z0-9]{10})\).*/\1/p'
}

# ---- macOS verifiers ---------------------------------------------------------
verify_dmg_macos() {
  local dmg="$1"
  [[ -f "$dmg" ]] || { err "DMG not found: $dmg"; return 1; }
  have_cmd spctl || { err "spctl not found (macOS only)"; return 1; }

  log "Verifying DMG with spctl: $dmg"
  local out
  if ! out="$(spctl -a -vv -t open "$dmg" 2>&1)"; then
    err "spctl verification FAILED for $dmg"
    echo "$out" >&2
    return 1
  fi
  echo "$out" >&2
  local team
  team="$(echo "$out" | extract_team_id_spctl | head -n1 || true)"
  [[ -n "$team" ]] && enforce_team_id "$team"

  if have_cmd stapler; then
    log "Checking notarization (stapler validate): $dmg"
    if ! stapler validate "$dmg" >/dev/null 2>&1; then
      warn "Notarization stapler validate FAILED or not stapled for $dmg"
    else
      log "Notarization stapled OK for $dmg"
    fi
  else
    warn "stapler not found; skipping notarization check for $dmg"
  fi
}

verify_pkg_macos() {
  local pkg="$1"
  [[ -f "$pkg" ]] || { err "PKG not found: $pkg"; return 1; }
  have_cmd pkgutil || { err "pkgutil not found (macOS only)"; return 1; }
  have_cmd spctl || { err "spctl not found (macOS only)"; return 1; }

  log "Checking PKG signature with pkgutil: $pkg"
  local out
  if ! out="$(pkgutil --check-signature "$pkg" 2>&1)"; then
    err "pkgutil signature check FAILED for $pkg"
    echo "$out" >&2
    return 1
  fi
  echo "$out" >&2

  # Enforce Team ID if provided
  local team
  team="$(echo "$out" | extract_team_id_paren | head -n1 || true)"
  [[ -n "$team" ]] && enforce_team_id "$team"

  log "Evaluating install trust with spctl: $pkg"
  if ! out="$(spctl -a -vv -t install "$pkg" 2>&1)"; then
    err "spctl install evaluation FAILED for $pkg"
    echo "$out" >&2
    return 1
  fi
  echo "$out" >&2

  if have_cmd stapler; then
    log "Checking notarization (stapler validate): $pkg"
    if ! stapler validate "$pkg" >/dev/null 2>&1; then
      warn "Notarization stapler validate FAILED or not stapled for $pkg"
    else
      log "Notarization stapled OK for $pkg"
    fi
  else
    warn "stapler not found; skipping notarization check for $pkg"
  fi
}

# ---- Windows / cross-platform MSIX verifier (PowerShell) ---------------------
# Uses pwsh (PowerShell 7) if available, else Windows powershell.exe.
# On Linux/mac runners, verification works if pwsh is installed.
verify_msix_with_powershell() {
  local msix="$1"
  [[ -f "$msix" ]] || { err "MSIX not found: $msix"; return 1; }

  local PS=; if have_cmd pwsh; then PS="pwsh"; elif have_cmd powershell; then PS="powershell"; else PS=""; fi
  if [[ -z "$PS" ]]; then
    err "Neither 'pwsh' nor 'powershell' is available to verify MSIX: $msix"
    return 1
  fi

  # Build PowerShell snippet
  local expectedThumb="${CODE_SIGN_CERT_THUMBPRINT:-}"
  local expectedOrg="${WIN_CERT_ORG:-}"

  # shellcheck disable=SC2016
  local script='
param([string]$Path, [string]$ExpectedThumb, [string]$ExpectedOrg)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Path)) { throw "File not found: $Path" }
$sig = Get-AuthenticodeSignature -FilePath $Path
Write-Host ("Signer Subject: {0}" -f ($sig.SignerCertificate.Subject)) -ForegroundColor Cyan
Write-Host ("Thumbprint:     {0}" -f ($sig.SignerCertificate.Thumbprint)) -ForegroundColor Cyan
Write-Host ("Status:         {0}" -f ($sig.Status)) -ForegroundColor Cyan

if ($sig.Status -ne "Valid") {
  Write-Error ("MSIX signature status not Valid: {0}" -f $sig.Status)
  exit 2
}

if ($ExpectedThumb -and ($sig.SignerCertificate.Thumbprint -ne $ExpectedThumb)) {
  Write-Error ("Thumbprint mismatch. Expected: {0}  Got: {1}" -f $ExpectedThumb, $sig.SignerCertificate.Thumbprint)
  exit 3
}

if ($ExpectedOrg -and ($sig.SignerCertificate.Subject -notmatch [Regex]::Escape($ExpectedOrg))) {
  Write-Error ("Subject mismatch. Expected contains: {0}  Got: {1}" -f $ExpectedOrg, $sig.SignerCertificate.Subject)
  exit 4
}
'

  log "Verifying MSIX with PowerShell: $msix"
  set +e
  "$PS" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$script" -Path "$msix" -ExpectedThumb "$expectedThumb" -ExpectedOrg "$expectedOrg"
  local rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    err "MSIX verification FAILED for $msix (rc=$rc)"
    return 1
  fi
}

# ---- main --------------------------------------------------------------------
if [[ $# -lt 1 ]]; then
  cat >&2 <<USAGE
Usage: $0 <artifact1> [artifact2 ...]
Supported extensions:
  • .dmg (macOS)  • .pkg (macOS)  • .msix (Windows / cross-platform via PowerShell)
Environment checks (optional):
  APPLE_TEAM_ID, WIN_CERT_ORG, CODE_SIGN_CERT_THUMBPRINT
USAGE
  exit 2
fi

fail=0
for f in "$@"; do
  case "${f,,}" in
    *.dmg)
      if [[ "$OS" != "macos" ]]; then
        warn "Verifying DMG on non-macOS host may be unreliable; skipping $f"
      else
        verify_dmg_macos "$f" || fail=1
      fi
      ;;
    *.pkg)
      if [[ "$OS" != "macos" ]]; then
        warn "Verifying PKG on non-macOS host may be unreliable; skipping $f"
      else
        verify_pkg_macos "$f" || fail=1
      fi
      ;;
    *.msix)
      verify_msix_with_powershell "$f" || fail=1
      ;;
    *)
      warn "Unsupported file type (skipping): $f"
      ;;
  esac
done

if [[ $fail -ne 0 ]]; then
  die "One or more verifications FAILED."
else
  log "All verifications completed successfully."
fi
