#!/usr/bin/env bash
# Verify macOS GUI artifacts and bundled node.

set -euo pipefail

log()  { printf "\033[1;34m[verify]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This script must be run on macOS"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR="$REPO_ROOT/apps/miner-gui"
DIST_DIR="$APP_DIR/dist"
NODE_PAYLOAD="$REPO_ROOT/dist/animica-node/animica-node"

[[ -x "$NODE_PAYLOAD" ]] || die "Node payload missing: $NODE_PAYLOAD"

log "Node preflight: --preflight-imports"
"$NODE_PAYLOAD" --preflight-imports

log "Node runtime sanity check"
TMP_DIR="$(mktemp -d)"
TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
)"
PORT=0
python3 - <<'PY' >"$TMP_DIR/port.txt"
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
PORT="$(cat "$TMP_DIR/port.txt")"
export ANIMICA_RPC_PORT="$PORT"
export ANIMICA_RPC_ADMIN_TOKEN="$TOKEN"
export ANIMICA_DATA_DIR="$TMP_DIR/data"
export ANIMICA_LOGS_DIR="$TMP_DIR/logs"
mkdir -p "$ANIMICA_DATA_DIR" "$ANIMICA_LOGS_DIR"
"$NODE_PAYLOAD" >/dev/null 2>&1 &
NODE_PID=$!

python3 - <<PY
import time, json, sys
import httpx

rpc_url = f"http://127.0.0.1:{PORT}/rpc"
headers = {"Authorization": f"Bearer {TOKEN}", "X-Animica-Admin-Token": TOKEN}
deadline = time.time() + 30
methods = None
while time.time() < deadline:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "rpc.methods", "params": []}
        resp = httpx.post(rpc_url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        methods = data.get("result")
        if methods:
            break
    except Exception:
        time.sleep(1)

if not methods:
    print("rpc.methods not available", file=sys.stderr)
    sys.exit(1)

required = {"chain.getHead", "sync.getStatus", "sync.dump", "p2p.listPeers", "p2p.getPeers", "net.peerCount"}
if not any(m in methods for m in ["chain.getHead", "chain_getHead"]):
    print("Missing chain.getHead", file=sys.stderr)
    sys.exit(1)
if not any(m in methods for m in ["sync.getStatus", "sync.dump", "chain_getSyncStatus"]):
    print("Missing sync status method", file=sys.stderr)
    sys.exit(1)
if not any(m in methods for m in ["p2p.listPeers", "p2p.getPeers", "net.peerCount"]):
    print("Missing peer methods", file=sys.stderr)
    sys.exit(1)
PY

kill "$NODE_PID"
wait "$NODE_PID" || true
rm -rf "$TMP_DIR"

APP_BUNDLE="$DIST_DIR/Animica Miner GUI.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  FOUND="$(find "$DIST_DIR" -maxdepth 3 -name "Animica Miner GUI.app" -type d -print -quit || true)"
  [[ -n "$FOUND" ]] && APP_BUNDLE="$FOUND"
fi
[[ -d "$APP_BUNDLE" ]] || die "App bundle missing"

log "App bundle sanity checks"
NODE_IN_APP="$APP_BUNDLE/Contents/Resources/node/animica-node/animica-node"
[[ -x "$NODE_IN_APP" ]] || die "Node missing in app bundle: $NODE_IN_APP"

QCOCOA="$(find "$APP_BUNDLE" -path "*platforms/libqcocoa.dylib" -print -quit)"
[[ -n "$QCOCOA" ]] || die "qcocoa platform plugin missing in app bundle"

DMG_PATH="$(ls -1 "$DIST_DIR"/*.dmg 2>/dev/null | head -n 1 || true)"
[[ -n "$DMG_PATH" ]] || die "DMG not found in $DIST_DIR"

log "DMG sanity check"
MOUNT_POINT="$(mktemp -d)"
hdiutil attach "$DMG_PATH" -nobrowse -mountpoint "$MOUNT_POINT" >/dev/null
[[ -d "$MOUNT_POINT/Animica Miner GUI.app" ]] || die "DMG missing app bundle"
[[ -x "$MOUNT_POINT/Animica Miner GUI.app/Contents/Resources/node/animica-node/animica-node" ]] || die "DMG missing node payload"
hdiutil detach "$MOUNT_POINT" >/dev/null
rmdir "$MOUNT_POINT"

log "All artifact checks passed."
