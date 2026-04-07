#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
SMOKE_DIR="${ANIMICA_STUDIO_WALLET_SMOKE_DIR:-/tmp/animica-studio-wallet-smoke}"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export ANIMICA_STUDIO_APP_DATA_DIR="${ANIMICA_STUDIO_APP_DATA_DIR:-/tmp/animica-studio-smoke}"
export ANIMICA_WALLETS_FILE="$SMOKE_DIR/wallets.json"

mkdir -p "$SMOKE_DIR"
rm -f "$ANIMICA_WALLETS_FILE"

cd "$ROOT"

echo "[smoke_wallet_flow] live wallet create"
timeout 30s "$PYTHON" -m animica wallet create --label studio_smoke --alg dilithium3

echo "[smoke_wallet_flow] live wallet list"
timeout 30s "$PYTHON" -m animica wallet list

ADDRESS="$(grep -o '"address": "[^"]*"' "$ANIMICA_WALLETS_FILE" | head -n1 | cut -d'"' -f4)"
if [[ -z "$ADDRESS" ]]; then
  echo "failed to resolve created wallet address from $ANIMICA_WALLETS_FILE" >&2
  exit 1
fi

echo "[smoke_wallet_flow] live wallet show $ADDRESS"
set +e
SHOW_OUTPUT="$(timeout 30s "$PYTHON" -m animica wallet show "$ADDRESS" 2>&1)"
SHOW_RC=$?
set -e
printf '%s\n' "$SHOW_OUTPUT"
if [[ $SHOW_RC -ne 0 ]]; then
  if grep -q "Failed to fetch balance from chain" <<<"$SHOW_OUTPUT"; then
    echo "[smoke_wallet_flow] wallet show reached RPC-dependent path; continuing without live node"
  else
    exit "$SHOW_RC"
  fi
fi

echo "[smoke_wallet_flow] pytest wallet/balance/send smoke"
"$PYTHON" -m pytest -q \
  apps/animica_studio/tests/test_wallet.py \
  apps/animica_studio/tests/test_dashboard_services.py \
  apps/animica_studio/tests/test_tx_service.py
