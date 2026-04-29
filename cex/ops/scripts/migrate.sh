#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OPS_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
ROOT_DIR=$(cd "$OPS_DIR/.." && pwd)

# shellcheck source=/dev/null
source "$SCRIPT_DIR/ensure-env.sh"
ensure_env_file "$OPS_DIR"
load_env_file "$OPS_DIR"
normalize_local_endpoints

cd "$ROOT_DIR"
exec pnpm --filter @cex/db migrate
