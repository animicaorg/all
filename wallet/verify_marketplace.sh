#!/bin/bash

# Animica Flutter Wallet - Marketplace Implementation Verification Script
# 
# This script verifies that all marketplace implementation files are in place
# and provides statistics about the implementation.
#
# Usage: bash verify_marketplace.sh

set -e

WALLET_DIR="${PWD}"
BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Animica Marketplace Implementation Verification${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Array of files that should exist
declare -a FILES=(
    "lib/services/pricing_engine.dart"
    "lib/services/market_data_service.dart"
    "lib/services/payment_gateway.dart"
    "lib/state/providers.dart"
    "lib/pages/marketplace/marketplace_home_page.dart"
    "lib/pages/marketplace/buy_anm_page.dart"
    "lib/pages/marketplace/treasury_dashboard_page.dart"
    "lib/pages/marketplace/purchase_history_page.dart"
    "lib/widgets/chart_widget.dart"
    "lib/router.dart"
    "lib/router/marketplace_routes.dart"
    "MARKETPLACE_INTEGRATION_GUIDE.md"
    "MARKETPLACE_CHECKLIST.md"
    "MARKETPLACE_QUICKSTART.md"
    "MARKETPLACE_IMPLEMENTATION_SUMMARY.md"
    "MARKETPLACE_ARCHITECTURE.md"
)

# Check each file
FOUND=0
MISSING=0

echo -e "${YELLOW}Checking implementation files:${NC}"
echo ""

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        LINES=$(wc -l < "$file")
        echo -e "${GREEN}✓${NC} $file (${LINES} lines)"
        ((FOUND++))
    else
        echo -e "${RED}✗${NC} $file (MISSING)"
        ((MISSING++))
    fi
done

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "Summary: ${GREEN}$FOUND found${NC}, ${RED}$MISSING missing${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

if [ $MISSING -eq 0 ]; then
    echo -e "${GREEN}✓ All files present!${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Run: flutter pub get"
    echo "2. Run: dart analyze lib/"
    echo "3. Run: flutter run -t lib/main.dart"
    echo ""
    echo "Documentation:"
    echo "  • Quick start: MARKETPLACE_QUICKSTART.md"
    echo "  • Full guide: MARKETPLACE_INTEGRATION_GUIDE.md"
    echo "  • Testing: MARKETPLACE_CHECKLIST.md"
    echo "  • Architecture: MARKETPLACE_ARCHITECTURE.md"
    echo "  • Summary: MARKETPLACE_IMPLEMENTATION_SUMMARY.md"
else
    echo -e "${RED}✗ Some files are missing!${NC}"
    echo "Please create the missing files before proceeding."
    exit 1
fi
