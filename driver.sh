#!/usr/bin/env bash
set -euo pipefail

# Go to repo root
cd /root/animica

# Activate venv
if [[ -d ".venv" && -f ".venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
else
  echo "[animica-dev] .venv missing. Run ./setup.sh first."
  exit 1
fi

# Default to mainnet
export ANIMICA_NETWORK="${ANIMICA_NETWORK:-mainnet}"
export ANIMICA_CHAIN_ID="${ANIMICA_CHAIN_ID:-1}"

echo "[animica-dev] Network: $ANIMICA_NETWORK (chain_id=$ANIMICA_CHAIN_ID)"

# Check if node is up; if not, start it
if animica node status >/dev/null 2>&1; then
  echo "[animica-dev] Node already running."
else
  echo "[animica-dev] Node not running; starting..."
  animica node up
fi

echo "[animica-dev] Ready. Try: animica wallet list"