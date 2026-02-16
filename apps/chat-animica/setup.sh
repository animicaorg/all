#!/usr/bin/env bash
# chat-animica setup: installs/runs Ollama with Qwen2.5-Coder locally on Ubuntu/Debian (apt) and macOS (brew).
# Idempotent, non-interactive, production-friendly bootstrap with health checks and test inference.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env.local"
MODEL="${QWEN_MODEL:-qwen2.5-coder:7b}"
OLLAMA_URL="http://127.0.0.1:11434"
OLLAMA_TAGS_URL="$OLLAMA_URL/api/tags"
OLLAMA_GEN_URL="$OLLAMA_URL/api/generate"
OLLAMA_LOG_FILE="/tmp/ollama-serve.log"
OLLAMA_PID_FILE="/tmp/ollama-serve.pid"
SETUP_LOG_FILE="/tmp/chat-animica-setup.log"

log() {
  printf '[INFO] %s\n' "$*" | tee -a "$SETUP_LOG_FILE"
}

warn() {
  printf '[WARN] %s\n' "$*" | tee -a "$SETUP_LOG_FILE" >&2
}

die() {
  printf '[ERROR] %s\n' "$*" | tee -a "$SETUP_LOG_FILE" >&2
  print_ollama_diagnostics || true
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

is_root() {
  [ "$(id -u)" -eq 0 ]
}

run_as_root() {
  if is_root; then
    "$@"
    return
  fi

  if have sudo; then
    sudo "$@"
    return
  fi

  die "This step requires elevated privileges, but sudo is not available. Install sudo or run this script as root."
}

print_ollama_diagnostics() {
  warn "Collecting Ollama diagnostics..."

  if have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
    warn "Last 40 lines of journald for ollama.service:"
    if is_root; then
      journalctl -u ollama --no-pager -n 40 2>/dev/null || true
    elif have sudo; then
      sudo journalctl -u ollama --no-pager -n 40 2>/dev/null || true
    else
      warn "Cannot access journald for ollama.service without sudo/root."
    fi
  fi

  if [ -f "$OLLAMA_LOG_FILE" ]; then
    warn "Last 40 lines of $OLLAMA_LOG_FILE:"
    tail -n 40 "$OLLAMA_LOG_FILE" || true
  fi
}

detect_os() {
  OS_TYPE="$(uname -s)"
  case "$OS_TYPE" in
    Linux)
      if have apt-get; then
        PKG_MANAGER="apt"
      else
        die "Unsupported Linux distribution: apt-get not found. Install curl + ollama manually: https://ollama.com/download"
      fi
      ;;
    Darwin)
      PKG_MANAGER="brew"
      ;;
    *)
      die "Unsupported OS: $OS_TYPE. Supported: Ubuntu/Debian Linux and macOS."
      ;;
  esac
  log "Detected OS: $OS_TYPE (package manager: $PKG_MANAGER)"
}

install_curl_if_missing() {
  if have curl; then
    log "curl already installed."
    return
  fi

  log "curl not found; installing..."
  case "$PKG_MANAGER" in
    apt)
      run_as_root env DEBIAN_FRONTEND=noninteractive apt-get update -y
      run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y curl
      ;;
    brew)
      if ! have brew; then
        die "Homebrew is required on macOS to install curl automatically. Install brew from https://brew.sh or install curl manually."
      fi
      brew install curl
      ;;
    *)
      die "Unsupported package manager for curl install: $PKG_MANAGER"
      ;;
  esac

  have curl || die "curl installation reported success, but curl is still unavailable."
  log "curl installed successfully."
}

install_ollama_if_missing() {
  if have ollama; then
    log "Ollama already installed."
    return
  fi

  log "Ollama not found; installing..."
  case "$OS_TYPE" in
    Linux)
      # Official Linux install method.
      curl -fsSL https://ollama.com/install.sh | sh
      ;;
    Darwin)
      if have brew; then
        brew install ollama
      else
        die "Homebrew not found. Install brew or install Ollama manually: https://ollama.com/download"
      fi
      ;;
    *)
      die "Unsupported OS for Ollama install: $OS_TYPE"
      ;;
  esac

  have ollama || die "Ollama installation failed: 'ollama' binary not found on PATH after install."
  log "Ollama installed successfully."
}

wait_for_ollama_api() {
  local attempts="${1:-30}"
  local sleep_secs="${2:-2}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$OLLAMA_TAGS_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_secs"
  done

  return 1
}

start_ollama_if_needed() {
  if curl -fsS "$OLLAMA_TAGS_URL" >/dev/null 2>&1; then
    log "Ollama API already reachable at $OLLAMA_URL; not restarting."
    return
  fi

  log "Ollama API not reachable; attempting to start service..."

  if [ "$OS_TYPE" = "Linux" ] && have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
    log "Detected systemd ollama.service; enabling + starting."
    run_as_root systemctl enable --now ollama || warn "systemctl enable --now ollama failed; will try background serve fallback."
    if wait_for_ollama_api 20 2; then
      log "Ollama API is up via systemd service."
      return
    fi
    warn "systemd startup did not make API reachable in time; using fallback."
  fi

  if [ -f "$OLLAMA_PID_FILE" ]; then
    local old_pid
    old_pid="$(cat "$OLLAMA_PID_FILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
      warn "Found existing ollama serve PID $old_pid from pidfile, waiting for API."
      if wait_for_ollama_api 10 2; then
        log "Ollama API became reachable."
        return
      fi
      warn "Existing background process did not make API reachable; starting a new one."
    fi
  fi

  log "Starting 'ollama serve' in background (nohup). Logs: $OLLAMA_LOG_FILE"
  nohup ollama serve >"$OLLAMA_LOG_FILE" 2>&1 &
  local new_pid=$!
  echo "$new_pid" > "$OLLAMA_PID_FILE"
  log "Started ollama serve with PID $new_pid"

  wait_for_ollama_api 30 2 || die "Ollama API failed to become ready on $OLLAMA_URL after startup."
  log "Ollama API is reachable."
}

ensure_model_pulled() {
  log "Pulling model: $MODEL"
  ollama pull "$MODEL" || die "Failed to pull model '$MODEL'."

  local tags_json
  tags_json="$(curl -fsS "$OLLAMA_TAGS_URL")" || die "Failed to query Ollama tags endpoint after model pull."

  if printf '%s' "$tags_json" | grep -Fq "\"name\":\"$MODEL\"" || printf '%s' "$tags_json" | grep -Fq "\"model\":\"$MODEL\""; then
    log "Model '$MODEL' is present in Ollama tags."
  else
    die "Model '$MODEL' not found in Ollama tags after pull."
  fi
}

test_inference() {
  log "Running test inference against model '$MODEL'..."

  local payload
  payload=$(cat <<JSON
{"model":"$MODEL","prompt":"Return exactly: OK","stream":false}
JSON
)

  local response
  response="$(curl -fsS "$OLLAMA_GEN_URL" -H 'Content-Type: application/json' -d "$payload")" || die "Test inference request failed."

  if printf '%s' "$response" | grep -Eq '"response"\s*:\s*"[^"]*OK[^"]*"|\bOK\b'; then
    log "Inference test succeeded."
  else
    warn "Inference response: $response"
    die "Inference test failed: expected output to contain 'OK'."
  fi
}

ensure_env_file() {
  if [ -f "$ENV_FILE" ]; then
    log "$ENV_FILE already exists; not overwriting."
    printf '%s\n' "Expected keys (ensure these are set appropriately):"
    printf '%s\n' "  LLM_PROVIDER=ollama"
    printf '%s\n' "  LLM_BASE_URL=$OLLAMA_URL"
    printf '%s\n' "  LLM_MODEL=$MODEL"
    return
  fi

  log "Creating $ENV_FILE"
  cat > "$ENV_FILE" <<ENV
LLM_PROVIDER=ollama
LLM_BASE_URL=http://127.0.0.1:11434
LLM_MODEL=qwen2.5-coder:7b
ENV
  log "Created $ENV_FILE"
}

print_next_steps() {
  echo
  log "Setup complete. Next steps:"
  echo "  cd apps/chat-animica"
  if have pnpm; then
    echo "  pnpm install"
    echo "  pnpm dev"
  else
    echo "  pnpm is not installed. Install it first: npm install -g pnpm"
    echo "  Then run:"
    echo "    pnpm install"
    echo "    pnpm dev"
  fi
  echo
  echo "The app should read: LLM_PROVIDER, LLM_BASE_URL, and LLM_MODEL"
}

main() {
  : > "$SETUP_LOG_FILE"
  log "Starting chat-animica Ollama setup..."
  log "Using model: $MODEL"

  detect_os
  install_curl_if_missing
  install_ollama_if_missing
  start_ollama_if_needed

  curl -fsS "$OLLAMA_TAGS_URL" >/dev/null || die "Health check failed: $OLLAMA_TAGS_URL is unreachable."
  log "Health check passed: Ollama API reachable on port 11434."

  ensure_model_pulled
  test_inference
  ensure_env_file
  print_next_steps
}

main "$@"
