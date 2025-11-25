#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/animica}"
OUT_DIR="$ROOT/proofs/fixtures"
TOOLS_DIR="$ROOT/proofs/fixtures/tools"
WORK="${WORK:-$TOOLS_DIR/_work}"
mkdir -p "$OUT_DIR" "$WORK"

echo "[*] Checking for SGX device nodes..."
if [[ ! -e /dev/sgx_enclave && ! -e /dev/sgx/enclave ]]; then
  echo "[-] No SGX enclave device found (/dev/sgx_enclave or /dev/sgx/enclave)."
  echo "    You need an SGX-capable host/VM (e.g., Azure DCsv3/DCasv5) with SGX enabled."
  exit 2
fi

echo "[*] Installing Intel SGX DCAP dependencies (requires sudo)..."
if command -v apt-get >/dev/null 2>&1; then
  # Intel SGX repo key & list (Ubuntu 22.04 "jammy")
  sudo mkdir -p /etc/apt/keyrings
  curl -fsSL https://download.01.org/intel-sgx/sgx_repo/ubuntu/intel-sgx-deb.key | sudo gpg --dearmor -o /etc/apt/keyrings/intel-sgx.gpg
  echo "deb [signed-by=/etc/apt/keyrings/intel-sgx.gpg] https://download.01.org/intel-sgx/sgx_repo/ubuntu jammy main" | sudo tee /etc/apt/sources.list.d/intel-sgx.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y \
    libsgx-enclave-common \
    libsgx-aesm-service \
    libsgx-dcap-ql libsgx-dcap-default-qpl \
    libsgx-dcap-quote-verify-dev \
    build-essential cmake git pkg-config
  echo "[*] Ensuring AESM is running..."
  sudo systemctl enable --now aesmd || true
else
  echo "[-] apt-get not found; install SGX DCAP stack for your distro, then re-run."
  exit 3
fi

echo "[*] Cloning Intel DCAP QuoteGenerationSample..."
cd "$WORK"
if [[ ! -d SGXDataCenterAttestationPrimitives ]]; then
  git clone --depth=1 https://github.com/intel/SGXDataCenterAttestationPrimitives.git
fi

echo "[*] Building QuoteGenerationSample..."
cd SGXDataCenterAttestationPrimitives/SampleCode/QuoteGenerationSample
make clean && make -j"$(nproc)"

echo "[*] Running QuoteGenerationSample (this calls the Quoting Enclave via AESM)..."
./app || { echo "[-] Quote generation failed (is PCK provisioned and AESM healthy?)"; exit 4; }

if [[ ! -f quote.dat ]]; then
  echo "[-] quote.dat not produced. Check AESM logs and DCAP provisioning."
  exit 5
fi

TARGET="$OUT_DIR/sgx_quote.bin"
META="$OUT_DIR/sgx_quote.meta.json"
cp -f quote.dat "$TARGET"

# Minimal metadata (no sensitive fields)
SIZE=$(stat -c%s "$TARGET")
SHA=$(sha256sum "$TARGET" | awk '{print $1}')
DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "$META" <<JSON
{"path":"proofs/fixtures/sgx_quote.bin","bytes":$SIZE,"sha256":"$SHA","generated_at_utc":"$DATE"}
JSON

echo "[+] Wrote real quote → $TARGET ($SIZE bytes)"
echo "[+] Meta → $META"
