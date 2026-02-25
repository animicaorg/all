#!/usr/bin/env bash
set -euo pipefail

RPC_URL="${1:-http://127.0.0.1:8545/rpc}"
DA_DIR="${2:-/data/da}"
MAX_BYTES="${3:-10737418240}"

status_payload='{"jsonrpc":"2.0","id":1,"method":"da.getStatus","params":{}}'
configure_payload="{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"da.configure\",\"params\":{\"enabled\":true,\"dir\":\"${DA_DIR}\",\"max_bytes\":${MAX_BYTES}}}"

echo "# BEFORE: da.getStatus"
curl -sS -X POST "$RPC_URL" -H 'Content-Type: application/json' --data-raw "$status_payload" | python3 -m json.tool

echo "# CONFIGURE: da.configure"
curl -sS -X POST "$RPC_URL" -H 'Content-Type: application/json' --data-raw "$configure_payload" | python3 -m json.tool

echo "# AFTER: da.getStatus"
curl -sS -X POST "$RPC_URL" -H 'Content-Type: application/json' --data-raw "$status_payload" | python3 -m json.tool
