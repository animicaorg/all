#!/usr/bin/env bash
# Sanity checks for setup.sh liboqs workflow
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[32m'
RED='\033[31m'
RESET='\033[0m'

pass() { echo -e "${GREEN}PASS${RESET}: $*"; }
fail() { echo -e "${RED}FAIL${RESET}: $*"; exit 1; }

check_syntax() {
  bash -n "$ROOT_DIR/setup.sh" || fail "setup.sh has syntax errors"
  pass "bash -n succeeded"
}

check_version_source() {
  if [[ ! -f "$ROOT_DIR/ops/liboqs.sh" ]]; then
    fail "Missing ops/liboqs.sh"
  fi
  local version
  version=$(grep -E "^LIBOQS_VERSION=\"" "$ROOT_DIR/ops/liboqs.sh" | head -1 | cut -d'"' -f2)
  [[ "$version" == "0.14.0" ]] || fail "Expected LIBOQS_VERSION 0.14.0, found $version"
  pass "LIBOQS_VERSION is pinned to $version"
}

check_setup_content() {
  grep -q "ops/liboqs.sh" "$ROOT_DIR/setup.sh" || fail "setup.sh should source ops/liboqs.sh"
  grep -q "\.deps/liboqs" "$ROOT_DIR/setup.sh" || fail "setup.sh should build into .deps/liboqs"
  grep -q "oqs==\${LIBOQS_VERSION}" "$ROOT_DIR/setup.sh" || fail "setup.sh should pin oqs install"
  grep -q "shim_path=\"\$shim_dir/animica\"" "$ROOT_DIR/setup.sh" || fail "setup.sh should install shim"
  pass "setup.sh content references liboqs helpers, pinned oqs, and shim"
}

main() {
  echo "Running setup.sh static checks"
  check_syntax
  check_version_source
  check_setup_content
  echo "All static checks passed"
}

main "$@"
