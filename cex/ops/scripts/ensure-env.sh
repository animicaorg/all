#!/usr/bin/env bash
# Shared utility helpers for ops scripts.

ensure_env_file() {
  local root_dir="${1:?Root directory required}"
  local env_file="$root_dir/env/.env"
  local env_example="$root_dir/env/.env.example"

  if [[ ! -f "$env_file" ]]; then
    if [[ -f "$env_example" ]]; then
      echo "⚠️  .env file not found. Creating from .env.example..."
      cp "$env_example" "$env_file"
      echo "✅ Created $env_file"
      echo "📝 Please review and update the environment variables if needed."
      echo ""
    else
      echo "❌ Error: Neither $env_file nor $env_example found."
      echo "Please create $env_file with required database configuration."
      exit 1
    fi
  fi
}

load_env_file() {
  local root_dir="${1:?Root directory required}"
  local env_file="$root_dir/env/.env"

  if [[ ! -f "$env_file" ]]; then
    echo "❌ Error: Missing env file: $env_file"
    exit 1
  fi

  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

normalize_local_endpoints() {
  local localhost="${1:-127.0.0.1}"

  export DB_HOST="${DB_HOST:-$localhost}"
  export DB_PORT="${DB_PORT:-5432}"
  if [[ "$DB_HOST" == "postgres" ]]; then
    DB_HOST="$localhost"
    export DB_HOST
  fi

  export DB_USER="${DB_USER:-cex}"
  export DB_PASSWORD="${DB_PASSWORD:-cex_password}"
  export DB_NAME="${DB_NAME:-cex_exchange}"

  if [[ -z "${DATABASE_URL:-}" ]]; then
    DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
  else
    DATABASE_URL="${DATABASE_URL/@postgres:/@$localhost:}"
    DATABASE_URL="${DATABASE_URL/@postgres\//@$localhost/}"
  fi
  export DATABASE_URL

  REDIS_URL="${REDIS_URL:-redis://$localhost:6379}"
  REDIS_URL="${REDIS_URL/@redis:/@$localhost:}"
  REDIS_URL="${REDIS_URL/redis:\/\/redis:/redis:\/\/$localhost:}"
  export REDIS_URL

  NATS_URL="${NATS_URL:-nats://$localhost:4222}"
  NATS_URL="${NATS_URL/@nats:/@$localhost:}"
  NATS_URL="${NATS_URL/nats:\/\/nats:/nats:\/\/$localhost:}"
  export NATS_URL
}
