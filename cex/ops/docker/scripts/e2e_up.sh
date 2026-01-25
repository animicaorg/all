#!/usr/bin/env bash
# =============================================================================
# E2E Stack Startup Script
# =============================================================================
# Brings up the E2E test environment with all services

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/../docker-compose.e2e.yml"
PROJECT_NAME="cex-e2e"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Starting E2E Test Stack${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Check if chaos profile requested
PROFILES=""
if [ "${ENABLE_CHAOS:-false}" = "true" ]; then
    PROFILES="--profile chaos"
    echo -e "${YELLOW}▶ Chaos testing enabled${NC}"
fi

if [ "${ENABLE_STORAGE:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile storage"
    echo -e "${YELLOW}▶ MinIO storage enabled${NC}"
fi

# Bring up infrastructure first
echo -e "${BLUE}▶ Starting infrastructure...${NC}"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" $PROFILES up -d postgres redis nats

# Wait for infrastructure
echo -e "${BLUE}▶ Waiting for infrastructure to be healthy...${NC}"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T postgres pg_isready -U cex || true
sleep 5

# Run migrations
echo -e "${BLUE}▶ Running database migrations...${NC}"
"${SCRIPT_DIR}/migrate.sh" || echo -e "${YELLOW}⚠️  Migrations may have failed (continuing anyway)${NC}"

# Start Animica devnet
echo -e "${BLUE}▶ Starting Animica devnet...${NC}"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" $PROFILES up -d animica-devnet

# Start exchange services
echo -e "${BLUE}▶ Starting exchange services...${NC}"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" $PROFILES up -d \
    api-gateway \
    admin-service \
    matching-engine \
    ledger-service \
    animica-asset-service \
    withdrawals-service \
    bitgo-webhook-ingestor

# Wait for services to be healthy
echo -e "${BLUE}▶ Waiting for services to be healthy...${NC}"
echo -e "   This may take 30-60 seconds..."

max_attempts=60
attempt=0

while [ $attempt -lt $max_attempts ]; do
    if docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps | grep -q "unhealthy"; then
        echo -n "."
        sleep 2
        ((attempt++))
    elif docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps | grep -q "(starting)"; then
        echo -n "."
        sleep 2
        ((attempt++))
    else
        break
    fi
done

echo ""

# Seed admin user and test data
echo -e "${BLUE}▶ Seeding test data...${NC}"
"${SCRIPT_DIR}/seed_admin.sh" || echo -e "${YELLOW}⚠️  Seeding may have failed (continuing anyway)${NC}"

# Show service status
echo ""
echo -e "${GREEN}✅ E2E stack is ready!${NC}"
echo ""
echo -e "${BLUE}Services:${NC}"
echo -e "  API Gateway:       http://localhost:13000"
echo -e "  Admin API:         http://localhost:13001"
echo -e "  Animica RPC:       http://localhost:18545"
echo -e "  PostgreSQL:        localhost:15432"
echo -e "  Redis:             localhost:16379"
echo -e "  NATS:              localhost:14222"
echo ""

if [ "${ENABLE_CHAOS:-false}" = "true" ]; then
    echo -e "  Toxiproxy:         http://localhost:18474"
    echo ""
fi

echo -e "${BLUE}View logs:${NC}"
echo -e "  docker compose -f $COMPOSE_FILE -p $PROJECT_NAME logs -f"
echo ""
