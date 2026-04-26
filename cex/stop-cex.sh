#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${ROOT:-$HOME/animica/cex}"
PID_DIR="$ROOT/.run-pids"

mkdir -p "$PID_DIR"

info() { echo "[INFO] $*"; }
ok()   { echo "[OK]   $*"; }
warn() { echo "[WARN] $*" >&2; }

kill_pid() {
  local pid="$1"
  local label="$2"

  [[ -z "${pid:-}" ]] && return 0
  kill -0 "$pid" 2>/dev/null || return 0

  info "Stopping $label (pid $pid)"
  kill "$pid" 2>/dev/null || true

  for _ in $(seq 1 10); do
    if ! kill -0 "$pid" 2>/dev/null; then
      ok "$label stopped"
      return 0
    fi
    sleep 1
  done

  warn "$label did not exit cleanly, sending SIGKILL"
  kill -9 "$pid" 2>/dev/null || true
}

stop_pid_file() {
  local name="$1"
  local pid_file="$PID_DIR/$name.pid"

  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"

  if [[ -n "${pid:-}" ]]; then
    kill_pid "$pid" "$name"
  fi

  rm -f "$pid_file"
}

info "Root: $ROOT"
info "PIDs: $PID_DIR"

for svc in \
  exchange-web api-gateway auth-service matching-engine ledger-service \
  wallet-router bitgo-webhook animica-indexer risk-service \
  withdrawals-service animica-asset admin-service redis-proxy
do
  stop_pid_file "$svc"
done

pkill -f "tsx watch" >/dev/null 2>&1 || true
pkill -f "vite" >/dev/null 2>&1 || true
pkill -f "socat TCP-LISTEN:6379,bind=127.0.0.1" >/dev/null 2>&1 || true

ok "Exchange stop sequence complete"
