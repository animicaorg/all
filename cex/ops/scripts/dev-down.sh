#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE="$ROOT_DIR/env/.env"

# Check if .env exists, but don't error if it doesn't (down doesn't need it)
if [[ -f "$ENV_FILE" ]]; then
  docker compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" down
else
  docker compose -f "$ROOT_DIR/docker-compose.yml" down
fi
