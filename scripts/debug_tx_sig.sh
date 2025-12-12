#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_PATH="${1:-/tmp/animica_tx_debug.json}"

info() {
  echo "[debug-tx] $*"
}

if ! command -v animica >/dev/null 2>&1; then
  echo "animica CLI not found in PATH" >&2
  exit 1
fi

info "Generating wallets and funding with faucet"
a_out=$(animica wallet create --label a)
b_out=$(animica wallet create --label b)

a_addr=$(echo "$a_out" | awk '/Address:/ {print $2; exit}')
b_addr=$(echo "$b_out" | awk '/Address:/ {print $2; exit}')

if [[ -z "$a_addr" || -z "$b_addr" ]]; then
  echo "Failed to parse wallet addresses" >&2
  exit 1
fi

animica faucet request "$a_addr"

info "Building dry-run transaction and capturing artifact"
animica tx send --from "$a_addr" --to "$b_addr" --value 1000 --dry-run --raw-out "$ARTIFACT_PATH" -v

info "Verifying signature artifact locally"
python - <<'PY'
import json, sys
from pathlib import Path

from pq.py import verify
from pq.py.sign import Signature

artifact_path = Path(sys.argv[1])
artifact = json.loads(artifact_path.read_text())

signing = artifact["signing"]
msg = bytes.fromhex(signing["preimage_hex"])
sig_bytes = bytes.fromhex(signing["signature_hex"])
pub = bytes.fromhex(signing["public_key_hex"])
chain_id = int(artifact["tx"]["chainId"])

sig_env = Signature(
    alg_id=signing["algorithm"]["id"],
    alg_name=signing["algorithm"]["name"],
    domain=signing.get("domain", "tx"),
    prehash=signing.get("prehash", "sha3-512"),
    sig=sig_bytes,
)

if not verify.verify_detached(msg, sig_env, pub, chain_id=chain_id):
    raise SystemExit("local PQ verification failed")
print("local PQ verification: ok")
PY "$ARTIFACT_PATH"

info "Broadcasting transaction now that verification passed"
animica tx send --from "$a_addr" --to "$b_addr" --value 1000 -v

info "Debug artifact stored at $ARTIFACT_PATH"
