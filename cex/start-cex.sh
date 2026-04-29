#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$SCRIPT_DIR}"
OPS_DIR="$ROOT/ops"
ENV_FILE="$OPS_DIR/env/.env"
LOG_DIR="$ROOT/.run-logs"
PID_DIR="$ROOT/.run-pids"

mkdir -p "$LOG_DIR" "$PID_DIR"
cd "$ROOT"

export CI=1
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
export NODE_ENV="${NODE_ENV:-development}"

# shellcheck source=/dev/null
source "$OPS_DIR/scripts/ensure-env.sh"
ensure_env_file "$OPS_DIR"
load_env_file "$OPS_DIR"
normalize_local_endpoints

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-cex}"
DB_PASSWORD="${DB_PASSWORD:-cex_password}"
DB_NAME="${DB_NAME:-cex_exchange}"
DATABASE_URL="${DATABASE_URL:-postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379}"
NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
ANIMICA_RPC_URL="${ANIMICA_RPC_URL:-http://127.0.0.1:8545/rpc}"

API_GATEWAY_PORT="${API_GATEWAY_PORT:-3000}"
AUTH_SERVICE_PORT="${AUTH_SERVICE_PORT:-3005}"
MATCHING_ENGINE_PORT="${MATCHING_ENGINE_PORT:-3006}"
LEDGER_SERVICE_PORT="${LEDGER_SERVICE_PORT:-3007}"
WALLET_ROUTER_PORT="${WALLET_ROUTER_PORT:-3008}"
BITGO_INGESTOR_PORT="${BITGO_INGESTOR_PORT:-3002}"
ANIMICA_INDEXER_PORT="${ANIMICA_INDEXER_PORT:-3009}"
RISK_SERVICE_PORT="${RISK_SERVICE_PORT:-3010}"
ADMIN_SERVICE_PORT="${ADMIN_SERVICE_PORT:-4000}"
FRONTEND_URL="${FRONTEND_URL:-https://trade.animica.org}"
GOOGLE_CALLBACK_URL="${GOOGLE_CALLBACK_URL:-https://api.animica.io/api/v1/auth/google/callback}"
AUTH_SERVICE_URL="${AUTH_SERVICE_URL:-http://127.0.0.1:${AUTH_SERVICE_PORT}}"

info() { echo "[INFO] $*"; }
ok()   { echo "[OK]   $*"; }
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
need_cmd pkill
need_cmd python3

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
    try: s.close()
    except: pass
PY
}

wait_for_port() {
  local host="$1"
  local port="$2"
  local timeout="${3:-20}"

  for _ in $(seq 1 "$timeout"); do
    if probe_tcp "$host" "$port"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cleanup_old() {
  info "Cleaning old application processes..."
  pkill -f "tsx watch" || true
  pkill -f "vite" || true
  sleep 1
}

start_background_process() {
  local name="$1"
  local cmd="$2"

  local log_file="$LOG_DIR/$name.log"
  local pid_file="$PID_DIR/$name.pid"

  : > "$log_file"
  info "Starting $name"

  bash -lc "
    cd '$ROOT'
    exec $cmd
  " >"$log_file" 2>&1 &

  echo $! > "$pid_file"
}

start_postgres() {
  if probe_tcp 127.0.0.1 5432; then
    ok "PostgreSQL already running"
    return 0
  fi

  info "PostgreSQL is down; attempting to start locally"

  if command -v pg_ctl >/dev/null 2>&1 && [[ -n "${PGDATA:-}" ]] && [[ -d "$PGDATA" ]]; then
    pg_ctl -D "$PGDATA" -l "$LOG_DIR/postgres.log" start >/dev/null 2>&1 || true
    if wait_for_port 127.0.0.1 5432 20; then
      printf 'pg_ctl:%s\n' "$PGDATA" > "$PID_DIR/postgres.managed"
      ok "PostgreSQL started with pg_ctl"
      return 0
    fi
  fi

  if command -v pg_lsclusters >/dev/null 2>&1 && command -v pg_ctlcluster >/dev/null 2>&1; then
    local cluster
    cluster=$(pg_lsclusters --no-header | awk 'NR==1 {print $1":"$2}')
    if [[ -n "${cluster:-}" ]]; then
      local ver name
      ver="${cluster%%:*}"
      name="${cluster#*:}"
      pg_ctlcluster --skip-systemctl-redirect "$ver" "$name" start >/dev/null 2>&1 || true
      if wait_for_port 127.0.0.1 5432 20; then
        printf 'pg_ctlcluster:%s:%s\n' "$ver" "$name" > "$PID_DIR/postgres.managed"
        ok "PostgreSQL started with pg_ctlcluster"
        return 0
      fi
    fi
  fi

  if command -v service >/dev/null 2>&1; then
    service postgresql start >/dev/null 2>&1 || true
    if wait_for_port 127.0.0.1 5432 20; then
      printf 'service:postgresql\n' > "$PID_DIR/postgres.managed"
      ok "PostgreSQL started with service"
      return 0
    fi
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl start postgresql >/dev/null 2>&1 || true
    if wait_for_port 127.0.0.1 5432 20; then
      printf 'systemctl:postgresql\n' > "$PID_DIR/postgres.managed"
      ok "PostgreSQL started with systemctl"
      return 0
    fi
  fi

  err "Could not start PostgreSQL automatically. Start it manually and retry."
  err "Expected endpoint: 127.0.0.1:5432"
  exit 1
}

start_redis() {
  if probe_tcp 127.0.0.1 6379; then
    ok "Redis already running"
    return 0
  fi

  command -v redis-server >/dev/null 2>&1 || {
    err "redis-server not found and Redis is not running on 127.0.0.1:6379"
    exit 1
  }

  start_background_process "redis" "redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no"

  if wait_for_port 127.0.0.1 6379 20; then
    ok "Redis started"
  else
    err "Redis failed to start"
    tail -n 50 "$LOG_DIR/redis.log" || true
    exit 1
  fi
}

start_nats() {
  if probe_tcp 127.0.0.1 4222; then
    ok "NATS already running"
    return 0
  fi

  local nats_bin=""
  if command -v nats-server >/dev/null 2>&1; then
    nats_bin="$(command -v nats-server)"
  elif [[ -x "$ROOT/nats-server-v2.10.7-linux-amd64/nats-server" ]]; then
    nats_bin="$ROOT/nats-server-v2.10.7-linux-amd64/nats-server"
  fi

  if [[ -z "$nats_bin" ]]; then
    err "nats-server not found and NATS is not running on 127.0.0.1:4222"
    exit 1
  fi

  start_background_process "nats" "$nats_bin -js -m 8222 -a 127.0.0.1 -p 4222"

  if wait_for_port 127.0.0.1 4222 20; then
    ok "NATS started"
  else
    err "NATS failed to start"
    tail -n 50 "$LOG_DIR/nats.log" || true
    exit 1
  fi
}

ensure_animica_rpc() {
  curl -fsS --max-time 5 \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}' \
    "$ANIMICA_RPC_URL" >/dev/null || {
      err "Animica RPC not responding at $ANIMICA_RPC_URL"
      exit 1
    }
  ok "Animica RPC OK"
}

start_service() {
  local name="$1"
  local port="$2"
  local cmd="$3"

  local log_file="$LOG_DIR/$name.log"
  local pid_file="$PID_DIR/$name.pid"

  : > "$log_file"

  info "Starting $name on port $port"

  bash -lc "
    cd '$ROOT'
    export PORT='$port'
    export HOST='0.0.0.0'
    export NODE_ENV='${NODE_ENV}'
    export NATS_URL='$NATS_URL'
    export REDIS_URL='$REDIS_URL'
    export DATABASE_URL='$DATABASE_URL'
    export DB_HOST='$DB_HOST'
    export DB_PORT='$DB_PORT'
    export DB_USER='$DB_USER'
    export DB_PASSWORD='$DB_PASSWORD'
    export DB_NAME='$DB_NAME'
    export ANIMICA_RPC_URL='$ANIMICA_RPC_URL'
    export BITGO_ENV='${BITGO_ENV:-test}'
    export BITGO_ACCESS_TOKEN='${BITGO_ACCESS_TOKEN:-dev-token}'
    export BITGO_WEBHOOK_SECRET='${BITGO_WEBHOOK_SECRET:-dev-webhook-secret}'
    export BITGO_BASE_URL='${BITGO_BASE_URL:-https://app.bitgo-test.com}'
    export ADMIN_API_KEY='${ADMIN_API_KEY:-dev-admin-key}'
    export FRONTEND_URL='${FRONTEND_URL}'
    export GOOGLE_CLIENT_ID='${GOOGLE_CLIENT_ID:-}'
    export GOOGLE_CLIENT_SECRET='${GOOGLE_CLIENT_SECRET:-}'
    export GOOGLE_CALLBACK_URL='${GOOGLE_CALLBACK_URL}'
    export AUTH_SERVICE_URL='${AUTH_SERVICE_URL}'
    $cmd
  " >"$log_file" 2>&1 &

  echo $! > "$pid_file"

  sleep 3

  ss -ltn | grep -q ":$port" || {
    err "$name failed to start (port $port not open)"
    tail -n 50 "$log_file" || true
    exit 1
  }

  ok "$name running on $port"
}

main() {
  info "Starting Animica CEX (BARE METAL MODE)"
  info "Root: $ROOT"
  info "Env:  $ENV_FILE"

  cleanup_old

  start_postgres
  start_redis
  start_nats
  ensure_animica_rpc

  info "Running migrations..."
  pnpm --filter @cex/db migrate

  info "Starting services..."
  start_service exchange-web        5175                   "pnpm --filter @cex/exchange-web dev -- --host 0.0.0.0 --port 5175"
  start_service api-gateway         "$API_GATEWAY_PORT"  "pnpm --filter @cex/api-gateway dev"
  start_service auth-service        "$AUTH_SERVICE_PORT" "pnpm --filter @cex/auth-service dev"
  start_service matching-engine     "$MATCHING_ENGINE_PORT" "pnpm --filter @cex/matching-engine dev"
  start_service ledger-service      "$LEDGER_SERVICE_PORT" "pnpm --filter @cex/ledger-service dev"
  start_service wallet-router       "$WALLET_ROUTER_PORT" "pnpm --filter @cex/wallet-router dev"
  start_service bitgo-webhook       "$BITGO_INGESTOR_PORT" "pnpm --filter @cex/bitgo-webhook-ingestor dev"
  start_service animica-indexer     "$ANIMICA_INDEXER_PORT" "pnpm --filter @cex/animica-indexer dev"
  start_service risk-service        "$RISK_SERVICE_PORT" "pnpm --filter @cex/risk-service dev"
  start_service withdrawals-service 3011                   "pnpm --filter @cex/withdrawals-service dev"
  start_service animica-asset       3012                   "pnpm --filter @cex/animica-asset-service dev"
  start_service admin-service       "$ADMIN_SERVICE_PORT" "pnpm --filter @cex/admin-service dev"

  ok "CEX fully started (bare metal mode)"
  echo
  echo "Logs: tail -f $LOG_DIR/*.log"
}

main "$@"
