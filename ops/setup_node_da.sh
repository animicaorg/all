#!/usr/bin/env bash
# ops/setup_node_da.sh – Enable DA + ingestLocal support on an Animica node
#
# Usage:
#   ./ops/setup_node_da.sh [--rpc-url http://127.0.0.1:8545] [--chain-id 1337]
#
# What it does:
#   1. Probes the node RPC endpoint.
#   2. Calls da.configure to enable DA with a sensible local directory.
#   3. Verifies da.getStatus reports enabled + writable.
#   4. Checks whether da.getIngestDir and da.ingestLocal are available
#      (needed when allow_remote_put=false).
#   5. Prints the RPC URL to copy into Animica Studio.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
RPC_URL="http://127.0.0.1:8545"
CHAIN_ID="1337"
DATA_DIR="${ANIMICA_DATA_DIR:-$HOME/.animica}"

for i in "$@"; do
  case "$i" in
    --rpc-url=*) RPC_URL="${i#*=}" ;;
    --rpc-url)   shift; RPC_URL="$1" ;;
    --chain-id=*) CHAIN_ID="${i#*=}" ;;
    --chain-id)  shift; CHAIN_ID="$1" ;;
    --data-dir=*) DATA_DIR="${i#*=}" ;;
    --data-dir)  shift; DATA_DIR="$1" ;;
  esac
done

log()  { printf '\033[1;34m[setup_node_da]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

DA_DIR="$DATA_DIR/chain-$CHAIN_ID/da"
INGEST_PENDING="$DATA_DIR/chain-$CHAIN_ID/da_ingest/pending"
INGEST_INGESTED="$DATA_DIR/chain-$CHAIN_ID/da_ingest/ingested"
INGEST_FAILED="$DATA_DIR/chain-$CHAIN_ID/da_ingest/failed"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
rpc_call() {
  local method="$1"
  local params="${2:-[]}"
  curl -sf -X POST "$RPC_URL" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$method\",\"params\":$params}" \
    2>/dev/null || echo '{"error":{"message":"curl failed"}}'
}

json_get() {
  # Very minimal jq-free JSON extractor for scalar string/bool/number values.
  local key="$1"
  local json="$2"
  echo "$json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
v = d.get('result', d)
keys = '$key'.split('.')
for k in keys:
    if isinstance(v, dict):
        v = v.get(k)
    else:
        v = None
        break
print(v if v is not None else '')
" 2>/dev/null || echo ""
}

# ---------------------------------------------------------------------------
# 1. Check node reachability
# ---------------------------------------------------------------------------
log "Probing node at $RPC_URL …"

HEAD_RESP=$(rpc_call "chain_getHead" "[]" 2>/dev/null || rpc_call "chain.getHead" "[]" 2>/dev/null || echo '{}')
if echo "$HEAD_RESP" | grep -q '"error"'; then
  err "Could not reach node at $RPC_URL"
  err "Make sure the node is running and RPC is enabled."
  exit 1
fi

ok "Node reachable"

# ---------------------------------------------------------------------------
# 2. Create local DA directories
# ---------------------------------------------------------------------------
log "Creating DA directories …"
mkdir -p "$DA_DIR" "$INGEST_PENDING" "$INGEST_INGESTED" "$INGEST_FAILED"
ok "DA dirs: $DA_DIR"
ok "Ingest dirs: $INGEST_PENDING"

# ---------------------------------------------------------------------------
# 3. Configure DA (best-effort; node may already have DA configured)
# ---------------------------------------------------------------------------
log "Calling da.configure …"
CONFIGURE_RESP=$(rpc_call "da_configure" "{\"enabled\":true,\"dir\":\"$DA_DIR\"}" 2>/dev/null \
  || rpc_call "da.configure" "{\"enabled\":true,\"dir\":\"$DA_DIR\"}" 2>/dev/null \
  || echo '{"result":{"ok":false}}')

if echo "$CONFIGURE_RESP" | grep -q '"error"'; then
  warn "da.configure returned an error (node may already be configured)."
  warn "Response: $CONFIGURE_RESP"
fi

# ---------------------------------------------------------------------------
# 4. Verify da.getStatus
# ---------------------------------------------------------------------------
log "Checking da.getStatus …"
STATUS_RESP=$(rpc_call "da_getStatus" "[]" 2>/dev/null \
  || rpc_call "da.getStatus" "[]" 2>/dev/null \
  || echo '{"result":{}}')

ENABLED=$(json_get "enabled" <<< "$STATUS_RESP")
WRITABLE=$(json_get "writable" <<< "$STATUS_RESP")
ALLOW_REMOTE_PUT=$(json_get "allow_remote_put" <<< "$STATUS_RESP")

if [ "$ENABLED" = "True" ] || [ "$ENABLED" = "true" ] || [ "$ENABLED" = "1" ]; then
  ok "DA enabled"
else
  warn "DA does not report enabled=true.  Manual node configuration may be required."
fi

if [ "$WRITABLE" = "True" ] || [ "$WRITABLE" = "true" ] || [ "$WRITABLE" = "1" ]; then
  ok "DA writable"
else
  warn "DA not writable.  Check node disk space and permissions."
fi

if [ "$ALLOW_REMOTE_PUT" = "False" ] || [ "$ALLOW_REMOTE_PUT" = "false" ] || [ -z "$ALLOW_REMOTE_PUT" ]; then
  warn "allow_remote_put=false detected.  Ingest-local mode required."
  warn "Studio will stage blobs in:  $INGEST_PENDING"
  warn "Then call da.ingestLocal with the node path."
fi

# ---------------------------------------------------------------------------
# 5. Check ingest RPC methods
# ---------------------------------------------------------------------------
log "Checking da.getIngestDir availability …"
INGEST_DIR_RESP=$(rpc_call "da_getIngestDir" "[]" 2>/dev/null \
  || rpc_call "da.getIngestDir" "[]" 2>/dev/null \
  || echo '{"error":{"message":"method not found"}}')

if echo "$INGEST_DIR_RESP" | grep -q '"error"'; then
  warn "da.getIngestDir not available on this node."
  warn "Ingest-local mode will use host-mapped path: $INGEST_PENDING"
  warn ""
  warn "To enable ingest on the node, ensure the node's ingest directory is"
  warn "mapped to: $INGEST_PENDING"
else
  NODE_INGEST_DIR=$(json_get "dir" <<< "$INGEST_DIR_RESP")
  ok "da.getIngestDir: $NODE_INGEST_DIR"
fi

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo " Animica Node DA Setup Summary"
echo "================================================================"
echo " RPC URL:        $RPC_URL"
echo " DA directory:   $DA_DIR"
echo " Ingest pending: $INGEST_PENDING"
echo " Chain ID:       $CHAIN_ID"
echo ""
echo " Copy this RPC URL into Animica Studio:"
echo "   $RPC_URL"
echo ""
echo " Then click 'FULL AUTO Setup' in Studio to continue."
echo "================================================================"
