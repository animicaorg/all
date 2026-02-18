#!/usr/bin/env bash
set -euo pipefail

RPC_URL="${ANIMICA_RPC_URL:-http://127.0.0.1:8545/rpc}"

echo "[1/5] stopping node"
animica node down || true

echo "[2/5] backup + wipe (chain reset)"
animica chain reset --force --rpc-url "$RPC_URL"

echo "[3/5] starting node"
animica node up

echo "[4/5] verify head"
animica chain head --rpc-url "$RPC_URL"

echo "[5/5] done"
