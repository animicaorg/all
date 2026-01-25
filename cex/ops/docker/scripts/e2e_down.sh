#!/usr/bin/env bash
# =============================================================================
# E2E Stack Teardown Script
# =============================================================================
# Stops and removes all E2E test containers and volumes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/../docker-compose.e2e.yml"
PROJECT_NAME="cex-e2e"

# Colors
BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Stopping E2E Test Stack${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Stop all services
echo -e "${BLUE}▶ Stopping services...${NC}"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" --profile chaos --profile storage down

# Remove volumes if requested
if [ "${REMOVE_VOLUMES:-false}" = "true" ]; then
    echo -e "${BLUE}▶ Removing volumes...${NC}"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down -v
fi

echo ""
echo -e "${GREEN}✅ E2E stack stopped${NC}"
echo ""
