#!/usr/bin/env bash
# Animica Pool - worker installer.
#   curl -fsSL https://pool.animica.org/install-worker.sh | bash -s -- anm_worker_xxx
# Create a worker token at https://pool.animica.org/workers
#
# SECURITY: this MVP only runs Animica's controlled model-serving container -
# it does NOT execute arbitrary customer code on your machine.
#
# NOTE: kept strictly ASCII. macOS ships bash 3.2 and is often run under a
# non-UTF-8 locale (e.g. inside conda/venv), where multibyte characters in the
# script can corrupt parsing.
set -euo pipefail

API_BASE="${ANIMICA_API_BASE:-https://pool.animica.org}"
# Token may come from $WORKER_TOKEN, the first positional arg
# (`bash -s -- TOKEN`, the recommended piped form), or the interactive prompt.
# `${1:-}` keeps this safe under `set -u` when no arg is given.
WORKER_TOKEN="${WORKER_TOKEN:-${1:-}}"

if [ -z "$WORKER_TOKEN" ]; then
  # Prompt on the terminal, NOT stdin: when run as `curl ... | bash`, stdin is
  # the pipe carrying this script, so reading it would consume the script.
  # Open /dev/tty on fd 3 only if available; the grouped redirect suppresses the
  # error and avoids aborting under `set -e` when there is no controlling tty.
  if { exec 3</dev/tty; } 2>/dev/null; then
    printf 'Paste your worker token (anm_worker_...): '
    read -r WORKER_TOKEN <&3 || true
    exec 3<&-
  fi
fi
if [ -z "$WORKER_TOKEN" ]; then
  echo "worker token required." >&2
  echo "Re-run with the token passed to bash (the token must be on the bash" >&2
  echo "side of the pipe, not curl's):" >&2
  echo "  curl -fsSL $API_BASE/install-worker.sh | bash -s -- anm_worker_xxx" >&2
  exit 1
fi

# DOCKER_OK=1 once we have a CLI AND a reachable daemon. Otherwise we fall back
# to the standalone Node agent (no Docker required).
DOCKER_OK=0
echo "==> Checking Docker..."
if ! command -v docker >/dev/null 2>&1; then
  case "$(uname -s)" in
    Linux)
      echo "    installing Docker..."
      curl -fsSL https://get.docker.com | sh && DOCKER_OK=1
      ;;
    *)
      echo "    Docker not found - will use the standalone Node agent instead."
      ;;
  esac
elif docker info >/dev/null 2>&1; then
  DOCKER_OK=1
else
  echo "    Docker is installed but the daemon isn't running."
  echo "    (Start Docker Desktop, or 'sudo systemctl start docker' on Linux, then"
  echo "     re-run for the container path.) Falling back to the Node agent for now."
fi

echo "==> Collecting hardware info..."
OS="$(uname -sr 2>/dev/null || echo unknown)"
CPU="unknown"
RAM_GB=0
GPU=""
VRAM_GB=0
case "$(uname -s)" in
  Darwin)
    CPU="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
    MEM_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    RAM_GB=$(( MEM_BYTES / 1024 / 1024 / 1024 ))
    # Apple Silicon: the chip IS the GPU (Metal, unified memory). Counts for
    # inference/rentals; Bittensor SN51 stays NVIDIA-only and gates server-side.
    case "$CPU" in
      Apple*)
        GPU="$CPU (Metal, unified memory)"
        VRAM_GB=$RAM_GB
        ;;
    esac
    ;;
  *)
    CPU="$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ //')"
    [ -z "$CPU" ] && CPU="unknown"
    MEM_KB="$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')"
    [ -z "$MEM_KB" ] && MEM_KB=0
    RAM_GB=$(( MEM_KB / 1024 / 1024 ))
    if command -v nvidia-smi >/dev/null 2>&1; then
      GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "")"
      VRAM_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)"
      case "$VRAM_MIB" in (*[!0-9]*|'') VRAM_MIB=0;; esac
      VRAM_GB=$(( VRAM_MIB / 1024 ))
    fi
    ;;
esac

echo "==> Registering with $API_BASE ..."
curl -fsS -X POST "$API_BASE/api/workers/register" \
  -H "authorization: Bearer $WORKER_TOKEN" -H "content-type: application/json" \
  -d "$(printf '{"cpuModel":"%s","gpuModel":"%s","vramGb":%s,"ramGb":%s,"os":"%s"}' "$CPU" "$GPU" "$VRAM_GB" "$RAM_GB" "$OS")" >/dev/null
echo "    registered."

WORKER_IMAGE="${WORKER_IMAGE:-animicaorg/worker:latest}"
LOCAL_INFERENCE_URL="${LOCAL_INFERENCE_URL:-}"

if [ "$DOCKER_OK" = "1" ] && docker pull "$WORKER_IMAGE" >/dev/null 2>&1; then
  echo "==> Starting worker container..."
  docker rm -f animica-worker >/dev/null 2>&1 || true
  docker run -d --name animica-worker --restart unless-stopped \
    -e WORKER_TOKEN="$WORKER_TOKEN" \
    -e ANIMICA_API_BASE="$API_BASE" \
    -e LOCAL_INFERENCE_URL="$LOCAL_INFERENCE_URL" \
    "$WORKER_IMAGE"
  echo "OK: worker container running. Manage it at $API_BASE/workers"
  exit 0
fi

# Standalone Node agent fallback (no Docker). Needs Node 18+ (global fetch).
echo "==> Using the standalone Node agent (no Docker)."
AGENT="$HOME/animica-worker-agent.mjs"
curl -fsSL "$API_BASE/worker-agent.mjs" -o "$AGENT"
echo "    downloaded agent to $AGENT"

if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || echo 0)"
  if [ "$NODE_MAJOR" -ge 18 ] 2>/dev/null; then
    # Default to Ollama's local OpenAI-compatible endpoint if the user didn't set one.
    RUN_URL="${LOCAL_INFERENCE_URL:-http://localhost:11434/v1}"
    LOG="$HOME/animica-worker.log"
    nohup env WORKER_TOKEN="$WORKER_TOKEN" ANIMICA_API_BASE="$API_BASE" \
      LOCAL_INFERENCE_URL="$RUN_URL" LOCAL_INFERENCE_MODEL="${LOCAL_INFERENCE_MODEL:-qwen2.5:7b}" \
      node "$AGENT" </dev/null >"$LOG" 2>&1 &
    PID=$!
    sleep 1
    echo "OK: worker agent started in the background (PID $PID)."
    echo "    Logs:  tail -f $LOG"
    echo "    Stop:  kill $PID"
    echo "    To serve inference jobs, run a local model server (Ollama):"
    echo "      brew install ollama; ollama serve &  ollama pull qwen2.5:7b"
    echo "    The agent uses LOCAL_INFERENCE_URL=$RUN_URL"
    echo "    Note: this background agent does not auto-restart on reboot; re-run to restart."
    exit 0
  fi
fi

echo "WARN: Node 18+ not found. Install Node (https://nodejs.org) or start Docker, then run:" >&2
echo "  WORKER_TOKEN=$WORKER_TOKEN ANIMICA_API_BASE=$API_BASE \\" >&2
echo "    LOCAL_INFERENCE_URL=http://localhost:11434/v1 node $AGENT" >&2
exit 1
