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
LEDGER_SERVICE_URL="${LEDGER_SERVICE_URL:-http://127.0.0.1:${LEDGER_SERVICE_PORT}}"
WALLET_ROUTER_PORT="${WALLET_ROUTER_PORT:-3008}"
BITGO_INGESTOR_PORT="${BITGO_INGESTOR_PORT:-3002}"
ANIMICA_INDEXER_PORT="${ANIMICA_INDEXER_PORT:-3009}"
RISK_SERVICE_PORT="${RISK_SERVICE_PORT:-3010}"
ADMIN_SERVICE_PORT="${ADMIN_SERVICE_PORT:-3001}"
FRONTEND_URL="${FRONTEND_URL:-https://trade.animica.org}"
GOOGLE_CALLBACK_URL="${GOOGLE_CALLBACK_URL:-https://api.animica.io/api/v1/auth/google/callback}"
AUTH_SERVICE_URL="${AUTH_SERVICE_URL:-http://127.0.0.1:${AUTH_SERVICE_PORT}}"

ANIMICA_ROOT="${ANIMICA_ROOT:-$(cd "$ROOT/.." && pwd)}"
ADMIN_API_DIR="${ADMIN_API_DIR:-$ANIMICA_ROOT/services/admin-api}"
ADMIN_WEB_DIR="${ADMIN_WEB_DIR:-$ANIMICA_ROOT/apps/admin-web}"
ADMIN_API_SCHEMA_SQL="${ADMIN_API_SCHEMA_SQL:-$ROOT/ops/sql/admin-api-bootstrap.sql}"
ADMIN_API_PORT="${ADMIN_API_PORT:-4000}"
ADMIN_WEB_PORT="${ADMIN_WEB_PORT:-5173}"
ADMIN_WEB_URL="${ADMIN_WEB_URL:-http://localhost:${ADMIN_WEB_PORT}}"

SESSION_SECRET="${SESSION_SECRET:-dev-cex-session-secret-change-me-32}"
ADMIN_API_SESSION_SECRET="${ADMIN_API_SESSION_SECRET:-$SESSION_SECRET}"
JWT_SECRET="${JWT_SECRET:-dev-admin-jwt-secret-change-me-32chars}"
CSRF_SECRET="${CSRF_SECRET:-dev-admin-csrf-secret-change-me-32ch}"
ADMIN_BOOTSTRAP_SECRET="${ADMIN_BOOTSTRAP_SECRET:-$SESSION_SECRET}"
CONFIG_ENCRYPTION_KEY="${CONFIG_ENCRYPTION_KEY:-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef}"

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
  pkill -f "$ROOT/services/.*/tsx/dist/cli.mjs watch src/index.ts" || true
  pkill -f "$ROOT/services/.*/tsx/dist/cli.mjs src/index.ts" || true
  pkill -f "$ROOT/node_modules/.pnpm/tsx@.*src/index.ts" || true
  pkill -f "$ROOT/apps/.*/vite/bin/vite.js" || true
  pkill -f "$ADMIN_API_DIR/.*/tsx/dist/cli.mjs watch src/index.ts" || true
  pkill -f "$ADMIN_API_DIR/.*/tsx/dist/cli.mjs src/index.ts" || true
  pkill -f "$ADMIN_WEB_DIR/.*/vite/bin/vite.js" || true
  sleep 1
}

start_background_process() {
  local name="$1"
  local cmd="$2"

  local log_file="$LOG_DIR/$name.log"
  local pid_file="$PID_DIR/$name.pid"

  : > "$log_file"
  info "Starting $name"

  nohup bash -lc "
    cd '$ROOT'
    exec $cmd
  " >"$log_file" 2>&1 </dev/null &

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

  nohup bash -lc "
    cd '$ROOT'
    export PORT='$port'
    export HOST='0.0.0.0'
    export NODE_ENV='${NODE_ENV}'
    export SESSION_SECRET='${SESSION_SECRET}'
    export NATS_URL='$NATS_URL'
    export REDIS_URL='$REDIS_URL'
    export DATABASE_URL='$DATABASE_URL'
    export LEDGER_SERVICE_URL='$LEDGER_SERVICE_URL'
    export DB_HOST='$DB_HOST'
    export DB_PORT='$DB_PORT'
    export DB_USER='$DB_USER'
    export DB_PASSWORD='$DB_PASSWORD'
    export DB_NAME='$DB_NAME'
    export ANIMICA_RPC_URL='$ANIMICA_RPC_URL'
    export BITGO_ENV='${BITGO_ENV:-test}'
    export BITGO_ACCESS_TOKEN='${BITGO_ACCESS_TOKEN:-dev-token}'
    export BITGO_WEBHOOK_SECRET='${BITGO_WEBHOOK_SECRET:-dev-webhook-secret}'
    export BITGO_BASE_URL='${BITGO_BASE_URL:-${BITGO_API_URL:-https://app.bitgo-test.com}}'
    export CONFIG_ENCRYPTION_KEY='$CONFIG_ENCRYPTION_KEY'
    export ADMIN_API_KEY='${ADMIN_API_KEY:-dev-admin-key}'
    export FRONTEND_URL='${FRONTEND_URL}'
    export GOOGLE_CLIENT_ID='${GOOGLE_CLIENT_ID:-}'
    export GOOGLE_CLIENT_SECRET='${GOOGLE_CLIENT_SECRET:-}'
    export GOOGLE_CALLBACK_URL='${GOOGLE_CALLBACK_URL}'
    export AUTH_SERVICE_URL='${AUTH_SERVICE_URL}'
    $cmd
  " >"$log_file" 2>&1 </dev/null &

  echo $! > "$pid_file"

  if ! wait_for_port 127.0.0.1 "$port" 45; then
    err "$name failed to start (port $port not open)"
    tail -n 50 "$log_file" || true
    exit 1
  fi

  ok "$name running on $port"
}

prepare_admin_api_database() {
  [[ -d "$ADMIN_API_DIR" ]] || {
    err "Admin API directory not found: $ADMIN_API_DIR"
    exit 1
  }

  info "Preparing admin API Prisma schema"

  command -v psql >/dev/null 2>&1 || {
    err "psql not found; cannot prepare admin API database schema"
    exit 1
  }

  [[ -f "$ADMIN_API_SCHEMA_SQL" ]] || {
    err "Admin API schema bootstrap SQL not found: $ADMIN_API_SCHEMA_SQL"
    exit 1
  }

  local schema_log="$LOG_DIR/admin-api-schema.log"

  pnpm --dir "$ADMIN_API_DIR" exec prisma generate --schema ../exchange-api/prisma/schema.prisma
  if ! PGPASSWORD="$DB_PASSWORD" psql \
    -v ON_ERROR_STOP=1 \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -f "$ADMIN_API_SCHEMA_SQL" >"$schema_log" 2>&1; then
    err "Admin API database schema bootstrap failed"
    tail -n 80 "$schema_log" || true
    exit 1
  fi
}

start_admin_api() {
  local name="admin-api"
  local log_file="$LOG_DIR/$name.log"
  local pid_file="$PID_DIR/$name.pid"

  prepare_admin_api_database

  : > "$log_file"
  info "Starting $name on port $ADMIN_API_PORT"

  nohup bash -lc "
    cd '$ADMIN_API_DIR'
    export NODE_ENV='${NODE_ENV}'
    export SERVICE_NAME='admin-api'
    export LOG_LEVEL='${LOG_LEVEL:-info}'
    export DATABASE_URL='$DATABASE_URL'
    export REDIS_URL='$REDIS_URL'
    export HTTP_PORT='$ADMIN_API_PORT'
    export HTTP_HOST='0.0.0.0'
    export JWT_SECRET='$JWT_SECRET'
    export JWT_EXPIRES_IN='${JWT_EXPIRES_IN:-1h}'
    export REFRESH_TOKEN_EXPIRES_IN='${REFRESH_TOKEN_EXPIRES_IN:-7d}'
    export SESSION_SECRET='$ADMIN_API_SESSION_SECRET'
    export ADMIN_BOOTSTRAP_SECRET='$ADMIN_BOOTSTRAP_SECRET'
    export CONFIG_ENCRYPTION_KEY='$CONFIG_ENCRYPTION_KEY'
    export TOTP_ISSUER='${TOTP_ISSUER:-Animica Admin}'
    export TOTP_WINDOW='${TOTP_WINDOW:-2}'
    export CSRF_SECRET='$CSRF_SECRET'
    export ADMIN_WEB_URL='$ADMIN_WEB_URL'
    export CORS_CREDENTIALS='${CORS_CREDENTIALS:-true}'
    export EXCHANGE_API_URL='${EXCHANGE_API_URL:-http://localhost:${API_GATEWAY_PORT}}'
    export MATCHING_ENGINE_URL='${MATCHING_ENGINE_URL:-http://localhost:${MATCHING_ENGINE_PORT}}'
    export BITGO_ENV='${BITGO_ENV:-test}'
    export BITGO_API_URL='${BITGO_API_URL:-${BITGO_BASE_URL:-https://app.bitgo-test.com}}'
    export BITGO_ACCESS_TOKEN='${BITGO_ACCESS_TOKEN:-}'
    export ANIMICA_NODE_URL='${ANIMICA_NODE_URL:-$ANIMICA_RPC_URL}'
    exec ./node_modules/.bin/tsx src/index.ts
  " >"$log_file" 2>&1 </dev/null &

  echo $! > "$pid_file"

  if wait_for_port 127.0.0.1 "$ADMIN_API_PORT" 30; then
    ok "$name running on $ADMIN_API_PORT"
  else
    err "$name failed to start (port $ADMIN_API_PORT not open)"
    tail -n 80 "$log_file" || true
    exit 1
  fi
}

start_admin_web() {
  [[ -d "$ADMIN_WEB_DIR" ]] || {
    err "Admin web directory not found: $ADMIN_WEB_DIR"
    exit 1
  }

  local name="admin-web"
  local log_file="$LOG_DIR/$name.log"
  local pid_file="$PID_DIR/$name.pid"

  : > "$log_file"
  info "Starting $name on port $ADMIN_WEB_PORT"

  nohup bash -lc "
    cd '$ADMIN_WEB_DIR'
    export NODE_ENV='${NODE_ENV}'
    export VITE_ADMIN_API_PROXY_TARGET='http://127.0.0.1:${ADMIN_API_PORT}'
    exec ./node_modules/.bin/vite --host 0.0.0.0 --port '$ADMIN_WEB_PORT'
  " >"$log_file" 2>&1 </dev/null &

  echo $! > "$pid_file"

  if wait_for_port 127.0.0.1 "$ADMIN_WEB_PORT" 30; then
    ok "$name running on $ADMIN_WEB_PORT"
  else
    err "$name failed to start (port $ADMIN_WEB_PORT not open)"
    tail -n 80 "$log_file" || true
    exit 1
  fi
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
  start_service exchange-web        5175                   "cd apps/exchange-web && exec ./node_modules/.bin/vite --host 0.0.0.0 --port 5175"
  start_service api-gateway         "$API_GATEWAY_PORT"  "cd services/api-gateway && exec ./node_modules/.bin/tsx src/index.ts"
  start_service auth-service        "$AUTH_SERVICE_PORT" "cd services/auth-service && exec ./node_modules/.bin/tsx src/index.ts"
  start_service matching-engine     "$MATCHING_ENGINE_PORT" "cd services/matching-engine && exec ./node_modules/.bin/tsx src/index.ts"
  start_service ledger-service      "$LEDGER_SERVICE_PORT" "cd services/ledger-service && exec ./node_modules/.bin/tsx src/index.ts"
  start_service wallet-router       "$WALLET_ROUTER_PORT" "cd services/wallet-router && exec ./node_modules/.bin/tsx src/index.ts"
  start_service bitgo-webhook       "$BITGO_INGESTOR_PORT" "cd services/bitgo-webhook-ingestor && exec ./node_modules/.bin/tsx src/index.ts"
  start_service animica-indexer     "$ANIMICA_INDEXER_PORT" "cd services/animica-indexer && exec ./node_modules/.bin/tsx src/index.ts"
  start_service risk-service        "$RISK_SERVICE_PORT" "cd services/risk-service && exec ./node_modules/.bin/tsx src/index.ts"
  start_service withdrawals-service 3011                   "cd services/withdrawals-service && exec ./node_modules/.bin/tsx src/index.ts"
  start_service animica-asset       3012                   "cd services/animica-asset-service && exec ./node_modules/.bin/tsx src/index.ts"
  start_service admin-service       "$ADMIN_SERVICE_PORT" "cd services/admin-service && exec ./node_modules/.bin/tsx src/index.ts"
  start_admin_api
  start_admin_web

  ok "CEX fully started (bare metal mode)"
  echo
  echo "Exchange web: http://localhost:5175"
  echo "Admin web:    http://localhost:$ADMIN_WEB_PORT"
  echo "Admin API:    http://localhost:$ADMIN_API_PORT/admin/v1/health"
  echo
  echo "First admin login:"
  echo "  Open first-time setup in admin-web and use ADMIN_BOOTSTRAP_SECRET."
  echo "  If ADMIN_BOOTSTRAP_SECRET is not set, this script uses SESSION_SECRET for local dev."
  echo
  echo "Logs: tail -f $LOG_DIR/*.log"
}

main "$@"
