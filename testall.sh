#!/usr/bin/env bash
# Animica "test everything" script.
# Save as ./testall in the repo root and chmod +x it.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

FAILED_STEPS=()
SKIPPED_STEPS=()

log() {
  printf '%s\n' "$*" >&2
}

hr() {
  printf '%s\n' "----------------------------------------------------------------------" >&2
}

run_step() {
  local name="$1"
  shift

  hr
  log ">> $name"
  hr

  if "$@"; then
    log "[PASS] $name"
  else
    log "[FAIL] $name"
    FAILED_STEPS+=("$name")
  fi
}

skip_step() {
  local name="$1"
  SKIPPED_STEPS+=("$name")
}

log "Animica testall: full test suite"
log "Repo root: $SCRIPT_DIR"
log

# --- Python env ----------------------------------------------------------------

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  log "ERROR: python is not installed or not on PATH."
  exit 1
fi

log "Using Python: $($PYTHON_BIN --version 2>&1)"

if [ -d ".venv" ]; then
  log "Activating virtualenv: .venv"
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
else
  log "No .venv found; assuming global Python environment is already prepared."
fi

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
export PIP_DISABLE_PIP_VERSION_CHECK=1

# --- Tool versions (best effort) ----------------------------------------------

if command -v pytest >/dev/null 2>&1; then
  log "pytest: $(pytest --version 2>&1 | head -n1)"
else
  log "WARNING: pytest not found; Python tests will fail."
fi

if command -v pnpm >/dev/null 2>&1; then
  log "pnpm: $(pnpm -v)"
fi

if command -v node >/dev/null 2>&1; then
  log "node: $(node -v)"
fi

if command -v cargo >/dev/null 2>&1; then
  log "cargo: $(cargo --version 2>&1)"
fi

if command -v ruff >/dev/null 2>&1; then
  log "ruff: $(ruff --version 2>&1)"
fi

if command -v pre-commit >/dev/null 2>&1; then
  log "pre-commit: $(pre-commit --version 2>&1)"
fi

log

# --- Python: tests -------------------------------------------------------------

run_step "Python: pytest (all discovered tests)" \
  pytest

# --- Rust: native crates -------------------------------------------------------

if command -v cargo >/dev/null 2>&1; then
  if [ -f "native/Cargo.toml" ]; then
    run_step "Rust: native crate tests (native/)" \
      bash -c 'cd native && cargo test --all --all-features'
  else
    skip_step "Rust: native crate tests (native/ not present)"
  fi

  if [ -f "crates/animica-native/Cargo.toml" ]; then
    run_step "Rust: animica-native crate tests (crates/animica-native/)" \
      bash -c 'cd crates/animica-native && cargo test --all --all-features'
  else
    skip_step "Rust: animica-native crate tests (crates/animica-native/ not present)"
  fi
else
  skip_step "Rust tests (cargo not installed)"
fi

# --- Node / TypeScript: pnpm workspace tests ----------------------------------

if command -v pnpm >/dev/null 2>&1; then
  if [ -f "pnpm-workspace.yaml" ]; then
    run_step "Node: pnpm recursive test (all workspaces with test script)" \
      pnpm -r --if-present test

    run_step "Node: pnpm recursive lint (all workspaces with lint script)" \
      pnpm -r --if-present lint
  else
    skip_step "Node workspace tests (pnpm-workspace.yaml not found)"
  fi
else
  skip_step "Node workspace tests (pnpm not installed)"
fi

# --- Optional linters: ruff + pre-commit --------------------------------------

if [[ "${ANIMICA_TESTALL_NO_LINT:-0}" = "1" ]]; then
  skip_step "Python lint (ANIMICA_TESTALL_NO_LINT=1)"
  skip_step "pre-commit (ANIMICA_TESTALL_NO_LINT=1)"
else
  if command -v ruff >/dev/null 2>&1; then
    run_step "Python: ruff lint (entire repo)" \
      ruff check .
  else
    skip_step "Python: ruff lint (ruff not installed)"
  fi

  if command -v pre-commit >/dev/null 2>&1 && [ -f ".pre-commit-config.yaml" ]; then
    run_step "pre-commit: run all hooks on all files" \
      pre-commit run --all-files
  else
    skip_step "pre-commit hooks (pre-commit not installed or no .pre-commit-config.yaml)"
  fi
fi

# --- Summary -------------------------------------------------------------------

hr
if [ "${#FAILED_STEPS[@]}" -eq 0 ]; then
  log "✅ Animica testall: ALL STEPS PASSED"
else
  log "❌ Animica testall: SOME STEPS FAILED"
  log "Failed steps:"
  for s in "${FAILED_STEPS[@]}"; do
    log "  - $s"
  done
fi

if [ "${#SKIPPED_STEPS[@]}" -ne 0 ]; then
  log
  log "Skipped steps:"
  for s in "${SKIPPED_STEPS[@]}"; do
    log "  - $s"
  done
fi

if [ "${#FAILED_STEPS[@]}" -eq 0 ]; then
  exit 0
else
  exit 1
fi
