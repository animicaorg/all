#!/usr/bin/env bash
# test.sh — Monorepo test orchestrator (Python | TypeScript/Node | Rust), with optional E2E/coverage.
#
# Usage:
#   ./templates/_common/scripts/test.sh [options]
#
# Common options:
#   --fast                    Run a quick suite (default): unit + selected module tests, no E2E.
#   --full                    Run everything we can find: unit + integration + E2E (if tooling present).
#   --python/--no-python      Enable/disable Python tests (default: on).
#   --ts/--no-ts              Enable/disable TypeScript/Node tests (default: on).
#   --rust/--no-rust          Enable/disable Rust tests (default: on).
#   --unit/--no-unit          Include/exclude unit tests (default: on).
#   --integration/--no-integration
#                            Include/exclude integration tests (default: off in --fast, on in --full).
#   --e2e/--no-e2e            Include/exclude browser/devnet E2E (default: off in --fast, on in --full).
#   --coverage                Collect coverage where supported (pytest/vitest). (default: off)
#   --workers N               Parallel workers for pytest/vitest (default: auto where supported).
#   --report-dir DIR          Output directory for reports (junit/coverage/html). (default: tests/reports)
#   --ci                      CI mode: prefer --check-like behaviors, headless, strict exits.
#   --only DIR[,DIR...]       Limit runs to comma-separated subpaths (globs ok) for speed.
#   --pytest-args '...'       Extra args forwarded to pytest.
#   --vitest-args '...'       Extra args forwarded to vitest/npm test.
#   --cargo-args  '...'       Extra args forwarded to cargo test.
#   -h|--help                 Show this help.
#
# Conventions assumed by default (auto-skipped if missing):
#   Python:   core/ rpc/ consensus/ proofs/ mining/ p2p/ mempool/ da/ execution/ vm_py/
#             capabilities/ aicf/ randomness/ studio-services/ sdk/python/ contracts/
#             plus repo-root tests/{unit,property,integration,e2e,...} if present
#   TypeScript/Node: wallet-extension/ studio-wasm/ studio-web/ sdk/typescript/
#   Rust:     sdk/rust/
#
# Exit code is non-zero if any enabled suite fails. Skips are not failures.

set -euo pipefail
IFS=$'\n\t'

# -------- colors & log helpers ------------------------------------------------
if [[ -t 1 ]]; then
  bold=$'\e[1m'; dim=$'\e[2m'; red=$'\e[31m'; green=$'\e[32m'; yellow=$'\e[33m'; blue=$'\e[34m'; reset=$'\e[0m'
else
  bold=''; dim=''; red=''; green=''; yellow=''; blue=''; reset=''
fi
ts() { date +'%H:%M:%S'; }
log()  { printf '%s\n' "${dim}[$(ts)]${reset} $*"; }
info() { printf '%s\n' "${blue}ℹ${reset}  $*"; }
ok()   { printf '%s\n' "${green}✔${reset}  $*"; }
warn() { printf '%s\n' "${yellow}⚠${reset}  $*"; }
err()  { printf '%s\n' "${red}✖${reset}  $*" >&2; }

# -------- util ----------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }
json_has_script() { # usage: json_has_script DIR scriptName
  local dir="$1" name="$2"
  [[ -f "$dir/package.json" ]] || return 1
  grep -q "\"$name\"[[:space:]]*:" "$dir/package.json"
}
pm_detect() {
  if have pnpm; then echo pnpm
  elif have yarn; then echo yarn
  else echo npm
  fi
}
run() { # prints and runs, but does not exit on failure; returns exit code
  printf '%s\n' "${dim}\$ $*${reset}"
  set +e
  "$@"; local rc=$?
  set -e
  return "$rc"
}
ensure_dir() { mkdir -p "$1"; }

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

# -------- defaults ------------------------------------------------------------
MODE="fast"              # fast|full
RUN_PY=1
RUN_TS=1
RUN_RS=1
DO_UNIT=1
DO_INT=0
DO_E2E=0
COVERAGE=0
WORKERS="auto"
REPORT_DIR="tests/reports"
ONLY_PATHS=""
CI_MODE=0
PYTEST_ARGS=""
VITEST_ARGS=""
CARGO_ARGS=""

# -------- args ----------------------------------------------------------------
while (( "$#" )); do
  case "$1" in
    --fast) MODE="fast"; DO_INT=0; DO_E2E=0; shift ;;
    --full) MODE="full"; DO_INT=1; DO_E2E=1; shift ;;
    --python) RUN_PY=1; shift ;;
    --no-python) RUN_PY=0; shift ;;
    --ts) RUN_TS=1; shift ;;
    --no-ts) RUN_TS=0; shift ;;
    --rust) RUN_RS=1; shift ;;
    --no-rust) RUN_RS=0; shift ;;
    --unit) DO_UNIT=1; shift ;;
    --no-unit) DO_UNIT=0; shift ;;
    --integration) DO_INT=1; shift ;;
    --no-integration) DO_INT=0; shift ;;
    --e2e) DO_E2E=1; shift ;;
    --no-e2e) DO_E2E=0; shift ;;
    --coverage) COVERAGE=1; shift ;;
    --workers) WORKERS="${2:-auto}"; shift 2 ;;
    --report-dir) REPORT_DIR="${2:-tests/reports}"; shift 2 ;;
    --ci) CI_MODE=1; shift ;;
    --only) ONLY_PATHS="${2:-}"; shift 2 ;;
    --pytest-args) PYTEST_ARGS="${2:-}"; shift 2 ;;
    --vitest-args) VITEST_ARGS="${2:-}"; shift 2 ;;
    --cargo-args) CARGO_ARGS="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '1,120p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) err "Unknown arg: $1"; exit 2;;
  esac
done

export CI=${CI_MODE}
ensure_dir "$REPORT_DIR"

# Restrict to ONLY_PATHS if set
path_in_scope() {
  [[ -z "$ONLY_PATHS" ]] && return 0
  local IFS=','; local p
  for p in $ONLY_PATHS; do
    if [[ "$1" == "$p"* ]]; then return 0; fi
  done
  return 1
}

# -------- Python tests --------------------------------------------------------
PY_MODULES=( core rpc consensus proofs mining p2p mempool da execution vm_py capabilities aicf randomness studio-services sdk/python contracts )
PY_ENV_VARS=(
  "PYTHONUNBUFFERED=1"
  "PYTHONDONTWRITEBYTECODE=1"
  "PYTHONPATH=$ROOT"
)

pytest_bin() {
  if have pytest; then echo pytest; else echo "python3 -m pytest"; fi
}
pytest_workers_args=()
if have pytest && python3 - <<'PY' >/dev/null 2>&1
import importlib, sys
sys.exit(0 if importlib.util.find_spec("xdist") else 1)
PY
then
  if [[ "$WORKERS" == "auto" ]]; then
    pytest_workers_args=(-n auto)
  else
    pytest_workers_args=(-n "$WORKERS")
  fi
fi

run_python_unit() {
  local outdir="$REPORT_DIR/python"
  ensure_dir "$outdir"
  local junit="$outdir/junit-unit.xml"
  local cov_args=()
  if (( COVERAGE )); then
    if have coverage; then
      cov_args=(--cov=. --cov-report=xml:"$outdir/coverage-unit.xml" --cov-report=term-missing)
    else
      warn "coverage.py not installed; skipping Python coverage."
    fi
  fi

  # Collect per-module test dirs that exist
  local targets=()
  for m in "${PY_MODULES[@]}"; do
    if path_in_scope "$m" && [[ -d "$m" ]]; then
      if [[ -d "$m/tests" ]]; then targets+=("$m/tests"); fi
    fi
  done
  # Add repo-root unit-like tests if present
  if path_in_scope "tests" && [[ -d tests ]]; then
    # Exclude e2e/integration/fuzz/bench/load by targeting specific dirs
    [[ -d tests/property ]] && targets+=("tests/property")
    [[ -d tests/bench ]]    && targets+=("tests/bench")  # unit-style benches sometimes have asserts
    [[ -d tests ]] && mapfile -t _loose < <(find tests -maxdepth 1 -type f -name "test_*.py" 2>/dev/null || true)
    targets+=("${_loose[@]}")
  fi

  if [[ ${#targets[@]} -eq 0 ]]; then
    info "Python unit: nothing to run (no test paths in scope)."
    return 0
  fi

  info "Python unit: running on ${#targets[@]} path(s)"
  ( export "${PY_ENV_VARS[@]}";
    run $(pytest_bin) -q "${pytest_workers_args[@]}" \
      --junitxml="$junit" \
      -k "not integration and not e2e" \
      "${cov_args[@]}" \
      ${PYTEST_ARGS} \
      "${targets[@]}"
  )
  return $?
}

run_python_integration() {
  local outdir="$REPORT_DIR/python"
  ensure_dir "$outdir"
  local junit="$outdir/junit-integration.xml"
  local cov_args=()
  if (( COVERAGE )) && have coverage; then
    cov_args=(--cov=. --cov-append --cov-report=xml:"$outdir/coverage-integration.xml" --cov-report=term-missing)
  fi
  local targets=()
  if path_in_scope "tests/integration" && [[ -d tests/integration ]]; then targets+=("tests/integration"); fi
  # Some modules might ship their own "integration" tests
  mapfile -t _modints < <(for m in "${PY_MODULES[@]}"; do
    [[ -d "$m/tests" ]] && find "$m/tests" -maxdepth 1 -type f -name "test_*integration*.py" 2>/dev/null
  done)
  targets+=("${_modints[@]}")
  if [[ ${#targets[@]} -eq 0 ]]; then
    info "Python integration: nothing to run."
    return 0
  fi
  info "Python integration: running on ${#targets[@]} path(s)"
  ( export "${PY_ENV_VARS[@]}";
    run $(pytest_bin) -q "${pytest_workers_args[@]}" \
      --junitxml="$junit" \
      ${PYTEST_ARGS} \
      "${cov_args[@]}" \
      "${targets[@]}"
  )
  return $?
}

run_python_e2e() {
  local outdir="$REPORT_DIR/python"
  ensure_dir "$outdir"
  local junit="$outdir/junit-e2e.xml"
  local targets=()
  if path_in_scope "tests/e2e" && [[ -d tests/e2e ]]; then targets+=("tests/e2e"); fi
  if [[ ${#targets[@]} -eq 0 ]]; then
    info "Python E2E: nothing to run."
    return 0
  fi
  local headless_args=()
  (( CI_MODE )) && headless_args=(--headless 1)
  info "Python E2E: running ${#targets[@]} path(s)"
  ( export "${PY_ENV_VARS[@]}";
    run $(pytest_bin) -q \
      --junitxml="$junit" \
      ${PYTEST_ARGS} \
      "${targets[@]}"
  )
  return $?
}

# -------- TypeScript/Node tests ----------------------------------------------
JS_PROJECTS=( wallet-extension studio-wasm studio-web sdk/typescript )
NODE_PM="$(pm_detect)"

vitest_ci_args=()
if [[ "$WORKERS" != "auto" ]]; then vitest_ci_args+=(--threads "$WORKERS"); fi
(( CI_MODE )) && vitest_ci_args+=(--run)

run_ts_unit() {
  local outdir="$REPORT_DIR/ts"
  ensure_dir "$outdir"

  local failures=0 ran=0
  for proj in "${JS_PROJECTS[@]}"; do
    [[ -d "$proj" ]] || continue
    path_in_scope "$proj" || continue
    pushd "$proj" >/dev/null
      if json_has_script "." "test"; then
        info "TS unit: $proj — running \"$NODE_PM test\""
        if (( COVERAGE )) && json_has_script "." "coverage"; then
          run $NODE_PM run coverage ${VITEST_ARGS} || failures=$((failures+1))
          ran=$((ran+1))
        else
          run $NODE_PM test -- ${VITEST_ARGS} ${vitest_ci_args[@]} || failures=$((failures+1))
          ran=$((ran+1))
        fi
      elif have npx && [[ -f vite.config.ts || -f vitest.config.ts ]]; then
        info "TS unit: $proj — running vitest via npx"
        run npx --yes --no-install vitest run ${VITEST_ARGS} ${vitest_ci_args[@]} || failures=$((failures+1))
        ran=$((ran+1))
      else
        warn "TS unit: $proj — no test script/config; skipping."
      fi
    popd >/dev/null
  done

  [[ $ran -eq 0 ]] && info "TS unit: nothing to run."
  return "$failures"
}

run_ts_e2e() {
  local failures=0 ran=0
  # Wallet extension & studio-web usually carry playwright e2e tests
  for proj in wallet-extension studio-web; do
    [[ -d "$proj" ]] || continue
    path_in_scope "$proj" || continue
    pushd "$proj" >/dev/null
      if json_has_script "." "test:e2e"; then
        info "TS E2E: $proj — \"$NODE_PM run test:e2e\" (headless=${CI_MODE})"
        # Many repos infer headless from CI env; ensure set.
        export CI=${CI_MODE}
        run $NODE_PM run test:e2e ${VITEST_ARGS} || failures=$((failures+1))
        ran=$((ran+1))
      else
        warn "TS E2E: $proj — no test:e2e script; skipping."
      fi
    popd >/dev/null
  done
  [[ $ran -eq 0 ]] && info "TS E2E: nothing to run."
  return "$failures"
}

# -------- Rust tests ----------------------------------------------------------
run_rust() {
  local dir="sdk/rust"
  path_in_scope "$dir" || { info "Rust: out of scope."; return 0; }
  [[ -d "$dir" ]] || { info "Rust: $dir not found; skipping."; return 0; }
  pushd "$dir" >/dev/null
    if have cargo; then
      info "Rust: cargo test ${CARGO_ARGS}"
      run cargo test ${CARGO_ARGS}
      local rc=$?
      popd >/dev/null
      return $rc
    else
      warn "Rust: cargo not available; skipping."
      popd >/dev/null
      return 0
    fi
}

# -------- run plan ------------------------------------------------------------
overall=0

log "${bold}Test plan${reset}: mode=${MODE} python=$RUN_PY ts=$RUN_TS rust=$RUN_RS unit=$DO_UNIT integration=$DO_INT e2e=$DO_E2E coverage=$COVERAGE workers=$WORKERS report_dir=$REPORT_DIR"

# Python
if (( RUN_PY )); then
  if (( DO_UNIT ));        then run_python_unit     || overall=1; fi
  if (( DO_INT ));         then run_python_integration || overall=1; fi
  if (( DO_E2E ));         then run_python_e2e      || overall=1; fi
else
  info "Skipping Python suite."
fi

# TS/Node
if (( RUN_TS )); then
  if (( DO_UNIT ));        then run_ts_unit         || overall=1; fi
  if (( DO_E2E ));         then run_ts_e2e          || overall=1; fi
else
  info "Skipping TypeScript/Node suite."
fi

# Rust
if (( RUN_RS )); then
  run_rust || overall=1
else
  info "Skipping Rust suite."
fi

# Summary
if (( overall == 0 )); then
  ok "All enabled test suites passed."
else
  err "One or more test suites failed."
fi

exit "$overall"
