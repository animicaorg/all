#!/usr/bin/env bash
# =============================================================================
# E2E Test Execution Script
# =============================================================================
# Main entry point for running E2E tests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CEX_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
E2E_DIR="${CEX_DIR}/tests/e2e"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default values
KEEP_STACK=false
AUTO_START=true

# Parse arguments
ARGS=("$@")

for arg in "${ARGS[@]}"; do
    if [ "$arg" = "--keep" ]; then
        KEEP_STACK=true
    fi
    if [ "$arg" = "--no-auto-start" ]; then
        AUTO_START=false
    fi
done

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║              CEX E2E Test Harness                             ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Start stack if requested
if [ "$AUTO_START" = true ]; then
    echo -e "${BLUE}▶ Starting E2E stack...${NC}"
    "${SCRIPT_DIR}/e2e_up.sh"
    echo ""
else
    echo -e "${YELLOW}⚠️  Skipping stack startup (--no-auto-start)${NC}"
    echo ""
fi

# Install dependencies
echo -e "${BLUE}▶ Installing E2E test dependencies...${NC}"
cd "$E2E_DIR"
if command -v pnpm &> /dev/null; then
    pnpm install
else
    npm install
fi

# Run E2E tests
echo ""
echo -e "${BLUE}▶ Running E2E tests...${NC}"
echo ""

# Pass through all arguments
if command -v pnpm &> /dev/null; then
    pnpm e2e "${ARGS[@]}" || TEST_EXIT_CODE=$?
else
    npm run e2e -- "${ARGS[@]}" || TEST_EXIT_CODE=$?
fi

TEST_EXIT_CODE=${TEST_EXIT_CODE:-0}

echo ""

# Collect logs
echo -e "${BLUE}▶ Collecting service logs...${NC}"
LOG_FILE="${E2E_DIR}/artifacts/logs-$(date +%s).txt"
docker compose -f "${SCRIPT_DIR}/../docker-compose.e2e.yml" -p cex-e2e logs > "$LOG_FILE" 2>&1
echo -e "   Logs saved to: ${LOG_FILE}"
echo ""

# Tear down stack if not keeping
if [ "$KEEP_STACK" = false ]; then
    echo -e "${BLUE}▶ Tearing down E2E stack...${NC}"
    "${SCRIPT_DIR}/e2e_down.sh"
else
    echo -e "${YELLOW}⚠️  Stack kept running (--keep flag)${NC}"
    echo -e "   Stop with: ${SCRIPT_DIR}/e2e_down.sh"
fi

echo ""

# Exit with test result
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ E2E tests passed!${NC}"
    exit 0
else
    echo -e "${RED}❌ E2E tests failed!${NC}"
    exit 1
fi
