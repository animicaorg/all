#!/usr/bin/env bash
# Integration test for setup.sh liboqs rebuild behavior
# This tests that setup.sh always rebuilds liboqs unless --skip-liboqs-rebuild is used

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
GREEN='\033[32m'
RED='\033[31m'
BLUE='\033[34m'
RESET='\033[0m'

test_pass() { echo -e "${GREEN}✓ PASS${RESET}: $*"; }
test_fail() { echo -e "${RED}✗ FAIL${RESET}: $*"; exit 1; }
test_info() { echo -e "${BLUE}INFO${RESET}: $*"; }

# Test 1: Verify flag parsing
test_flag_parsing() {
  test_info "Testing --skip-liboqs-rebuild flag parsing..."
  
  # Check that flag is defined and parsed
  if grep -q "SKIP_LIBOQS_REBUILD=false" "$ROOT_DIR/setup.sh"; then
    test_pass "SKIP_LIBOQS_REBUILD flag is defined with default false"
  else
    test_fail "SKIP_LIBOQS_REBUILD flag should be defined"
  fi
  
  if grep -q "\-\-skip-liboqs-rebuild)" "$ROOT_DIR/setup.sh"; then
    test_pass "Flag parsing code exists for --skip-liboqs-rebuild"
  else
    test_fail "Missing flag parsing for --skip-liboqs-rebuild"
  fi
}

# Test 2: Verify forced rebuild logic
test_forced_rebuild() {
  test_info "Testing forced rebuild logic..."
  
  # Check that rebuild is default behavior
  if grep -q 'if \[\[ "\$SKIP_LIBOQS_REBUILD" == "false" \]\]' "$ROOT_DIR/setup.sh"; then
    test_pass "Script checks SKIP_LIBOQS_REBUILD flag"
  else
    test_fail "Missing SKIP_LIBOQS_REBUILD check"
  fi
  
  # Check that build_liboqs_from_source is called by default
  if grep -A 3 'SKIP_LIBOQS_REBUILD" == "false"' "$ROOT_DIR/setup.sh" | grep -q "build_liboqs_from_source"; then
    test_pass "build_liboqs_from_source is called when flag is false"
  else
    test_fail "build_liboqs_from_source should be called by default"
  fi
}

# Test 3: Verify skip logic when flag is set
test_skip_logic() {
  test_info "Testing skip logic when --skip-liboqs-rebuild is set..."
  
  # Check that there's an else branch
  if grep -A 10 'SKIP_LIBOQS_REBUILD" == "false"' "$ROOT_DIR/setup.sh" | grep -q "else"; then
    test_pass "Script has else branch for skip case"
  else
    test_fail "Missing else branch for skip case"
  fi
  
  # Check that it uses existing installation if available
  if grep -A 15 'SKIP_LIBOQS_REBUILD" == "false"' "$ROOT_DIR/setup.sh" | grep -q "Using existing liboqs installation"; then
    test_pass "Script logs when using existing installation"
  else
    test_fail "Should log when using existing installation"
  fi
}

# Test 4: Verify directory cleanup happens
test_directory_cleanup() {
  test_info "Testing that previous builds are cleaned up..."
  
  # Check that rm -rf is called on LIBOQS_DIR
  if grep -A 5 "Always clean up previous builds" "$ROOT_DIR/setup.sh" | grep -q "rm -rf"; then
    test_pass "Script removes previous liboqs builds"
  else
    test_fail "Script should remove previous builds to ensure fresh installation"
  fi
}

# Test 5: Verify logging improvements
test_logging_improvements() {
  test_info "Testing logging improvements..."
  
  # Check for clear separator lines
  if grep -q "═══" "$ROOT_DIR/setup.sh" || grep -q "════" "$ROOT_DIR/setup.sh"; then
    test_pass "Script uses visual separators for clarity"
  else
    test_fail "Script should use visual separators for important sections"
  fi
  
  # Check that install location is logged
  if grep -q "Install location:" "$ROOT_DIR/setup.sh"; then
    test_pass "Install location is clearly logged"
  else
    test_fail "Install location should be clearly logged"
  fi
  
  # Check for library path logging
  if grep -q "Library path:" "$ROOT_DIR/setup.sh"; then
    test_pass "Library path is logged"
  else
    test_fail "Library path should be logged"
  fi
}

# Test 6: Verify usage message
test_usage_message() {
  test_info "Testing usage message..."
  
  # Check for usage message when unknown argument is passed
  if grep -q "Usage:.*skip-liboqs-rebuild" "$ROOT_DIR/setup.sh"; then
    test_pass "Usage message includes --skip-liboqs-rebuild flag"
  else
    test_fail "Usage message should document --skip-liboqs-rebuild flag"
  fi
}

# Test 7: Verify environment variables are always set
test_env_vars_always_set() {
  test_info "Testing that environment variables are set in all code paths..."
  
  # Check that setup_liboqs_env_vars is called in skip path
  if grep -A 5 "Using existing liboqs installation" "$ROOT_DIR/setup.sh" | grep -q "setup_liboqs_env_vars"; then
    test_pass "Environment variables are set even when skipping rebuild"
  else
    test_fail "Environment variables should be set in skip path"
  fi
}

# Run all tests
main() {
  echo "=========================================="
  echo "Testing setup.sh liboqs rebuild behavior"
  echo "=========================================="
  echo ""
  
  test_flag_parsing
  test_forced_rebuild
  test_skip_logic
  test_directory_cleanup
  test_logging_improvements
  test_usage_message
  test_env_vars_always_set
  
  echo ""
  echo "=========================================="
  echo -e "${GREEN}All rebuild behavior tests passed!${RESET}"
  echo "=========================================="
}

main "$@"
