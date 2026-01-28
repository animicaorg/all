#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE="$ROOT_DIR/env/.env"
ENV_EXAMPLE="$ROOT_DIR/env/.env.example"

# Ensure .env file exists
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_EXAMPLE" ]]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "✅ Created $ENV_FILE"
    echo "📝 Please review and update the environment variables if needed."
  else
    echo "❌ Error: Neither $ENV_FILE nor $ENV_EXAMPLE found."
    echo "Please create $ENV_FILE with required database configuration."
    exit 1
  fi
fi

docker compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" up -d
