#!/usr/bin/env bash
set -euo pipefail

# 2-node relay + validation test for PQ signed transactions.
# Usage:
#   scripts/test_pq_relay_two_nodes.sh http://127.0.0.1:8545 http://127.0.0.1:9545 0x<raw_signed_tx>

RPC_A=${1:-http://127.0.0.1:8545}
RPC_B=${2:-http://127.0.0.1:9545}
RAW_TX=${3:-}

if [[ -z "${RAW_TX}" ]]; then
  echo "missing raw tx hex"
  exit 2
fi

call() {
  local rpc=$1
  local method=$2
  local params=$3
  curl -sS -H 'content-type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"${method}\",\"params\":${params}}" \
    "${rpc}"
}

json_get_result() {
  python -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("result")))'
}

echo "[1/6] submit tx on node A"
SUBMIT=$(call "$RPC_A" "tx.sendRawTransaction" "[\"${RAW_TX}\"]")
echo "$SUBMIT"
TXID=$(echo "$SUBMIT" | python -c 'import json,sys;d=json.load(sys.stdin);print(d.get("result","") or "")')

if [[ -z "$TXID" ]]; then
  echo "submission failed"
  exit 1
fi

echo "[2/6] verify on node B"
call "$RPC_B" "tx.debugVerify" "[\"${TXID}\"]" || true

echo "[3/6] trigger peer import on node B"
call "$RPC_B" "p2p.importPeerKnownTxs" "[128]" | json_get_result

echo "[4/6] wait for relay"
sleep 3

echo "[5/6] check relay state on node B"
call "$RPC_B" "debug.txRelay" "[\"${TXID}\"]" | json_get_result

echo "[6/6] check pending mempool on node B"
call "$RPC_B" "mempool.getPending" "[]" | json_get_result
