#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$SCRIPT_DIR}"
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

  [[ -f "$pid_file" ]] || return 0

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"

  if [[ -n "${pid:-}" ]]; then
    kill_pid "$pid" "$name"
  fi

  rm -f "$pid_file"
}

stop_managed_postgres() {
  local managed_file="$PID_DIR/postgres.managed"
  [[ -f "$managed_file" ]] || return 0

  local mode
  mode="$(cat "$managed_file" 2>/dev/null || true)"

  case "$mode" in
    pg_ctl:*)
      local pgdata
      pgdata="${mode#pg_ctl:}"
      if command -v pg_ctl >/dev/null 2>&1; then
        info "Stopping PostgreSQL (pg_ctl)"
        pg_ctl -D "$pgdata" stop >/dev/null 2>&1 || true
      fi
      ;;
    pg_ctlcluster:*)
      local rest ver name
      rest="${mode#pg_ctlcluster:}"
      ver="${rest%%:*}"
      name="${rest#*:}"
      if command -v pg_ctlcluster >/dev/null 2>&1; then
        info "Stopping PostgreSQL (pg_ctlcluster $ver/$name)"
        pg_ctlcluster --skip-systemctl-redirect "$ver" "$name" stop >/dev/null 2>&1 || true
      fi
      ;;
    service:postgresql)
      if command -v service >/dev/null 2>&1; then
        info "Stopping PostgreSQL (service)"
        service postgresql stop >/dev/null 2>&1 || true
      fi
      ;;
    systemctl:postgresql)
      if command -v systemctl >/dev/null 2>&1; then
        info "Stopping PostgreSQL (systemctl)"
        systemctl stop postgresql >/dev/null 2>&1 || true
      fi
      ;;
  esac

  rm -f "$managed_file"
}

stop_managed_redis() {
  if [[ -f "$PID_DIR/redis.pid" ]] && command -v redis-cli >/dev/null 2>&1; then
    info "Stopping Redis"
    redis-cli -h 127.0.0.1 -p 6379 shutdown nosave >/dev/null 2>&1 || true
  fi
}

info "Root: $ROOT"
info "PIDs: $PID_DIR"

for svc in \
  exchange-web api-gateway auth-service matching-engine ledger-service \
  wallet-router bitgo-webhook animica-indexer risk-service \
  withdrawals-service animica-asset admin-service

do
  stop_pid_file "$svc"
done

stop_managed_redis
stop_pid_file "redis"
stop_pid_file "nats"
stop_managed_postgres

pkill -f "tsx watch" >/dev/null 2>&1 || true
pkill -f "vite" >/dev/null 2>&1 || true

ok "Exchange stop sequence complete"
