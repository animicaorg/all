#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${ROOT:-$HOME/animica/cex}"
LOG_DIR="$ROOT/.run-logs"
PID_DIR="$ROOT/.run-pids"

mkdir -p "$LOG_DIR" "$PID_DIR"
cd "$ROOT"

export CI=1
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0

NATS_URL_FIXED="nats://127.0.0.1:4222"
DB_HOST_FIXED="127.0.0.1"
DB_PORT_FIXED="5432"
DB_USER_FIXED="cex"
DB_PASSWORD_FIXED="glassrock1212"
DB_NAME_FIXED="cex_exchange"
DATABASE_URL_FIXED="postgresql://${DB_USER_FIXED}:${DB_PASSWORD_FIXED}@${DB_HOST_FIXED}:${DB_PORT_FIXED}/${DB_NAME_FIXED}"
ANIMICA_RPC_URL_FIXED="http://127.0.0.1:8545/rpc"
REDIS_URL_FIXED="redis://127.0.0.1:6379"

info() { echo "[INFO] $*"; }
ok()   { echo "[OK]   $*"; }
warn() { echo "[WARN] $*" >&2; }
err()  { echo "[ERR]  $*" >&2; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    err "Missing required command: $1"
    exit 1
  }
}

need_cmd pnpm
need_cmd curl
need_cmd ss
need_cmd docker
need_cmd pkill
need_cmd python3
need_cmd socat

probe_tcp() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket, sys
host = sys.argv[1]
port = int(sys.argv[2])
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect((host, port))
    sys.exit(0)
except Exception:
    sys.exit(1)
finally:
    try:
        s.close()
    except Exception:
        pass
PY
}

cleanup_old() {
  info "Stopping old local dev processes if present..."
  pkill -f "tsx watch" >/dev/null 2>&1 || true
  pkill -f "vite" >/dev/null 2>&1 || true
  pkill -f "socat TCP-LISTEN:6379,bind=127.0.0.1" >/dev/null 2>&1 || true
  rm -f "$PID_DIR"/*.pid >/dev/null 2>&1 || true
  sleep 2
}

ensure_nats() {
  if probe_tcp 127.0.0.1 4222; then
    ok "NATS already available on 4222"
    return 0
  fi

  info "NATS is not listening on 4222; starting docker container local-nats..."
  docker rm -f local-nats >/dev/null 2>&1 || true
  docker run -d --name local-nats -p 4222:4222 nats:2.10-alpine >/dev/null

  for _ in $(seq 1 20); do
    probe_tcp 127.0.0.1 4222 && { ok "NATS is now listening on 4222"; return 0; }
    sleep 1
  done

  err "NATS did not open 127.0.0.1:4222"
  exit 1
}

ensure_redis_local() {
  if probe_tcp 127.0.0.1 6379; then
    ok "Redis already available on 127.0.0.1:6379"
    return 0
  fi

  if docker ps -a --format '{{.Names}}' | grep -qx 'ops-redis-1'; then
    info "Starting preferred Redis container: ops-redis-1"
    docker start ops-redis-1 >/dev/null 2>&1 || true

    local redis_ip=""
    redis_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ops-redis-1 2>/dev/null || true)"

    if [[ -n "${redis_ip:-}" ]] && probe_tcp "$redis_ip" 6379; then
      info "Creating local Redis proxy 127.0.0.1:6379 -> ${redis_ip}:6379"
      pkill -f "socat TCP-LISTEN:6379,bind=127.0.0.1" >/dev/null 2>&1 || true
      nohup socat TCP-LISTEN:6379,bind=127.0.0.1,reuseaddr,fork TCP:${redis_ip}:6379 \
        >"$LOG_DIR/redis-proxy.log" 2>&1 &
      echo $! > "$PID_DIR/redis-proxy.pid"
    fi
  fi

  for _ in $(seq 1 20); do
    probe_tcp 127.0.0.1 6379 && { ok "Redis now available on 127.0.0.1:6379"; return 0; }
    sleep 1
  done

  err "Redis did not open 127.0.0.1:6379"
  docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep redis || true
  [[ -f "$LOG_DIR/redis-proxy.log" ]] && tail -n 80 "$LOG_DIR/redis-proxy.log" || true
  exit 1
}

ensure_postgres() {
  if probe_tcp "$DB_HOST_FIXED" "$DB_PORT_FIXED"; then
    ok "PostgreSQL already available on $DB_PORT_FIXED"
    return 0
  fi

  err "PostgreSQL is not listening on $DB_PORT_FIXED"
  exit 1
}

ensure_animica_rpc() {
  if curl -fsS --max-time 5 \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}' \
    "$ANIMICA_RPC_URL_FIXED" >/dev/null; then
    ok "Animica RPC is responding"
    return 0
  fi

  err "Animica RPC is not responding at $ANIMICA_RPC_URL_FIXED"
  exit 1
}

run_migrations_and_seeds() {
  info "Running migrations..."
  bash -lc "cd '$ROOT' && yes | ./ops/scripts/migrate.sh"

  info "Running seeds..."
  bash -lc "cd '$ROOT' && yes | ./ops/scripts/seed.sh"
}

port_in_use() {
  local port="$1"
  ss -ltn "( sport = :$port )" | tail -n +2 | grep -q .
}

wait_for_port() {
  local name="$1"
  local port="$2"
  local log_file="$3"

  for _ in $(seq 1 45); do
    if port_in_use "$port"; then
      ok "$name is listening on $port"
      return 0
    fi
    sleep 1
  done

  err "$name did not open port $port"
  [[ -f "$log_file" ]] && tail -n 120 "$log_file"
  exit 1
}

start_service() {
  local name="$1"
  local port="$2"
  local cmd="$3"
  local log_file="$LOG_DIR/$name.log"
  local pid_file="$PID_DIR/$name.pid"

  if port_in_use "$port"; then
    local pids=""
    pids="$(ss -ltnp | awk -v p=":$port" '$4 ~ p {print $NF}' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)"
    if [[ -n "${pids:-}" ]]; then
      warn "Port $port already in use before starting $name; checking owners..."
      for pid in $pids; do
        local cmdline=""
        cmdline="$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null || true)"
        local cwd=""
        cwd="$(pwdx "$pid" 2>/dev/null | awk '{print $2}' || true)"
        warn "Port $port owner pid=$pid cmd=[$cmdline] cwd=[$cwd]"

        if [[ "$cmdline" == *"/root/animica/cex/"* ]] || [[ "$cwd" == /root/animica/cex* ]] || [[ "$cmdline" == *"tsx watch"* ]] || [[ "$cmdline" == *"vite"* ]]; then
          warn "Killing stale CEX-owned process on port $port: pid=$pid"
          kill "$pid" 2>/dev/null || true
          sleep 2
          kill -9 "$pid" 2>/dev/null || true
        fi
      done
    fi
  fi

  if port_in_use "$port"; then
    err "Port $port is still in use before starting $name"
    ss -ltnp | grep ":$port" || true
    exit 1
  fi

  : > "$log_file"
  info "Starting $name on port $port"

  bash -lc "
    cd '$ROOT'
    export PORT='$port'
    export HOST='0.0.0.0'
    export NATS_URL='$NATS_URL_FIXED'
    export REDIS_URL='$REDIS_URL_FIXED'
    export DATABASE_URL='$DATABASE_URL_FIXED'
    export DB_HOST='$DB_HOST_FIXED'
    export DB_PORT='$DB_PORT_FIXED'
    export DB_USER='$DB_USER_FIXED'
    export DB_PASSWORD='$DB_PASSWORD_FIXED'
    export DB_NAME='$DB_NAME_FIXED'
    export PGHOST='$DB_HOST_FIXED'
    export PGPORT='$DB_PORT_FIXED'
    export PGUSER='$DB_USER_FIXED'
    export PGPASSWORD='$DB_PASSWORD_FIXED'
    export PGDATABASE='$DB_NAME_FIXED'
    export ANIMICA_RPC_URL='$ANIMICA_RPC_URL_FIXED'
    $cmd
  " >"$log_file" 2>&1 &

  local pid=$!
  echo "$pid" > "$pid_file"

  wait_for_port "$name" "$port" "$log_file"
}

main() {
  info "Root: $ROOT"
  info "Logs: $LOG_DIR"
  info "PIDs:  $PID_DIR"

  cleanup_old
  ensure_nats
  ensure_redis_local
  ensure_postgres
  ensure_animica_rpc

  info "Using REDIS_URL=$REDIS_URL_FIXED"
  info "Using DATABASE_URL=$DATABASE_URL_FIXED"

  run_migrations_and_seeds

  start_service exchange-web        5175 "pnpm --filter ./apps/exchange-web dev -- --host 0.0.0.0 --port 5175"
  start_service api-gateway         3000 "pnpm --filter ./services/api-gateway dev"
  start_service auth-service        3002 "pnpm --filter ./services/auth-service dev"
  start_service matching-engine     3003 "pnpm --filter ./services/matching-engine dev"
  start_service ledger-service      3004 "pnpm --filter ./services/ledger-service dev"
  start_service wallet-router       3005 "pnpm --filter ./services/wallet-router dev"
  start_service bitgo-webhook       3006 "pnpm --filter ./services/bitgo-webhook-ingestor dev"
  start_service animica-indexer     3007 "pnpm --filter ./services/animica-indexer dev"
  start_service risk-service        3008 "pnpm --filter ./services/risk-service dev"
  start_service withdrawals-service 3011 "pnpm --filter ./services/withdrawals-service dev"
  start_service animica-asset       3012 "pnpm --filter ./services/animica-asset-service dev"
  start_service admin-service       4000 "pnpm --filter ./services/admin-service dev"

  ok "CEX startup sequence complete"
  echo
  echo "tail -f $LOG_DIR/*.log"
}

main "$@"
