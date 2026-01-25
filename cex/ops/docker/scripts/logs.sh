#!/usr/bin/env bash
# =============================================================================
# View Service Logs Script
# =============================================================================
# Usage: ./logs.sh [service] [--follow] [--tail N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICE="${1:-}"
FOLLOW_FLAG=""
TAIL_FLAG=""

# Parse arguments
shift || true
while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--follow)
            FOLLOW_FLAG="-f"
            shift
            ;;
        -t|--tail)
            TAIL_FLAG="--tail ${2:-100}"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [service] [--follow] [--tail N]"
            exit 1
            ;;
    esac
done

cd "$OPS_DIR/docker"

if [ -z "$SERVICE" ]; then
    echo "Available services:"
    docker compose -f docker-compose.dev.yml ps --services
    echo ""
    echo "Usage: $0 <service> [--follow] [--tail N]"
    exit 0
fi

# shellcheck disable=SC2086
docker compose -f docker-compose.dev.yml logs $FOLLOW_FLAG $TAIL_FLAG "$SERVICE"
