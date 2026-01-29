#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$ROOT_DIR/intel_sgx_root.pem"
CHAIN="$ROOT_DIR/issuer_chain.pem"

FMSPCS=(00906ED10000 00A066050000 00906ED30000 00606A000000)
URL_BASE="https://api.trustedservices.intel.com/sgx/certification/v4/tcb?fmspc="

echo "[*] Fetching Intel PCS TCB issuer chain header..."
found=0
raw=""
for fp in "${FMSPCS[@]}"; do
  echo "    Trying FMSPC=$fp"
  # Get headers only (-D -) and discard body (-o /dev/null)
  if headers="$(curl -sS -D - -o /dev/null "${URL_BASE}${fp}")"; then
    # Parse a (possibly folded) header value in Python (tries both header names)
    hdr="$(printf "%s" "$headers" | python3 - <<'PY'
import sys, re
text = sys.stdin.read().splitlines()
def get(name):
    cap = False; buf=[]
    for i,ln in enumerate(text):
        low = ln.lower()
        if not cap and low.startswith(name+':'):
            buf.append(ln.split(':',1)[1].strip()); cap=True; continue
        if cap:
            if ln.startswith((' ', '\t')):  # folded continuation line
                buf.append(ln.strip()); continue
            if re.match(r'^[!-9;-~]+:', ln):  # next header
                break
    return ''.join(buf)
v = get('tcb-info-issuer-chain') or get('sgx-enclave-identity-issuer-chain')
print(v, end="")
PY
)"
    if [ -n "${hdr:-}" ]; then raw="$hdr"; found=1; break; fi
  fi
done

if [ $found -eq 0 ]; then
  echo "[-] Could not read the issuer-chain header from Intel PCS." >&2
  exit 1
fi

echo "[*] Decoding URL-encoded PEM chain → $CHAIN"
printf "%s" "$raw" | python3 - "$CHAIN" <<'PY'
import sys, urllib.parse, pathlib
out = pathlib.Path(sys.argv[1])
out.write_text(urllib.parse.unquote(sys.stdin.read()))
print(f"[+] Wrote issuer chain to {out}")
PY

# Some responses separate certs with commas — normalize.
sed -i 's/,/\n/g' "$CHAIN"

TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

# Split chain into individual PEMs
python3 - "$CHAIN" "$TMPD" <<'PY'
import sys, re, pathlib
chain, out = map(pathlib.Path, sys.argv[1:3])
data = chain.read_text()
certs = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', data, re.S)
if not certs:
    print("[-] No PEM certificates found in issuer chain.", file=sys.stderr); sys.exit(2)
for i,c in enumerate(certs,1):
    p = out / f"cert{i:02d}.pem"; p.write_text(c+"\n"); print(f"[+] {p}")
PY

# Choose self-signed root (issuer==subject); else last in chain
root=""
for f in "$TMPD"/cert*.pem; do
  sub="$(openssl x509 -in "$f" -noout -subject | sed 's/^subject= *//')"
  iss="$(openssl x509 -in "$f" -noout -issuer  | sed 's/^issuer= *//')"
  if [ "$sub" = "$iss" ]; then root="$f"; fi
done
if [ -z "$root" ]; then
  root="$(ls "$TMPD"/cert*.pem | sort | tail -n1)"
  echo "[!] Self-signed not detected; using last in chain: $root"
fi

cp "$root" "$OUT"
chmod 0644 "$OUT"
echo "[*] Saved Intel SGX Root CA → $OUT"
echo "[*] Sanity check:"
openssl x509 -in "$OUT" -noout -subject -issuer -fingerprint -sha256 -dates
