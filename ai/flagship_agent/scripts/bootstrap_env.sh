#!/usr/bin/env bash
# Set up (or sanity-check) the environment for the flagship-agent pipeline.
#
# Strategy:
#   1. Detect accelerator (cuda / mps / cpu).
#   2. If a venv is present (REPO/.venv, PKG/.venv, or $VIRTUAL_ENV), assume it's
#      already populated and validate the minimum import set.
#   3. Otherwise create PKG/.venv and pip-install requirements.txt + the
#      accelerator-specific PyTorch wheel.
#   4. Emit runs/_pipeline/bootstrap.json with what we picked.
#
# Idempotent: rerunning is cheap when nothing changed.
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="${ANIMICA_REPO_ROOT:-$(cd "$PKG_DIR/../.." && pwd)}"
mkdir -p "$PKG_DIR/runs/_pipeline"
OUT_JSON="$PKG_DIR/runs/_pipeline/bootstrap.json"

detect_accel() {
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo cuda; return
  fi
  if [ "$(uname -s)" = "Darwin" ] && uname -m | grep -qi 'arm\|aarch64'; then
    echo mps; return
  fi
  echo cpu
}

pick_venv() {
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then
    echo "$VIRTUAL_ENV"; return
  fi
  if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    echo "$REPO_ROOT/.venv"; return
  fi
  if [ -x "$PKG_DIR/.venv/bin/python3" ]; then
    echo "$PKG_DIR/.venv"; return
  fi
  echo ""
}

create_venv() {
  local target="$PKG_DIR/.venv"
  python3 -m venv "$target"
  echo "$target"
}

validate_imports() {
  local venv="$1"
  "$venv/bin/python3" - <<'PYEOF'
import sys
required = ["yaml", "pydantic", "httpx"]
optional_inference = ["torch", "transformers"]
missing = []
for m in required:
    try:
        __import__(m)
    except ImportError:
        missing.append(m)
print(f"missing_required={missing}")
opt = []
for m in optional_inference:
    try:
        __import__(m)
    except ImportError:
        opt.append(m)
print(f"missing_optional_inference={opt}")
sys.exit(1 if missing else 0)
PYEOF
}

install_minimum() {
  local venv="$1"
  "$venv/bin/python3" -m pip install --upgrade pip >/dev/null
  "$venv/bin/python3" -m pip install -r "$PKG_DIR/requirements.txt"
  # Install agent_runtime in editable mode (source-tree resolves both packages).
  if [ -d "$REPO_ROOT/ai/agent_runtime" ]; then
    "$venv/bin/python3" -m pip install -e "$REPO_ROOT/ai/agent_runtime"
  fi
}

install_accel() {
  local venv="$1" accel="$2"
  case "$accel" in
    cuda)
      "$venv/bin/python3" -m pip install --index-url \
        https://download.pytorch.org/whl/cu121 torch
      ;;
    mps)
      "$venv/bin/python3" -m pip install torch
      ;;
    cpu)
      "$venv/bin/python3" -m pip install --index-url \
        https://download.pytorch.org/whl/cpu torch
      ;;
  esac
}

main() {
  local accel
  accel="$(detect_accel)"
  local venv
  venv="$(pick_venv)"
  if [ -z "$venv" ]; then
    venv="$(create_venv)"
    install_minimum "$venv"
    install_accel "$venv" "$accel"
  else
    # Validate imports; reinstall minimum if anything required is missing.
    if ! validate_imports "$venv" >/dev/null 2>&1; then
      install_minimum "$venv"
    fi
  fi

  cat > "$OUT_JSON" <<EOF
{
  "schema": 1,
  "venv": "$venv",
  "accelerator": "$accel",
  "repo_root": "$REPO_ROOT",
  "ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  echo "[bootstrap_env] venv=$venv accel=$accel"
}

main "$@"
