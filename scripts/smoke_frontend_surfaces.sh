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

run_step "explorer-web unit sync smoke" npm --prefix explorer-web test -- test/unit/sync.test.ts
run_step "studio-web provider smoke" npm --prefix studio-web test -- test/unit/provider.test.ts

if (( failures > 0 )); then
  echo "Frontend smoke failed: ${failures} step(s) failed."
  exit 1
fi

echo "Frontend smoke passed."
