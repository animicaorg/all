#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# testall.sh — Run Animica monorepo tests (unit + e2e) excluding the Flutter wallet
#
# What it covers (auto-skips if a folder is missing):
#   • Python pkgs: aicf, capabilities, consensus, da, execution, mempool,
#                  mining, p2p, pq, proofs, randomness, chains, governance
#   • Rust crate:  native/
#   • Web app:     explorer-web (Vitest unit + Playwright e2e)
#
# Defaults:
#   E2E=1          # run e2e (Playwright) for explorer-web; set NO_E2E=1 to skip
#   FAST=0         # FAST=1 adds -k "not slow" to pytest
#   TIMEOUT=1800   # per test-suite timeout seconds
#   INSTALL=0      # INSTALL=1 will run package installs where sensible
#   NO_RUST=0      # set to 1 to skip Rust tests
#   NO_JS=0        # set to 1 to skip JS/TS tests
#   NO_PY=0        # set to 1 to skip Python tests
#
# Examples:
#   chmod +x testall.sh
#   ./testall.sh
#   FAST=1 ./testall.sh
#   NO_E2E=1 ./testall.sh
#   INSTALL=1 ./testall.sh
# ============================================================================

ROOT="$(pwd)"
TIMEOUT="${TIMEOUT:-1800}"
FAST="${FAST:-0}"
INSTALL="${INSTALL:-0}"

NO_PY="${NO_PY:-0}"
NO_RUST="${NO_RUST:-0}"
NO_JS="${NO_JS:-0}"
NO_E2E="${NO_E2E:-0}"

# Enable e2e by default unless explicitly disabled
if [[ "${E2E:-1}" = "0" ]]; then NO_E2E=1; fi

# Pretty logs
c_gray='\033[90m'; c_red='\033[31m'; c_green='\033[32m'; c_yellow='\033[33m'; c_blue='\033[34m'; c_reset='\033[0m'
log()   { echo -e "${c_blue}[testall]${c_reset} $*"; }
warn()  { echo -e "${c_yellow}[warn]${c_reset} $*"; }
fail()  { echo -e "${c_red}[fail]${c_reset} $*"; }
ok()    { echo -e "${c_green}[ok]${c_reset} $*"; }

PASSED=()
FAILED=()

record_result() {
  local name="$1" code="$2"
  if [[ "$code" -eq 0 ]]; then PASSED+=("$name"); ok "$name"
  else FAILED+=("$name"); fail "$name (exit $code)"
  fi
}

run_cmd() {
  # usage: run_cmd "<suite-name>" timeout_secs cmd...
  local name="$1"; shift
  local to="$1"; shift
  set +e
  timeout "$to" "$@"
  local code=$?
  set -e
  record_result "$name" "$code"
  return "$code"
}

maybe_install_python() {
  # Try lightweight install for each pkg if INSTALL=1 and requirements present
  if [[ "$INSTALL" = "1" ]]; then
    if [[ -f "requirements.txt" ]]; then
      python3 -m pip -q install -r requirements.txt || warn "pip install failed in $(pwd)"
    elif [[ -f "pyproject.toml" ]]; then
      # Best effort: build deps via pip if PEP 621/PEP 517
      python3 -m pip -q install . || warn "pip install . failed in $(pwd)"
    fi
  fi
}

run_py_pkg() {
  local pkg="$1"
  [[ "$NO_PY" = "1" ]] && { warn "Skipping Python ($pkg) by NO_PY=1"; return 0; }
  [[ ! -d "$pkg" ]] && { warn "No $pkg/ dir; skipping"; return 0; }
  [[ ! -d "$pkg/tests" ]] && { warn "$pkg has no tests/; skipping"; return 0; }

  if ! command -v pytest >/dev/null 2>&1; then
    warn "pytest not found; attempting to install (python3 -m pip install -U pytest)"
    python3 -m pip -q install -U pytest || { fail "Could not install pytest"; return 1; }
  fi

  pushd "$pkg" >/dev/null
    maybe_install_python
    local addopts=("-q")
    [[ "$FAST" = "1" ]] && addopts+=(-k "not slow")
    run_cmd "py::$pkg" "$TIMEOUT" python3 -m pytest "${addopts[@]}"
  popd >/dev/null
}

run_rust() {
  [[ "$NO_RUST" = "1" ]] && { warn "Skipping Rust by NO_RUST=1"; return 0; }
  [[ ! -d native ]] && { warn "No native/ dir; skipping"; return 0; }
  if ! command -v cargo >/dev/null 2>&1; then
    warn "cargo not found; skipping Rust tests"
    return 0
  fi
  pushd native >/dev/null
    [[ "$INSTALL" = "1" && -f Cargo.lock ]] && cargo fetch || true
    run_cmd "rust::native" "$TIMEOUT" cargo test --all --locked
  popd >/dev/null
}

detect_pkg_manager() {
  if command -v pnpm >/dev/null 2>&1; then echo "pnpm"
  elif command -v yarn >/dev/null 2>&1; then echo "yarn"
  else echo "npm"
  fi
}

js_install() {
  local mgr="$1"
  if [[ "$INSTALL" != "1" ]]; then return 0; fi
  case "$mgr" in
    pnpm) pnpm install --frozen-lockfile || pnpm install ;;
    yarn) yarn install --frozen-lockfile || yarn install ;;
    npm)  npm ci || npm install ;;
  esac
}

run_explorer_web_unit() {
  [[ "$NO_JS" = "1" ]] && { warn "Skipping JS/TS by NO_JS=1"; return 0; }
  [[ ! -d explorer-web ]] && { warn "No explorer-web/ dir; skipping"; return 0; }

  pushd explorer-web >/dev/null
    local mgr; mgr="$(detect_pkg_manager)"
    js_install "$mgr"
    case "$mgr" in
      pnpm) run_cmd "js::explorer-web::unit" "$TIMEOUT" pnpm test ;;
      yarn) run_cmd "js::explorer-web::unit" "$TIMEOUT" yarn test ;;
      npm)  run_cmd "js::explorer-web::unit" "$TIMEOUT" npm test ;;
    esac
  popd >/dev/null
}

run_explorer_web_e2e() {
  [[ "$NO_JS" = "1" ]] && { warn "Skipping JS/TS by NO_JS=1"; return 0; }
  [[ "$NO_E2E" = "1" ]] && { warn "Skipping e2e by NO_E2E=1"; return 0; }
  [[ ! -d explorer-web/test/e2e ]] && { warn "No explorer-web/test/e2e; skipping"; return 0; }

  pushd explorer-web >/dev/null
    local mgr; mgr="$(detect_pkg_manager)"
    # Ensure Playwright browsers present
    case "$mgr" in
      pnpm)
        pnpm exec playwright install --with-deps || true
        run_cmd "e2e::explorer-web" "$TIMEOUT" pnpm exec playwright test
        ;;
      yarn)
        npx playwright install --with-deps || true
        run_cmd "e2e::explorer-web" "$TIMEOUT" npx playwright test
        ;;
      npm)
        npx playwright install --with-deps || true
        run_cmd "e2e::explorer-web" "$TIMEOUT" npx playwright test
        ;;
    esac
  popd >/dev/null
}

# ---------------- Main ----------------

log "Animica test runner — starting in $ROOT"
log "FAST=$FAST  INSTALL=$INSTALL  TIMEOUT=$TIMEOUT  NO_PY=$NO_PY  NO_RUST=$NO_RUST  NO_JS=$NO_JS  NO_E2E=$NO_E2E"

# 1) Python test suites (per-package)
PY_PKGS=(aicf capabilities consensus da execution mempool mining p2p pq proofs randomness chains governance)
for pkg in "${PY_PKGS[@]}"; do
  run_py_pkg "$pkg"
done

# 2) Rust crate(s)
run_rust

# 3) Web app tests (unit + e2e)
run_explorer_web_unit
run_explorer_web_e2e

# 4) Summary
echo
log "====================== SUMMARY ======================"
echo -e "${c_green}PASSED (${#PASSED[@]}):${c_reset} ${PASSED[*]:-<none>}"
if [[ "${#FAILED[@]}" -gt 0 ]]; then
  echo -e "${c_red}FAILED (${#FAILED[@]}):${c_reset} ${FAILED[*]}"
  exit 1
else
  ok "All selected test suites passed."
fi
