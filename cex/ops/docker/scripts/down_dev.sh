#!/usr/bin/env bash
# =============================================================================
# Development Environment Shutdown Script
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$OPS_DIR/docker"

echo "Stopping CEX development environment..."

# Stop all services
docker compose -f docker-compose.dev.yml down

# Check if monitoring is running and stop it
if docker compose -f docker-compose.monitoring.yml ps --services &>/dev/null; then
    echo "Stopping monitoring stack..."
    docker compose -f docker-compose.monitoring.yml down
fi

echo "✓ All services stopped"
echo ""
echo "To remove volumes as well, run:"
echo "  docker compose -f docker-compose.dev.yml down -v"
