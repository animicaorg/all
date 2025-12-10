#!/usr/bin/env bash
# Test script for setup.sh liboqs installation logic
# This tests various scenarios to ensure proper error handling and fallback logic

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

# Test 1: Verify script syntax
test_syntax() {
  test_info "Testing bash syntax of setup.sh..."
  if bash -n "$ROOT_DIR/setup.sh"; then
    test_pass "setup.sh has valid bash syntax"
  else
    test_fail "setup.sh has syntax errors"
  fi
}

# Test 2: Verify all required functions exist
test_functions() {
  test_info "Testing that all required functions are defined..."
  local required_functions=(
    "detect_os"
    "check_build_prerequisites"
    "setup_liboqs_env_vars"
    "build_liboqs_from_source"
  )
  
  for func in "${required_functions[@]}"; do
    if grep -q "^${func}()" "$ROOT_DIR/setup.sh"; then
      test_pass "Function $func is defined"
    else
      test_fail "Function $func is missing"
    fi
  done
}

# Test 3: Verify error messages are actionable
test_error_messages() {
  test_info "Testing that error messages are actionable..."
  
  # Check for prerequisite installation instructions
  if grep -q "Ubuntu/Debian: sudo apt-get install cmake build-essential" "$ROOT_DIR/setup.sh"; then
    test_pass "Prerequisites error message includes Ubuntu/Debian instructions"
  else
    test_fail "Missing Ubuntu/Debian instructions in prerequisites error"
  fi
  
  if grep -q "macOS: brew install cmake" "$ROOT_DIR/setup.sh"; then
    test_pass "Prerequisites error message includes macOS instructions"
  else
    test_fail "Missing macOS instructions in prerequisites error"
  fi
  
  # Check for git installation instructions
  if grep -q "git is required to build liboqs from source" "$ROOT_DIR/setup.sh"; then
    test_pass "Git check with error message exists"
  else
    test_fail "Missing git check error message"
  fi
}

# Test 4: Verify proper exit codes on failure
test_exit_codes() {
  test_info "Testing that failures use 'fail' function (which exits non-zero)..."
  
  # Check for critical failure points that should use 'fail'
  local critical_checks=(
    "git is required to build liboqs"
    "Failed to install liboqs-python even after building liboqs"
    "Missing required build tools"
  )
  
  local all_found=true
  for check in "${critical_checks[@]}"; do
    if ! grep -q "$check" "$ROOT_DIR/setup.sh"; then
      test_fail "Missing critical error check: $check"
      all_found=false
    fi
  done
  
  if $all_found; then
    test_pass "Script has proper error checks for critical failure points"
  fi
}

# Test 5: Verify environment variables are set
test_env_vars() {
  test_info "Testing that environment variables are properly set..."
  
  local required_vars=(
    "LIBRARY_PATH"
    "PKG_CONFIG_PATH"
    "C_INCLUDE_PATH"
    "CPLUS_INCLUDE_PATH"
  )
  
  for var in "${required_vars[@]}"; do
    if grep -q "export ${var}=" "$ROOT_DIR/setup.sh"; then
      test_pass "Environment variable $var is exported"
    else
      test_fail "Environment variable $var is not exported"
    fi
  done
  
  # Check for platform-specific variables
  if grep -q "DYLD_LIBRARY_PATH" "$ROOT_DIR/setup.sh" && grep -q "LD_LIBRARY_PATH" "$ROOT_DIR/setup.sh"; then
    test_pass "Platform-specific library paths (DYLD/LD) are set"
  else
    test_fail "Missing platform-specific library path variables"
  fi
}

# Test 6: Verify convenience script is created
test_convenience_script() {
  test_info "Testing that convenience script is created..."
  
  if grep -q "cat > \"\$LIBOQS_DIR/env.sh\"" "$ROOT_DIR/setup.sh"; then
    test_pass "Convenience script creation code exists"
  else
    test_fail "Missing convenience script creation"
  fi
  
  if grep -q "chmod +x \"\$LIBOQS_DIR/env.sh\"" "$ROOT_DIR/setup.sh"; then
    test_pass "Convenience script is made executable"
  else
    test_fail "Convenience script is not made executable"
  fi
}

# Test 7: Verify retry uses --no-cache-dir
test_retry_logic() {
  test_info "Testing retry logic uses --no-cache-dir..."
  
  if grep -q "pip install liboqs-python --no-cache-dir" "$ROOT_DIR/setup.sh"; then
    test_pass "Retry uses --no-cache-dir flag"
  else
    test_fail "Retry should use --no-cache-dir flag to avoid cached failures"
  fi
}

# Test 8: Verify logging is informative
test_logging() {
  test_info "Testing that logging is informative..."
  
  # Check for success indicators
  if grep -q "✓" "$ROOT_DIR/setup.sh"; then
    test_pass "Script uses checkmarks for success indicators"
  else
    test_fail "Script should use visual indicators for success"
  fi
  
  # Check for build location logging
  if grep -q "Built liboqs is at:" "$ROOT_DIR/setup.sh"; then
    test_pass "Script logs the build location"
  else
    test_fail "Script should log where liboqs was built"
  fi
}

# Test 9: Verify liboqs version is pinned
test_version_pinning() {
  test_info "Testing that liboqs version is pinned..."
  
  if grep -q "LIBOQS_VERSION=" "$ROOT_DIR/setup.sh"; then
    local version
    # More robust extraction that handles different quote styles
    version=$(grep "LIBOQS_VERSION=" "$ROOT_DIR/setup.sh" | head -1 | sed 's/.*LIBOQS_VERSION=\s*["'\'']*\([^"'\'']*\).*/\1/')
    if [[ -n "$version" ]]; then
      test_pass "liboqs version is pinned to: $version"
    else
      test_fail "Could not extract liboqs version"
    fi
  else
    test_fail "liboqs version should be pinned"
  fi
}

# Test 10: Verify cmake options are correct
test_cmake_options() {
  test_info "Testing cmake configuration options..."
  
  if grep -q "BUILD_SHARED_LIBS=ON" "$ROOT_DIR/setup.sh"; then
    test_pass "Builds shared libraries as required"
  else
    test_fail "Should build shared libraries"
  fi
  
  if grep -q "OQS_USE_OPENSSL=OFF" "$ROOT_DIR/setup.sh"; then
    test_pass "Disables OpenSSL dependency as required"
  else
    test_fail "Should disable OpenSSL dependency"
  fi
}

# Run all tests
main() {
  echo "=========================================="
  echo "Testing setup.sh liboqs installation logic"
  echo "=========================================="
  echo ""
  
  test_syntax
  test_functions
  test_error_messages
  test_exit_codes
  test_env_vars
  test_convenience_script
  test_retry_logic
  test_logging
  test_version_pinning
  test_cmake_options
  
  echo ""
  echo "=========================================="
  echo -e "${GREEN}All tests passed!${RESET}"
  echo "=========================================="
}

main "$@"
