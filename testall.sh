#!/usr/bin/env bash
set -euo pipefail

# Animica monorepo test orchestrator
# Modes:
#   ./testall.sh            -> run all suites
#   ./testall.sh <target>   -> run a specific suite (core|consensus|mempool|da|vm_py|sdk)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
  local pkgs=(pytest pytest-asyncio pyyaml)
  if ! command -v pytest >/dev/null 2>&1; then
    warn "pytest not found; attempting install via python3 -m pip install -U ${pkgs[*]}"
    python3 -m pip install -U "${pkgs[@]}" || fail "Unable to install pytest"
  fi
}

npm_runner() {
  if command -v pnpm >/dev/null 2>&1; then echo pnpm; return; fi
  if command -v npm >/dev/null 2>&1; then echo npm; return; fi
  warn "No Node package manager (pnpm/npm) found"; echo ""; return 1
}

run_pytest_target() {
  local label="$1" target="$2"
  activate_venv
  ensure_pytest
  if [[ -d "$ROOT_DIR/$target" || -f "$ROOT_DIR/$target" ]]; then
    log "[$label] pytest $target"
    (cd "$ROOT_DIR" && pytest "$target")
  else
    warn "[$label] $target not present; skipping"
  fi
}

run_core_tests()       { run_pytest_target "Core" core; }
run_consensus_tests()  { run_pytest_target "Consensus" consensus; }
run_mempool_tests()    { run_pytest_target "Mempool" mempool; }
run_da_tests()         { run_pytest_target "DA" da; }
run_vm_py_tests()      { run_pytest_target "VM-Py" vm_py/tests; }
run_sdk_python_tests() { run_pytest_target "SDK (Python)" sdk/python/tests; }

run_sdk_typescript_tests() {
  log "[SDK] TypeScript tests"
  if [[ ! -d "$ROOT_DIR/sdk" ]]; then
    warn "[SDK] sdk directory missing; skipping TypeScript tests"
    return
  fi
  local mgr; mgr=$(npm_runner) || return
  (cd "$ROOT_DIR/sdk" && $mgr install && $mgr test)
}

run_sdk_tests() {
  run_sdk_python_tests
  run_sdk_typescript_tests
}

run_all() {
  run_core_tests
  run_consensus_tests
  run_mempool_tests
  run_da_tests
  run_vm_py_tests
  run_sdk_tests
}

usage() {
  cat <<USAGE
Usage: $0 [all|core|consensus|mempool|da|vm_py|sdk]
Run Animica test suites. Defaults to "all" if no argument provided.
USAGE
}

cmd="${1:-all}"
case "$cmd" in
  core) run_core_tests ;;
  consensus) run_consensus_tests ;;
  mempool) run_mempool_tests ;;
  da) run_da_tests ;;
  vm_py) run_vm_py_tests ;;
  sdk) run_sdk_tests ;;
  all) run_all ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
