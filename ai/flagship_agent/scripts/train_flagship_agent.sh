#!/usr/bin/env bash
# One-command driver for the flagship-agent training pipeline.
#
# Reads ai/configs/pipeline.yaml::stages and runs each stage in order,
# obeying:
#   FLAGSHIP_MODE          simulate | lite | full   (default: simulate)
#   FLAGSHIP_RESUME        latest | <run_id>        (resume an existing run)
#   FLAGSHIP_STAGES        comma-separated subset   (run a specific subset)
#   FLAGSHIP_ALLOW_CPU_REAL=1                       (gate for CPU full mode)
#   FLAGSHIP_TIERS=tiny,small,flagship              (model_catalog tier override)
#   FLAGSHIP_DEBUG=1                                (verbose logs)
#
# Stage scripts live in scripts/ relative to this file. Each stage emits
# a manifest under runs/<run_id>/_pipeline/<stage>.manifest.json. The
# pipeline status is written live at runs/<run_id>/_pipeline/status.json.
#
# Exit codes:
#   0   pipeline completed (or selected stages completed)
#   2   bad configuration
#   3   stage failed (see <stage>.manifest.json for error)
#   4   missing dependency surfaced by bootstrap_env.sh
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="${ANIMICA_REPO_ROOT:-$(cd "$PKG_DIR/../.." && pwd)}"
CONFIG_DIR="$REPO_ROOT/ai/configs"

# --------------------------------------------------------------------------- #
# Banner                                                                      #
# --------------------------------------------------------------------------- #
print_banner() {
  local mode="${FLAGSHIP_MODE:-simulate}"
  local accel
  if command -v nvidia-smi >/dev/null 2>&1; then accel="cuda"; else accel="cpu"; fi
  cat <<EOF

================================================================
  Animica flagship-agent pipeline
  mode:    $mode
  accel:   $accel
  resume:  ${FLAGSHIP_RESUME:-<none>}
  stages:  ${FLAGSHIP_STAGES:-<all>}
  tiers:   ${FLAGSHIP_TIERS:-<from model_catalog.yaml>}
  repo:    $REPO_ROOT
================================================================

EOF
}

# --------------------------------------------------------------------------- #
# Resolve python + the pipeline driver                                        #
# --------------------------------------------------------------------------- #
resolve_python() {
  if [ -n "${FLAGSHIP_PYTHON:-}" ] && [ -x "$FLAGSHIP_PYTHON" ]; then
    echo "$FLAGSHIP_PYTHON"
    return 0
  fi
  # Prefer the in-repo .venv when present.
  if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    echo "$REPO_ROOT/.venv/bin/python3"
    return 0
  fi
  if [ -x "$PKG_DIR/.venv/bin/python3" ]; then
    echo "$PKG_DIR/.venv/bin/python3"
    return 0
  fi
  command -v python3
}

# --------------------------------------------------------------------------- #
# Stage runner                                                                #
# --------------------------------------------------------------------------- #
ensure_python_path() {
  # Make agent_runtime + flagship_agent importable from the source tree.
  export PYTHONPATH="$REPO_ROOT/ai/agent_runtime/src:$REPO_ROOT/ai/flagship_agent/src${PYTHONPATH:+:$PYTHONPATH}"
}

run_python_driver() {
  local py
  py="$(resolve_python)"
  ensure_python_path
  exec "$py" -m flagship_agent.driver "$@"
}

# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #
main() {
  print_banner

  # Bootstrap (idempotent). Returns 0 if the environment is usable.
  if [ -x "$SCRIPT_DIR/bootstrap_env.sh" ]; then
    if ! "$SCRIPT_DIR/bootstrap_env.sh"; then
      echo "[flagship] bootstrap_env.sh failed; aborting." >&2
      exit 4
    fi
  fi

  # Validate configs before doing anything destructive.
  local py
  py="$(resolve_python)"
  ensure_python_path
  if ! "$py" -m agent_runtime.config >/dev/null 2>&1; then
    echo "[flagship] config validation failed; rerun with FLAGSHIP_DEBUG=1 for detail" >&2
    "$py" -m agent_runtime.config || true
    exit 2
  fi

  run_python_driver "$@"
}

main "$@"
