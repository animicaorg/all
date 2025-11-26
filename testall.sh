#!/usr/bin/env bash
set -euo pipefail

# Animica monorepo test orchestrator
# Modes:
#   ./testall.sh        -> run unit + e2e/integration tests
#   ./testall.sh unit   -> unit tests only
#   ./testall.sh e2e    -> e2e/integration flows only

ROOT_DIR="$(pwd)"

c_blue='\033[34m'; c_green='\033[32m'; c_yellow='\033[33m'; c_red='\033[31m'; c_reset='\033[0m'
log()   { echo -e "${c_blue}[testall]${c_reset} $*"; }
warn()  { echo -e "${c_yellow}[warn]${c_reset} $*"; }
fail()  { echo -e "${c_red}[fail]${c_reset} $*"; }

activate_venv() {
  if [[ -d "$ROOT_DIR/.venv" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.venv/bin/activate" || warn "Could not activate .venv"
  fi
}

ensure_pytest() {
  if ! command -v pytest >/dev/null 2>&1; then
    warn "pytest not found; attempting install via python3 -m pip install -U pytest"
    python3 -m pip install -U pytest || fail "Unable to install pytest"
  fi
}

ensure_node_pkg() {
  local dir="$1"
  if [[ ! -d "$dir" ]]; then
    warn "$dir missing; skipping"
    return 1
  fi
  return 0
}

run_py_unit() {
  log "[PY] Running unit tests"
  activate_venv
  ensure_pytest
  local targets=(core execution consensus mempool p2p da randomness aicf mining pq governance templates python/animica)
  local existing=()
  for t in "${targets[@]}"; do
    [[ -d "$ROOT_DIR/$t" ]] && existing+=("$t") || warn "[PY] $t not present; skipping"
  done
  if ((${#existing[@]}==0)); then
    warn "[PY] no python targets found"
    return
  fi
  (cd "$ROOT_DIR" && pytest "${existing[@]}")
}

run_py_e2e() {
  log "[PY] Running integration/e2e tests"
  activate_venv
  ensure_pytest
  local e2e_paths=(
    "p2p/tests/test_end_to_end_two_nodes.py"
    "da/tests/test_integration_post_get_verify.py"
    "python/animica/da/tests/test_da_api_contract.py"
    "mining/tests/test_orchestrator_single_node.py"
    "randomness/tests/test_rpc_cli_roundtrip.py"
    "aicf/tests/test_integration_proof_to_payout.py"
    "aicf/tests/test_cli_provider_flow.py"
  )
  local existing=()
  for p in "${e2e_paths[@]}"; do
    [[ -f "$ROOT_DIR/$p" ]] && existing+=("$p") || warn "[PY] $p not present; skipping"
  done
  if ((${#existing[@]}==0)); then
    warn "[PY] no e2e test files found"
    return
  fi
  (cd "$ROOT_DIR" && pytest "${existing[@]}")
}

npm_runner() {
  if command -v pnpm >/dev/null 2>&1; then echo pnpm; return; fi
  if command -v npm >/dev/null 2>&1; then echo npm; return; fi
  warn "No Node package manager (pnpm/npm) found"; echo ""; return 1
}

run_sdk_py_e2e() {
  log "[SDK] Python e2e harness"
  activate_venv
  if [[ -f "$ROOT_DIR/sdk/test-harness/run_e2e_py.py" ]]; then
    (cd "$ROOT_DIR" && python sdk/test-harness/run_e2e_py.py)
  else
    warn "[SDK] run_e2e_py.py not found; skipping"
  fi
}

run_sdk_ts_unit() {
  log "[SDK] TypeScript unit tests"
  ensure_node_pkg "$ROOT_DIR/sdk/typescript" || return
  local mgr; mgr=$(npm_runner) || return
  (cd "$ROOT_DIR/sdk/typescript" && $mgr install && $mgr test)
}

run_sdk_ts_e2e() {
  log "[SDK] TypeScript e2e harness"
  ensure_node_pkg "$ROOT_DIR/sdk" || return
  local mgr; mgr=$(npm_runner) || return
  if [[ -f "$ROOT_DIR/sdk/test-harness/run_e2e_ts.mjs" ]]; then
    (cd "$ROOT_DIR/sdk" && $mgr install && node test-harness/run_e2e_ts.mjs)
  else
    warn "[SDK] run_e2e_ts.mjs not found; skipping"
  fi
}

run_sdk_rs_tests() {
  log "[SDK] Rust tests and e2e"
  if ! command -v cargo >/dev/null 2>&1; then
    warn "cargo not found; skipping Rust"
    return
  fi
  if [[ -d "$ROOT_DIR/sdk/rust" ]]; then
    (cd "$ROOT_DIR/sdk/rust" && cargo test)
  else
    warn "sdk/rust missing; skipping cargo tests"
  fi
  if [[ -x "$ROOT_DIR/sdk/test-harness/run_e2e_rs.sh" ]]; then
    (cd "$ROOT_DIR/sdk/test-harness" && ./run_e2e_rs.sh)
  else
    warn "run_e2e_rs.sh not found; skipping"
  fi
}

run_wallet_extension_unit() {
  log "[Wallet-Ext] Unit tests"
  ensure_node_pkg "$ROOT_DIR/wallet-extension" || return
  local mgr; mgr=$(npm_runner) || return
  (cd "$ROOT_DIR/wallet-extension" && $mgr install && $mgr test)
}

run_wallet_extension_e2e() {
  log "[Wallet-Ext] E2E tests"
  ensure_node_pkg "$ROOT_DIR/wallet-extension" || return
  local mgr; mgr=$(npm_runner) || return
  if [[ -f "$ROOT_DIR/wallet-extension/playwright.config.ts" ]]; then
    (cd "$ROOT_DIR/wallet-extension" && $mgr install && $mgr exec playwright install --with-deps || true && $mgr exec playwright test)
  else
    warn "playwright.config.ts missing; skipping wallet-extension e2e"
  fi
}

run_studio_wasm_unit() {
  log "[Studio-WASM] Unit tests"
  ensure_node_pkg "$ROOT_DIR/studio-wasm" || return
  local mgr; mgr=$(npm_runner) || return
  (cd "$ROOT_DIR/studio-wasm" && $mgr install && $mgr test)
}

run_studio_wasm_e2e() {
  log "[Studio-WASM] E2E tests"
  ensure_node_pkg "$ROOT_DIR/studio-wasm" || return
  local mgr; mgr=$(npm_runner) || return
  if [[ -f "$ROOT_DIR/studio-wasm/playwright.config.ts" ]]; then
    (cd "$ROOT_DIR/studio-wasm" && $mgr install && $mgr exec playwright install --with-deps || true && $mgr exec playwright test)
  else
    warn "playwright.config.ts missing; skipping studio-wasm e2e"
  fi
}

run_wallet_checks() {
  log "[Wallet] Flutter checks"
  if ! command -v flutter >/dev/null 2>&1; then
    warn "Flutter not installed; skipping wallet checks"
    return
  fi
  if [[ -d "$ROOT_DIR/wallet" ]]; then
    (cd "$ROOT_DIR/wallet" && flutter test)
  else
    warn "wallet directory missing; skipping"
  fi
}

cmd="${1:-all}"
case "$cmd" in
  unit)
    run_py_unit
    run_sdk_ts_unit
    run_sdk_rs_tests
    run_wallet_extension_unit
    run_studio_wasm_unit
    ;;
  e2e)
    run_py_e2e
    run_sdk_py_e2e
    run_sdk_ts_e2e
    run_wallet_extension_e2e
    run_studio_wasm_e2e
    ;;
  all|*)
    run_py_unit
    run_sdk_ts_unit
    run_sdk_rs_tests
    run_wallet_extension_unit
    run_studio_wasm_unit
    run_wallet_checks
    run_py_e2e
    run_sdk_py_e2e
    run_sdk_ts_e2e
    run_wallet_extension_e2e
    run_studio_wasm_e2e
    ;;
 esac
