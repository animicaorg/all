#!/usr/bin/env bash
# make_selfsigned.sh — generate a dev CA (if missing) and a server cert with SANs.
# Usage:
#   ./make_selfsigned.sh \
#     --cn dev.animica.local \
#     --hosts localhost,127.0.0.1,::1,dev.animica.local \
#     --out ./ops/tls/dev \
#     --days 825
#
# Notes:
# - DEV-ONLY. Do NOT use in production.
# - Reuses ./selfsigned-ca.{pem,key} if present; otherwise creates a new EC (P-256) CA.
# - Emits: server.key, server.csr, server.crt, server.fullchain.pem, ca.pem, ca.srl
# - Requires: openssl

set -euo pipefail

CN="${CN:-dev.animica.local}"
HOSTS="${HOSTS:-localhost,127.0.0.1,::1,${CN}}"
OUT="${OUT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
DAYS="${DAYS:-825}" # ~27 months; browsers cap for self-signed anyway
KEY_TYPE="${KEY_TYPE:-ec}"    # ec|rsa
RSA_BITS="${RSA_BITS:-4096}"

print_help() {
  cat <<EOF
make_selfsigned.sh — generate dev CA (if missing) and self-signed server cert.

Options:
  --cn <name>        Common Name for server cert (default: ${CN})
  --hosts <list>     Comma-separated DNS/IP SANs (default: ${HOSTS})
  --out <dir>        Output directory (default: ${OUT})
  --days <n>         Validity days (default: ${DAYS})
  --key-type <ec|rsa>Key type for server key (default: ${KEY_TYPE})
  --rsa-bits <n>     RSA bits if key-type=rsa (default: ${RSA_BITS})
  -h|--help          Show this help

Environment variables of the same names also supported (CN, HOSTS, OUT, DAYS, KEY_TYPE, RSA_BITS).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cn) CN="$2"; shift 2;;
    --hosts) HOSTS="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --days) DAYS="$2"; shift 2;;
    --key-type) KEY_TYPE="$2"; shift 2;;
    --rsa-bits) RSA_BITS="$2"; shift 2;;
    -h|--help) print_help; exit 0;;
    *) echo "Unknown arg: $1" >&2; print_help; exit 1;;
  esac
done

command -v openssl >/dev/null 2>&1 || { echo "openssl not found"; exit 1; }
mkdir -p "${OUT}"

CA_PEM="${OUT}/selfsigned-ca.pem"
CA_KEY="${OUT}/selfsigned-ca.key"
CA_SRL="${OUT}/selfsigned-ca.srl"

SRV_KEY="${OUT}/server.key"
SRV_CSR="${OUT}/server.csr"
SRV_CRT="${OUT}/server.crt"
SRV_FULLCHAIN="${OUT}/server.fullchain.pem"
SRV_P12="${OUT}/server.p12"

TMPCFG="$(mktemp)"
trap 'rm -f "${TMPCFG}"' EXIT

# Build an OpenSSL config with SANs
cat > "${TMPCFG}" <<CFG
[ req ]
default_bits       = 256
default_md         = sha256
prompt             = no
distinguished_name = dn
req_extensions     = req_ext

[ dn ]
CN = ${CN}

[ req_ext ]
subjectAltName = @alt_names

[ v3_ca ]
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always,issuer
basicConstraints = critical, CA:true, pathlen:1
keyUsage = critical, keyCertSign, cRLSign

[ v3_server ]
authorityKeyIdentifier=keyid,issuer
basicConstraints = CA:false
keyUsage = critical, digitalSignature, keyEncipherment, keyAgreement
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[ alt_names ]
CFG

# Add default loopback SANs first to be safe
DNS_i=1
IP_i=1
echo "DNS.${DNS_i}=localhost" >> "${TMPCFG}"; DNS_i=$((DNS_i+1))
echo "IP.${IP_i}=127.0.0.1"   >> "${TMPCFG}"; IP_i=$((IP_i+1))
echo "IP.${IP_i}=::1"         >> "${TMPCFG}"; IP_i=$((IP_i+1))

# Parse HOSTS and append
IFS=',' read -r -a HOST_ARR <<< "${HOSTS}"
for H in "${HOST_ARR[@]}"; do
  H_TRIM="$(echo -n "${H}" | sed 's/^ *//; s/ *$//')"
  [[ -z "${H_TRIM}" ]] && continue
  if [[ "${H_TRIM}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ || "${H_TRIM}" =~ : ]]; then
    echo "IP.${IP_i}=${H_TRIM}"   >> "${TMPCFG}"; IP_i=$((IP_i+1))
  else
    echo "DNS.${DNS_i}=${H_TRIM}" >> "${TMPCFG}"; DNS_i=$((DNS_i+1))
  fi
done

# Ensure a dev CA exists (reuse if already present)
if [[ ! -s "${CA_PEM}" || ! -s "${CA_KEY}" ]]; then
  echo ">> Generating dev CA (EC P-256) at ${CA_PEM}"
  openssl ecparam -name prime256v1 -genkey -noout -out "${CA_KEY}"
  openssl req -x509 -new -key "${CA_KEY}" -sha256 -days $((DAYS*2)) \
    -subj "/CN=Animica Dev CA 000" \
    -extensions v3_ca -config "${TMPCFG}" \
    -out "${CA_PEM}"
else
  echo ">> Using existing dev CA at ${CA_PEM}"
fi

# Generate server key
if [[ "${KEY_TYPE}" == "rsa" ]]; then
  echo ">> Generating RSA-${RSA_BITS} server key"
  openssl genrsa -out "${SRV_KEY}" "${RSA_BITS}"
else
  echo ">> Generating EC P-256 server key"
  openssl ecparam -name prime256v1 -genkey -noout -out "${SRV_KEY}"
fi

# CSR with SANs
echo ">> Creating CSR for CN=${CN} with SANs: ${HOSTS}"
openssl req -new -key "${SRV_KEY}" -sha256 -out "${SRV_CSR}" -config "${TMPCFG}"

# Sign with CA
echo ">> Signing server certificate (valid ${DAYS} days)"
openssl x509 -req -in "${SRV_CSR}" \
  -CA "${CA_PEM}" -CAkey "${CA_KEY}" -CAcreateserial -CAserial "${CA_SRL}" \
  -out "${SRV_CRT}" -days "${DAYS}" -sha256 \
  -extfile "${TMPCFG}" -extensions v3_server

# Full chain (server cert + CA)
cat "${SRV_CRT}" "${CA_PEM}" > "${SRV_FULLCHAIN}"

# Optional PKCS#12 (empty password)
if openssl pkcs12 -export -out "${SRV_P12}" -inkey "${SRV_KEY}" -in "${SRV_CRT}" -certfile "${CA_PEM}" -passout pass: 2>/dev/null; then
  :
else
  echo ">> Skipping PKCS#12 export (openssl may require a password in your build)."
  rm -f "${SRV_P12}" || true
fi

echo
echo "==> Done."
echo "Artifacts in: ${OUT}"
ls -l "${SRV_KEY}" "${SRV_CRT}" "${SRV_FULLCHAIN}" "${CA_PEM}" 2>/dev/null || true
echo
echo "Hints:"
echo "  - To trust the CA locally: import ${CA_PEM} into your OS/browser trust store."
echo "  - Typical reverse-proxy config:"
echo "      ssl_certificate     ${SRV_FULLCHAIN};"
echo "      ssl_certificate_key ${SRV_KEY};"
