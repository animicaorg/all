#!/usr/bin/env bash
# =============================================================================
# E2E Chaos Testing Helper Script
# =============================================================================
# Convenient wrapper for running chaos scenarios

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  E2E Chaos Testing${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Enable chaos profile
export ENABLE_CHAOS=true

# Start stack with chaos tools
echo -e "${BLUE}▶ Starting E2E stack with chaos tools...${NC}"
"${SCRIPT_DIR}/e2e_up.sh"
echo ""

# Menu
echo -e "${BLUE}Select chaos scenario:${NC}"
echo "  1) Kill/Restart services"
echo "  2) Network partitions"
echo "  3) Both"
echo "  4) Custom"
echo ""
read -p "Enter choice [1-4]: " choice

case $choice in
  1)
    SCENARIO="chaos_kill_restart"
    ;;
  2)
    SCENARIO="chaos_partition"
    ;;
  3)
    SCENARIO="chaos_kill_restart,chaos_partition"
    ;;
  4)
    read -p "Enter scenario name: " SCENARIO
    ;;
  *)
    echo "Invalid choice"
    exit 1
    ;;
esac

# Run chaos test
echo ""
echo -e "${BLUE}▶ Running chaos scenario(s): ${SCENARIO}${NC}"
echo ""

cd "$(dirname "$SCRIPT_DIR")/../tests/e2e"

if command -v pnpm &> /dev/null; then
    pnpm e2e -- --scenario "$SCENARIO" --chaos true --duration 300
else
    npm run e2e -- --scenario "$SCENARIO" --chaos true --duration 300
fi

# Ask if user wants to keep stack running
echo ""
read -p "Keep stack running for manual testing? [y/N]: " keep_running

if [ "${keep_running,,}" != "y" ]; then
    echo -e "${BLUE}▶ Tearing down stack...${NC}"
    "${SCRIPT_DIR}/e2e_down.sh"
else
    echo -e "${YELLOW}Stack kept running. Stop with: ${SCRIPT_DIR}/e2e_down.sh${NC}"
fi
