#!/usr/bin/env bash
# Animica — Sparkle (v2) Ed25519 signer helper for macOS CI
#
# Signs a DMG/PKG with an Ed25519 private key and prints the base64 signature
# suitable for the Sparkle appcast enclosure@sparkle:edSignature attribute.
#
# Prefers Sparkle's official `sign_update` tool if available; otherwise falls
# back to a Python signer (cryptography → pynacl).
#
# Usage:
#   installers/updates/scripts/sign_appcast_macos.sh \
#     --key installers/wallet/macos/sparkle/ed25519_private.pem \
#     --artifact dist/Animica-Wallet-1.4.3.dmg \
#     [--tool /opt/homebrew/bin/sign_update] \
#     [--sig-out sig.txt] \
#     [--json-out sig_meta.json]
#
# Env:
#   SPARKLE_PRIV_PASSPHRASE   Optional passphrase for encrypted PEM (will prompt if needed)
#   SIGN_UPDATE               Optional path to Sparkle sign_update tool
#
# Output:
#   - Writes signature to STDOUT (base64, single line)
#   - If --sig-out provided, writes signature there (no newline)
#   - If --json-out provided, writes JSON with {path,length,sha256,ed25519_signature_b64}

set -euo pipefail

die() { echo "[-] $*" >&2; exit 2; }
info() { echo "[*] $*" >&2; }
ok()  { echo "[✓] $*" >&2; }

# --- arg parsing ---
KEY=""
ART=""
SIG_OUT=""
JSON_OUT=""
SIGN_UPDATE_TOOL="${SIGN_UPDATE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --key) KEY="${2:-}"; shift 2;;
    --artifact) ART="${2:-}"; shift 2;;
    --sig-out) SIG_OUT="${2:-}"; shift 2;;
    --json-out) JSON_OUT="${2:-}"; shift 2;;
    --tool) SIGN_UPDATE_TOOL="${2:-}"; shift 2;;
    -h|--help) sed -n '1,80p' "$0"; exit 0;;
    *) die "Unknown arg: $1";;
  esac
done

[[ -n "$KEY" ]] || die "--key <ed25519_private.pem> is required"
[[ -n "$ART" ]] || die "--artifact <path/to.dmg|pkg> is required"
[[ -f "$KEY" ]] || die "Key not found: $KEY"
[[ -f "$ART" ]] || die "Artifact not found: $ART"

# --- compute size & sha256 (for metadata/debug) ---
# macOS has shasum; fall back to openssl if needed.
if command -v shasum >/dev/null 2>&1; then
  SHA256="$(shasum -a 256 "$ART" | awk '{print $1}')"
elif command -v openssl >/dev/null 2>&1; then
  SHA256="$(openssl dgst -sha256 "$ART" | awk '{print $2}')"
else
  die "Neither shasum nor openssl found to compute sha256"
fi

# portable size
if stat -f %z "$ART" >/dev/null 2>&1; then
  LENGTH="$(stat -f %z "$ART")"
else
  LENGTH="$(wc -c < "$ART" | tr -d ' ')"
fi

info "artifact: $ART"
info "length:   $LENGTH bytes"
info "sha256:   $SHA256"

# --- detect or validate sign_update ---
detect_sign_update() {
  local cands=(
    "$SIGN_UPDATE_TOOL"
    "/opt/homebrew/bin/sign_update"
    "/usr/local/bin/sign_update"
    "/usr/bin/sign_update"
    "/Applications/Sparkle.framework/Versions/B/Resources/sign_update"
    "/Library/Frameworks/Sparkle.framework/Versions/B/Resources/sign_update"
  )
  for c in "${cands[@]}"; do
    [[ -n "$c" && -x "$c" ]] && { echo "$c"; return 0; }
  done
  return 1
}

SIGN_TOOL=""
if SIGN_TOOL="$(detect_sign_update)"; then
  ok "using Sparkle tool: $SIGN_TOOL"
else
  info "Sparkle sign_update not found; will use Python fallback"
fi

# --- read passphrase if encrypted and not supplied ---
PEM_PW="${SPARKLE_PRIV_PASSPHRASE:-}"
if grep -qE 'ENCRYPTED|BEGIN ENCRYPTED PRIVATE KEY' "$KEY" && [[ -z "$PEM_PW" ]]; then
  read -r -s -p "PEM passphrase: " PEM_PW
  echo >&2
fi

SIGNATURE_B64=""

if [[ -n "$SIGN_TOOL" ]]; then
  # Sparkle's sign_update prints: "edSignature: <base64>" (format varies slightly by version)
  # We'll greedily extract the last base64 token on the final line.
  set +e
  OUT="$("$SIGN_TOOL" "$KEY" "$ART" 2>&1)"
  RC=$?
  set -e
  if [[ $RC -ne 0 ]]; then
    info "sign_update failed (rc=$RC):"
    echo "$OUT" >&2
    info "falling back to Python signer…"
  else
    # common outputs include lines with 'Signature:' or 'DSA' (for old), we want the last b64-like token
    SIGNATURE_B64="$(echo "$OUT" | tr -d '\r' | tail -n1 | grep -Eo '([A-Za-z0-9+/=]{64,})' | tail -n1 || true)"
    if [[ -z "$SIGNATURE_B64" ]]; then
      # try broader search
      SIGNATURE_B64="$(echo "$OUT" | grep -Eo 'edSignature[:= ]+([A-Za-z0-9+/=]+)' | awk -F'[ =]+' '{print $2}' | tail -n1 || true)"
    fi
  fi
fi

if [[ -z "$SIGNATURE_B64" ]]; then
  # --- Python fallback (cryptography → pynacl) ---
  PYTHON_BIN="$(command -v python3 || true)"
  [[ -n "$PYTHON_BIN" ]] || die "python3 not found for fallback signer"
  SIGNATURE_B64="$("$PYTHON_BIN" - <<'PY' "$KEY" "$ART" "$PEM_PW"
import base64, sys, mmap, os
from pathlib import Path

key_path = Path(sys.argv[1])
art_path = Path(sys.argv[2])
pem_pw = sys.argv[3] if len(sys.argv) > 3 else ""

# Try cryptography first
try:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = load_pem_private_key(key_path.read_bytes(), password=(pem_pw.encode() if pem_pw else None))
    if not isinstance(key, Ed25519PrivateKey):
        print("ERROR: Not an Ed25519 key in PEM", file=sys.stderr)
        sys.exit(3)
    with art_path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        sig = key.sign(mm)
    print(base64.b64encode(sig).decode(), end="")
    sys.exit(0)
except Exception as e:
    cryptography_err = e

# Fallback to PyNaCl if provided a raw 32-byte seed
try:
    from nacl import signing
    seed = key_path.read_bytes()[:32]
    if len(seed) != 32:
        print("ERROR: PyNaCl fallback requires a raw 32-byte seed file", file=sys.stderr)
        sys.exit(3)
    sk = signing.SigningKey(seed)
    sig = sk.sign(art_path.read_bytes()).signature
    print(base64.b64encode(sig).decode(), end="")
    sys.exit(0)
except Exception as e:
    print(f"ERROR: crypto libs unavailable. cryptography_error={cryptography_err} pynacl_error={e}", file=sys.stderr)
    sys.exit(3)
PY
)"
  [[ -n "$SIGNATURE_B64" ]] || die "Failed to compute Ed25519 signature (fallback)"
  ok "signature computed via Python fallback"
else
  ok "signature computed via Sparkle sign_update"
fi

# --- outputs ---
echo -n "$SIGNATURE_B64"

if [[ -n "$SIG_OUT" ]]; then
  printf "%s" "$SIGNATURE_B64" > "$SIG_OUT"
  ok "wrote signature to $SIG_OUT"
fi

if [[ -n "$JSON_OUT" ]]; then
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  cat > "$JSON_OUT" <<JSON
{
  "path": "$(python3 -c 'import os,sys;print(os.path.abspath(sys.argv[1]))' "$ART" 2>/dev/null || realpath "$ART")",
  "length": $LENGTH,
  "sha256": "$SHA256",
  "ed25519_signature_b64": "$SIGNATURE_B64",
  "createdAt": "$ts"
}
JSON
  ok "wrote metadata to $JSON_OUT"
fi
