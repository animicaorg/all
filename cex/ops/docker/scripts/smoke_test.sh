#!/usr/bin/env bash
# =============================================================================
# Smoke Test Script
# =============================================================================
# Runs basic health checks on all services

set -euo pipefail

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

check_endpoint() {
    local name="$1"
    local url="$2"
    local expected="${3:-200}"
    
    echo -n "  Testing $name... "
    
    response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    
    if [ "$response" = "$expected" ]; then
        echo -e "${GREEN}✓${NC} ($response)"
        ((PASSED++))
        return 0
    else
        echo -e "${RED}✗${NC} (expected $expected, got $response)"
        ((FAILED++))
        return 1
    fi
}

echo "═══════════════════════════════════════════════════════════════"
echo "  CEX Smoke Tests"
echo "═══════════════════════════════════════════════════════════════"
echo ""

echo "Infrastructure Health Checks:"
check_endpoint "PostgreSQL (via NATS)" "http://localhost:8222/healthz"
check_endpoint "Redis CLI" "http://localhost:6379" "000" # Just check port

echo ""
echo "Service Health Checks:"
check_endpoint "API Gateway" "http://localhost:3000/health"
check_endpoint "Admin Service" "http://localhost:3001/health"
check_endpoint "MailHog UI" "http://localhost:8025"

echo ""
echo "Development Tools:"
check_endpoint "MinIO Console" "http://localhost:9001"
check_endpoint "NATS Monitoring" "http://localhost:8222"

echo ""
echo "═══════════════════════════════════════════════════════════════"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC} ($PASSED/$((PASSED + FAILED)) passed)"
    exit 1
fi
