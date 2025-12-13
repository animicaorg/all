#!/usr/bin/env bash
# Lightweight guardrail for setup.sh rebuild behavior
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck disable=SC1091
source "$ROOT_DIR/ops/liboqs.sh"

ok() { echo "[OK] $*"; }
fail() { echo "[FAIL] $*"; exit 1; }

if ! grep -q "--clean" "$ROOT_DIR/setup.sh"; then
  fail "setup.sh should document --clean flag"
fi

if ! grep -q "LD_LIBRARY_PATH=\$prefix/lib" "$ROOT_DIR/setup.sh"; then
  fail "setup.sh should export LD_LIBRARY_PATH to vendored liboqs"
fi

if ! grep -q "\.deps/liboqs/${LIBOQS_VERSION}" "$ROOT_DIR/setup.sh"; then
  fail "setup.sh should target .deps/liboqs/${LIBOQS_VERSION}"
fi

ok "setup.sh references clean flag, vendored liboqs path, and LD_LIBRARY_PATH"
