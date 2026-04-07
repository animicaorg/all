#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

failures=0

run_step() {
  local name="$1"
  shift
  echo "== ${name} =="
  if "$@"; then
    echo "PASS: ${name}"
  else
    local rc=$?
    echo "FAIL(${rc}): ${name}"
    failures=$((failures + 1))
  fi
  echo
}

run_step "apps/admin-web type-check" npm --prefix apps/admin-web run type-check
run_step "cex e2e harness build" npm --prefix cex/tests/e2e run build

if (( failures > 0 )); then
  echo "Exchange smoke failed: ${failures} step(s) failed."
  exit 1
fi

echo "Exchange smoke passed."
