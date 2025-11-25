#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-$HOME/animica/proofs/attestations/vendor_roots}"
mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"

FMSPCS=(
  00906ED10000
  00A066050000
  00906ED30000
  00606A000000
)

ENDPOINTS=(
  "https://api.trustedservices.intel.com/sgx/certification/v4/tcb?fmspc=%s"
  "https://api.trustedservices.intel.com/sgx/certification/v3/tcb?fmspc=%s"
)

HEADER_NAMES=(
  "SGX-TCB-Info-Issuer-Chain"
  "SGX-PCK-Certificate-Issuer-Chain"
)

decode_and_write() {
  local encoded="$1"
  python3 - "$encoded" "$TMP/issuer_chain.pem" <<'PY'
import sys, urllib.parse, re, pathlib
enc=sys.argv[1]
out=sys.argv[2]
pem = urllib.parse.unquote(enc)
certs = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
if not certs:
    sys.exit(42)
pathlib.Path(out).write_text("\n".join(certs) + "\n")
print(f"[+] Wrote {out} with {len(certs)} cert(s)")
PY
}

got=0
for fmspc in "${FMSPCS[@]}"; do
  for ep in "${ENDPOINTS[@]}"; do
    url=$(printf "$ep" "$fmspc")
    echo "[*] GET $url"
    hdrs="$(curl -sD - -o /dev/null "$url" || true)"
    for hn in "${HEADER_NAMES[@]}"; do
      line="$(printf "%s\n" "$hdrs" | grep -i "^$hn:" | sed -E "s/^$hn:[[:space:]]*//I")"
      if [[ -n "${line:-}" ]]; then
        echo "[+] Found header $hn for FMSPC=$fmspc"
        if decode_and_write "$line"; then
          got=1
        fi
      fi
    done
  done
done

if [[ "$got" -eq 0 ]]; then
  echo "[-] Could not obtain issuer chain from Intel PCS (tried ${#FMSPCS[@]} FMSPCs on v3/v4)."
  echo "    Check network egress and try again later."
  exit 1
fi

# Split chain to identify root candidate (usually last cert)
csplit -z -f "$TMP/cert" -b "%02d.pem" "$TMP/issuer_chain.pem" '/-----BEGIN CERTIFICATE-----/' '{*}' >/dev/null 2>&1 || true

cp "$TMP/issuer_chain.pem" "$OUT_DIR/issuer_chain.pem"
root="$(ls -1 "$TMP"/cert*.pem 2>/dev/null | tail -n1 || true)"
if [[ -n "$root" && -s "$root" ]]; then
  cp "$root" "$OUT_DIR/intel_sgx_root.pem"
  echo "[+] Root candidate copied to $OUT_DIR/intel_sgx_root.pem"
fi

echo "[i] Inspecting cert subjects/issuers:"
for f in "$OUT_DIR"/issuer_chain.pem "$OUT_DIR"/intel_sgx_root.pem; do
  [[ -s "$f" ]] || continue
  echo "---- $f ----"
  openssl x509 -in "$f" -noout -subject -issuer -fingerprint -sha256 -dates || true
done

echo "[✓] Files in $OUT_DIR:"
ls -l "$OUT_DIR"
