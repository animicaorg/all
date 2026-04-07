#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
CLI=("$PYTHON_BIN" -m animica)
WORK_DIR="$(mktemp -d /tmp/animica-ena-smoke-XXXXXX)"
PORT="${ENA_SMOKE_PORT:-18080}"
ENDPOINT="http://127.0.0.1:${PORT}"
PLAN_FILE="$WORK_DIR/plan.json"
FETCH_DIR="$WORK_DIR/fetched"
MODEL_DIR="$WORK_DIR/models"

export PYTHONPATH="$ROOT/python:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export ENA_DEV_MODE=1
export ENA_DB_PATH="$WORK_DIR/ena.db"
export ENA_LOG_FILE="$WORK_DIR/logs/ena.log"
export ENA_TRAINING_DIR="$WORK_DIR/training"
export ENA_CHECKPOINTS_DIR="$WORK_DIR/checkpoints"
export ENA_MODELS_DIR="$WORK_DIR/models-registry"
export ENA_LOCAL_ENDPOINT="$ENDPOINT"
export ANIMICA_SERVICE_STATE_DIR="$WORK_DIR/service-state"

cleanup() {
  "${CLI[@]}" ena serve stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[smoke-ena] work dir: $WORK_DIR"

"${CLI[@]}" ena serve stop >/dev/null 2>&1 || true
"${CLI[@]}" ena serve start --daemon --host 127.0.0.1 --port "$PORT"
"${CLI[@]}" ena serve status >/dev/null

for _ in $(seq 1 40); do
  if curl -fsS "$ENDPOINT/v1/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

curl -fsS "$ENDPOINT/v1/health" >/dev/null
curl -fsS "$ENDPOINT/v1/models" >/dev/null

echo "[smoke-ena] local inference"
"${CLI[@]}" ena infer --local --json "smoke test prompt" >/dev/null

cat >"$PLAN_FILE" <<'JSON'
{
  "model": "ena.latest",
  "job_type": "ena.train.sft",
  "hyperparams": {
    "epochs": 3,
    "batch_size": 1
  }
}
JSON

echo "[smoke-ena] submit training job"
SUBMIT_JSON="$("${CLI[@]}" ena train submit --plan "$PLAN_FILE" --budget 0 --payer local-dev --endpoint "$ENDPOINT" --json)"
JOB_ID="$(printf '%s\n' "$SUBMIT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')"

for _ in $(seq 1 40); do
  STATUS_JSON="$("${CLI[@]}" ena train watch "$JOB_ID" --endpoint "$ENDPOINT" --json)"
  STATUS="$(printf '%s\n' "$STATUS_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  if [ "$STATUS" = "completed" ]; then
    break
  fi
  if [ "$STATUS" = "failed" ]; then
    echo "[smoke-ena][ERROR] training job failed" >&2
    exit 1
  fi
  sleep 0.5
done

[ "$STATUS" = "completed" ]

echo "[smoke-ena] list and fetch checkpoint"
CHECKPOINTS_JSON="$("${CLI[@]}" ena checkpoints list --endpoint "$ENDPOINT" --json)"
VERSION="$(printf '%s\n' "$CHECKPOINTS_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["checkpoints"][0]["version"])')"
mkdir -p "$FETCH_DIR"
"${CLI[@]}" ena checkpoints fetch "$VERSION" --endpoint "$ENDPOINT" --output "$FETCH_DIR" >/dev/null
[ -f "$FETCH_DIR/$VERSION.ckpt" ]

echo "[smoke-ena] pull and export model"
mkdir -p "$MODEL_DIR"
"${CLI[@]}" ena models pull ena.latest --endpoint "$ENDPOINT" --output "$MODEL_DIR" >/dev/null
"${CLI[@]}" ena models export ena.latest --endpoint "$ENDPOINT" --output "$MODEL_DIR/ena.latest.onnx" >/dev/null
[ -s "$MODEL_DIR/ena.latest.onnx" ]

echo "[smoke-ena] success"
