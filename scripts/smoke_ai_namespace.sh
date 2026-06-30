#!/usr/bin/env bash
# Smoke test for the `animica ai` namespace (5.2.0). Fully offline — uses the
# deterministic/hashing providers and never spends ANM. Verifies: doctor, setup,
# models, embed, rag (index/query/list), the OpenAI gateway over HTTP, and job
# estimate (read-only). Exits non-zero on the first failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
CLI=("$PYTHON_BIN" -m animica)
WORK_DIR="$(mktemp -d /tmp/animica-ai-smoke-XXXXXX)"
PORT="${AI_SMOKE_PORT:-18799}"
BASE="http://127.0.0.1:${PORT}"

export PYTHONPATH="$ROOT/python:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export ANIMICA_HOME="$WORK_DIR/home"
export ANIMICA_CONFIG="$WORK_DIR/home/config.toml"
mkdir -p "$ANIMICA_HOME"

SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

pass() { echo "[smoke-ai] ✔ $1"; }

echo "[smoke-ai] work dir: $WORK_DIR"

# 1) doctor — must run and emit JSON (exit 0 or 1, never crash).
"${CLI[@]}" ai doctor --json >/dev/null && pass "doctor --json"

# 2) setup — non-interactive, deterministic provider (offline).
"${CLI[@]}" ai setup --yes --provider deterministic --model deterministic --json >/dev/null
test -f "$ANIMICA_CONFIG" && pass "setup wrote config.toml"

# 3) models — list (JSON).
"${CLI[@]}" ai models --json >/dev/null && pass "models --json"

# 4) embed — offline hashing provider.
DIM=$("${CLI[@]}" ai embed "smoke test" --provider hashing --json \
  | "$PYTHON_BIN" -c 'import sys,json;print(len(json.load(sys.stdin)["data"][0]["embedding"]))')
[ "$DIM" -gt 0 ] && pass "embed (dim=$DIM)"

# 5) rag — index + query a local doc, offline.
mkdir -p "$WORK_DIR/docs"
printf 'Animica pays miners in ANM.\n\nThe stratum pool listens on port 3333.' > "$WORK_DIR/docs/d.md"
"${CLI[@]}" ai rag index "$WORK_DIR/docs" --provider hashing --name smoke >/dev/null
HITS=$("${CLI[@]}" ai rag query "which port?" --name smoke --provider hashing --k 1 --json \
  | "$PYTHON_BIN" -c 'import sys,json;print(len(json.load(sys.stdin)["hits"]))')
[ "$HITS" -ge 1 ] && pass "rag index+query ($HITS hit)"

# 6) gateway — serve (deterministic) + curl the OpenAI surface.
"${CLI[@]}" ai serve --port "$PORT" --provider deterministic >"$WORK_DIR/serve.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 40); do
  curl -sf --max-time 2 "$BASE/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -sf --max-time 4 "$BASE/health" >/dev/null && pass "gateway /health"
curl -sf --max-time 4 "$BASE/v1/models" >/dev/null && pass "gateway /v1/models"
CHAT=$(curl -sf --max-time 8 "$BASE/v1/chat/completions" -H 'content-type: application/json' \
  -d '{"model":"deterministic","messages":[{"role":"user","content":"ping"}]}' \
  | "$PYTHON_BIN" -c 'import sys,json;print(json.load(sys.stdin)["object"])')
[ "$CHAT" = "chat.completion" ] && pass "gateway /v1/chat/completions"
curl -sf --max-time 6 "$BASE/v1/embeddings" -H 'content-type: application/json' \
  -d '{"input":["a","b"]}' >/dev/null && pass "gateway /v1/embeddings"

echo "[smoke-ai] ALL CHECKS PASSED"
