#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-$HOME/animica/proofs/attestations/vendor_roots}"
mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# AMD SEV-SNP KDS families commonly seen in the wild.
FAMILIES=(Milan Genoa Bergamo Siena Turin)

fetch_family() {
  local fam="$1"
  local url="https://kdsintf.amd.com/vcek/v1/${fam}/cert_chain"
  echo "[*] GET $url"
  if ! curl -fsSLo "$TMP/${fam}.pem" "$url"; then
    echo "[-] ${fam}: fetch failed (skipping)"; return 1
  fi
  # Sanity check: must contain at least one PEM block.
  if ! grep -q "BEGIN CERTIFICATE" "$TMP/${fam}.pem"; then
    echo "[-] ${fam}: no PEM blocks found (skipping)"; return 1
  fi
  echo "[+] ${fam}: retrieved cert_chain"
}

for fam in "${FAMILIES[@]}"; do
  fetch_family "$fam" || true
done

# Aggregate & dedupe all found PEM certs across families.
python3 - "$OUT_DIR/amd_sev_snp_root.pem" "$TMP"/*.pem <<'PY'
import sys, re, pathlib, glob
out = pathlib.Path(sys.argv[1])
blocks = set()
for p in sys.argv[2:]:
    try:
        data = pathlib.Path(p).read_text(errors="ignore")
    except Exception:
        continue
    for m in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", data, re.S):
        blocks.add(m.strip())
if not blocks:
    print("[-] No AMD SNP certs collected; exiting.", file=sys.stderr)
    sys.exit(1)
# Write in stable order
out.write_text("\n".join(sorted(blocks)) + "\n")
print(f"[+] Wrote {len(blocks)} unique cert(s) → {out}")
PY

echo "[i] Inspecting subjects/issuers:"
if command -v openssl >/dev/null 2>&1; then
  awk 'BEGIN{RS="-----END CERTIFICATE-----"} /BEGIN CERTIFICATE/ {print $0 RS > "/dev/fd/3"}' 3> >( # split stream for loop
    while IFS= read -r -d '' cert; do
      tmpfile="$(mktemp)"; printf "%s" "$cert" > "$tmpfile"
      echo "---- cert ----"
      openssl x509 -in "$tmpfile" -noout -subject -issuer -fingerprint -sha256 -dates || true
      rm -f "$tmpfile"
    done
  ) < "$OUT_DIR/amd_sev_snp_root.pem"
fi

echo "[✓] Final file:"
ls -l "$OUT_DIR/amd_sev_snp_root.pem"
