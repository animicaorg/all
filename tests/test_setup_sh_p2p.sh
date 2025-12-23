#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

pass() {
  echo "[PASS] $*"
}

p2p_default=$(bash -c 'set -euo pipefail; source ./setup.sh; echo "$ENABLE_P2P"')
if [ "$p2p_default" != "false" ]; then
  fail "setup.sh should disable P2P autostart by default when docker compose is present"
fi
pass "setup.sh default P2P autostart is disabled when docker compose exists"

p2p_enabled=$(bash -c 'set -euo pipefail; set -- --p2p; source ./setup.sh; echo "$ENABLE_P2P"')
if [ "$p2p_enabled" != "true" ]; then
  fail "setup.sh --p2p should enable P2P autostart"
fi
pass "setup.sh --p2p enables P2P autostart"

p2p_disabled=$(bash -c 'set -euo pipefail; set -- --no-p2p; source ./setup.sh; echo "$ENABLE_P2P"')
if [ "$p2p_disabled" != "false" ]; then
  fail "setup.sh --no-p2p should disable P2P autostart"
fi
pass "setup.sh --no-p2p disables P2P autostart"
