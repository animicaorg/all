#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

# Source shared utility
source "$SCRIPT_DIR/ensure-env.sh"

# Ensure .env file exists
ensure_env_file "$ROOT_DIR"

docker compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ROOT_DIR/env/.env" run --rm migrate
