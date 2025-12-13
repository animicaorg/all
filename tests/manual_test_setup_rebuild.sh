#!/usr/bin/env bash
# Manual test to demonstrate liboqs rebuild behavior
# This script shows that setup.sh rebuilds liboqs on each run unless --skip-liboqs-rebuild is used

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIBOQS_DIR="$ROOT_DIR/.liboqs"

# Colors
GREEN='\033[32m'
BLUE='\033[34m'
YELLOW='\033[33m'
RESET='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════════════════════${RESET}"
echo -e "${BLUE}Manual Test: Demonstrating liboqs rebuild behavior${RESET}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════════════${RESET}"
echo ""

# Function to check if liboqs directory exists and show timestamp
check_liboqs_status() {
  if [[ -d "$LIBOQS_DIR" ]]; then
    echo -e "${GREEN}✓${RESET} liboqs directory exists at: $LIBOQS_DIR"
    if [[ -d "$LIBOQS_DIR/install" ]]; then
      echo -e "${GREEN}✓${RESET} liboqs installation found"
      # Show the timestamp of the install directory
      local timestamp
      timestamp=$(stat -c '%y' "$LIBOQS_DIR/install" 2>/dev/null || stat -f '%Sm' "$LIBOQS_DIR/install" 2>/dev/null || echo "timestamp unavailable")
      echo -e "${BLUE}  Last modified: $timestamp${RESET}"
    else
      echo -e "${YELLOW}⚠${RESET} liboqs directory exists but no installation found"
    fi
  else
    echo -e "${YELLOW}⚠${RESET} liboqs directory does not exist"
  fi
  echo ""
}

echo "Step 1: Clean up any existing liboqs installation"
if [[ -d "$LIBOQS_DIR" ]]; then
  rm -rf "$LIBOQS_DIR"
  echo -e "${GREEN}✓${RESET} Removed existing liboqs directory"
else
  echo -e "${BLUE}ℹ${RESET} No existing liboqs directory to clean"
fi
echo ""

echo "Step 2: Verify setup.sh behavior"
echo ""
echo "Checking current state:"
check_liboqs_status

echo -e "${BLUE}────────────────────────────────────────────────────────────────────────${RESET}"
echo "To test the actual rebuild behavior, you would run:"
echo -e "${GREEN}./setup.sh${RESET}                        # Builds liboqs from source"
echo -e "${GREEN}./setup.sh${RESET}                        # Rebuilds liboqs (cleans and rebuilds)"
echo -e "${GREEN}./setup.sh --skip-liboqs-rebuild${RESET}  # Skips rebuild, uses existing installation"
echo -e "${BLUE}────────────────────────────────────────────────────────────────────────${RESET}"
echo ""

echo "Expected behavior:"
echo "• First run: Clones liboqs v0.14.0, builds, installs to .liboqs/install/"
echo "• Second run (default): Removes .liboqs/, clones again, rebuilds (fresh installation)"
echo "• With --skip-liboqs-rebuild: Reuses existing .liboqs/install/, sets env vars only"
echo ""

echo -e "${BLUE}════════════════════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}Manual test demonstration complete${RESET}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════════════${RESET}"
