#!/usr/bin/env bash
set -euo pipefail

# Usage: ./fetch_intel_pcs_chain.sh 00906ED10000
FMSPC="${1:-00906ED10000}"
OUT="${2:-~/animica/proofs/attestations/vendor_roots/intel_pcs_issuer_chain.pem}"

# Hit PCS v4 TCB endpoint and extract the URL-encoded issuer-chain header.
URL="https://api.trustedservices.intel.com/sgx/certification/v4/tcb?fmspc=${FMSPC}"
HDR=$(curl -fsSLI "$URL" | tr -d '\r' | grep -i '^SGX-TCB-Info-Issuer-Chain:' || true)

if [[ -z "$HDR" ]]; then
  echo "[-] Could not read the issuer-chain header from Intel PCS for FMSPC=${FMSPC}" >&2
  exit 1
fi

CHAIN_URLENC=$(echo "$HDR" | sed -E 's/^SGX-TCB-Info-Issuer-Chain:[[:space:]]*//I')
# URL-decode into PEMs
python3 - <<PY > "$OUT"
import sys, urllib.parse
print(urllib.parse.unquote(sys.stdin.read()))
PY
echo "$CHAIN_URLENC" | python3 - "$OUT" >/dev/null

# Quick sanity:
openssl crl2pkcs7 -nocrl -certfile "$OUT" | openssl pkcs7 -print_certs -noout >/dev/null 2>&1 \
  && echo "[+] Intel PCS issuer chain written to $OUT" \
  || { echo "[-] Wrote, but OpenSSL couldn't parse certificates; inspect $OUT" ; exit 1; }
