#!/usr/bin/env bash
set -euo pipefail

# Choose family: Milan|Genoa   (SNP-era EPYC families)
GEN="${1:-Milan}"
OUT="${2:-~/animica/proofs/attestations/vendor_roots/amd_sev_snp_chain.pem}"

URL="https://kdsintf.amd.com/vcek/v1/${GEN}/cert_chain"
curl -fsSL "$URL" -o "$OUT"

# Quick sanity:
openssl crl2pkcs7 -nocrl -certfile "$OUT" | openssl pkcs7 -print_certs -noout >/dev/null 2>&1 \
  && echo "[+] AMD ${GEN} cert_chain written to $OUT" \
  || { echo "[-] Downloaded, but OpenSSL couldn't parse certificates; inspect $OUT" ; exit 1; }
