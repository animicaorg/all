#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
CLI=("$PYTHON_BIN" -m animica)
WORK_DIR="$(mktemp -d /tmp/animica-stratum-smoke-XXXXXX)"
RPC_PORT="${STRATUM_SMOKE_RPC_PORT:-18545}"
STRATUM_PORT="${STRATUM_SMOKE_PORT:-19333}"
API_PORT="${STRATUM_SMOKE_API_PORT:-18550}"
POOL_ADDRESS="${STRATUM_SMOKE_POOL_ADDRESS:-anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpool00}"

export PYTHONPATH="$ROOT/python:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export ANIMICA_SERVICE_STATE_DIR="$WORK_DIR/service-state"

cleanup() {
  "${CLI[@]}" stratum down >/dev/null 2>&1 || true
  if [ -n "${RPC_PID:-}" ]; then
    kill "$RPC_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[smoke-stratum] work dir: $WORK_DIR"

"$PYTHON_BIN" -u - <<'PY' "$RPC_PORT" "$WORK_DIR/rpc.log" &
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[1])
log_path = sys.argv[2]

class Handler(BaseHTTPRequestHandler):
    counter = 0

    def log_message(self, fmt, *args):
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write((fmt % args) + "\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = payload.get("method")
        params = payload.get("params") or []
        Handler.counter += 1

        if method == "miner.get_sha256_job":
            result = {
                "jobId": f"job-{Handler.counter}",
                "prevhash": "00" * 32,
                "coinb1": "01000000",
                "coinb2": "abcd",
                "merkle_branch": [],
                "version": "20000000",
                "nbits": "1d00ffff",
                "ntime": f"{int(time.time()):08x}",
                "difficulty": 1e-12,
                "height": Handler.counter,
            }
        elif method == "miner.submit_sha256_block":
            result = {"accepted": True, "payload": params}
        else:
            result = {"echo": {"method": method, "params": params}}

        body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
server.serve_forever()
PY
RPC_PID=$!

"${CLI[@]}" stratum down >/dev/null 2>&1 || true
"${CLI[@]}" stratum up --daemon \
  --profile asic_sha256 \
  --rpc-url "http://127.0.0.1:${RPC_PORT}" \
  --host 127.0.0.1 \
  --port "$STRATUM_PORT" \
  --api-host 127.0.0.1 \
  --api-port "$API_PORT" \
  --pool-address "$POOL_ADDRESS" \
  --min-difficulty 0.000000000001

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

curl -fsS "http://127.0.0.1:${API_PORT}/healthz" >/dev/null
"${CLI[@]}" stratum status >/dev/null

echo "[smoke-stratum] validating subscribe/authorize handshake"
"$PYTHON_BIN" - <<'PY' "$STRATUM_PORT"
import json
import socket
import sys

port = int(sys.argv[1])
sock = socket.create_connection(("127.0.0.1", port), timeout=5)
file = sock.makefile("rwb", buffering=0)

def send(obj):
    file.write((json.dumps(obj) + "\n").encode("utf-8"))

def recv():
    line = file.readline()
    if not line:
        raise SystemExit("connection closed")
    return json.loads(line.decode("utf-8"))

send({"id": 1, "method": "mining.subscribe", "params": ["smoke-harness"]})
sub = recv()
assert sub["result"][0][0][0] == "mining.set_difficulty"
set_diff = recv()
notify = recv()
assert set_diff["method"] == "mining.set_difficulty"
assert notify["method"] == "mining.notify"

send({"id": 2, "method": "mining.authorize", "params": ["worker.smoke", "x"]})
auth = recv()
assert auth["result"] is True

sock.close()
print("stratum-handshake-ok")
PY

curl -fsS "http://127.0.0.1:${API_PORT}/summary" >/dev/null

echo "[smoke-stratum] success"
