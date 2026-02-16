#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(git -C "${APP_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  REPO_ROOT="$(cd "${APP_DIR}/../.." && pwd)"
fi

VENV_DIR="${APP_DIR}/.venv-modal"
VENV_PYTHON="${VENV_DIR}/bin/python"
MODAL_REQUIREMENTS="${APP_DIR}/modal/bootstrap-requirements.txt"
DEFAULT_MODAL_ENV="dev"

log() {
  echo "[modal-bootstrap] $*"
}

die() {
  echo "[modal-bootstrap] ERROR: $*" >&2
  exit 1
}

load_env_file() {
  if [[ -f "${APP_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${APP_DIR}/.env"
    set +a
  fi
}

ensure_pnpm_and_node() {
  command -v pnpm >/dev/null 2>&1 || die "pnpm is required. Install pnpm first."
  command -v node >/dev/null 2>&1 || die "Node.js is required. Install Node.js >=20 first."

  local pnpm_version node_version node_major
  pnpm_version="$(pnpm -v)"
  node_version="$(node -v)"
  node_major="$(echo "${node_version}" | sed -E 's/^v([0-9]+).*/\1/')"

  log "pnpm version: ${pnpm_version}"
  log "node version: ${node_version}"

  if [[ "${node_major}" -lt 20 ]]; then
    die "Node.js >=20 is required by chat-animica. Found ${node_version}."
  fi
}

ensure_node_modules() {
  if [[ ! -d "${REPO_ROOT}/node_modules" || ! -x "${APP_DIR}/node_modules/.bin/next" ]]; then
    log "node_modules missing; running pnpm -w install"
    (cd "${REPO_ROOT}" && pnpm -w install)
  fi
}

ensure_python() {
  local py_bin
  if command -v python3 >/dev/null 2>&1; then
    py_bin="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    py_bin="$(command -v python)"
  else
    die "Python 3 is required to deploy Modal app."
  fi

  if [[ ! -x "${VENV_PYTHON}" ]]; then
    log "creating Python venv at ${VENV_DIR}"
    "${py_bin}" -m venv "${VENV_DIR}"
  fi
}

install_modal_deps() {
  log "installing Modal CLI + Python deps"
  "${VENV_PYTHON}" -m pip install --upgrade pip setuptools wheel
  "${VENV_PYTHON}" -m pip install -r "${MODAL_REQUIREMENTS}"
}

ensure_modal_auth() {
  if [[ -n "${MODAL_TOKEN_ID:-}" && -n "${MODAL_TOKEN_SECRET:-}" ]]; then
    return
  fi
  if [[ -n "${MODAL_TOKEN:-}" ]]; then
    return
  fi

  die "Missing Modal credentials. Export MODAL_TOKEN_ID and MODAL_TOKEN_SECRET (or MODAL_TOKEN) before deploy."
}

print_modal_diagnostics() {
  local selected_env="$1"
  local modal_version profile
  modal_version="$("${VENV_PYTHON}" -m modal --version 2>&1 || true)"
  profile="${MODAL_PROFILE:-default}"

  log "Diagnostics:"
  log "- modal version: ${modal_version}"
  log "- modal profile: ${profile}"
  log "- modal environment used: ${selected_env:-<default>}"
  log "- MODAL_TOKEN_ID set: $([[ -n "${MODAL_TOKEN_ID:-}" ]] && echo yes || echo no)"
  log "- MODAL_TOKEN_SECRET set: $([[ -n "${MODAL_TOKEN_SECRET:-}" ]] && echo yes || echo no)"
  log "- MODAL_TOKEN set: $([[ -n "${MODAL_TOKEN:-}" ]] && echo yes || echo no)"
}

env_list_output() {
  local output status

  set +e
  output="$("${VENV_PYTHON}" -m modal environment list 2>&1)"
  status=$?
  set -e
  if [[ ${status} -eq 0 ]]; then
    echo "${output}"
    return 0
  fi

  set +e
  output="$("${VENV_PYTHON}" -m modal env list 2>&1)"
  status=$?
  set -e
  if [[ ${status} -eq 0 ]]; then
    echo "${output}"
    return 0
  fi

  return 1
}

resolve_modal_env_arg() {
  local requested_env env_output
  requested_env="${MODAL_ENV:-${MODAL_ENVIRONMENT:-}}"

  if [[ -z "${requested_env}" ]]; then
    echo ""
    return 0
  fi

  if env_output="$(env_list_output)"; then
    if echo "${env_output}" | awk '{print $1}' | grep -Fxq "${requested_env}"; then
      echo "${requested_env}"
      return 0
    fi

    log "Requested Modal environment '${requested_env}' not found; deploying with default environment."
    log "If you want this environment, run: modal environment create ${requested_env}"
    echo ""
    return 0
  fi

  log "Modal environment listing command unavailable in this Modal CLI version; deploying with default environment."
  echo ""
}

deploy_modal() {
  load_env_file
  ensure_modal_auth
  ensure_python
  install_modal_deps

  local modal_version selected_env
  modal_version="$("${VENV_PYTHON}" -m modal --version 2>&1 || true)"
  log "modal version: ${modal_version}"

  selected_env="$(resolve_modal_env_arg)"

  log "running deploy"
  local -a cmd
  cmd=("${VENV_PYTHON}" -m modal deploy modal/modal_app.py)
  if [[ -n "${selected_env}" ]]; then
    cmd+=(--env "${selected_env}")
  fi

  set +e
  (
    cd "${APP_DIR}"
    "${cmd[@]}"
  )
  local status=$?
  set -e

  if [[ ${status} -ne 0 ]]; then
    log "Modal deploy failed."
    print_modal_diagnostics "${selected_env}"
    if [[ -n "${MODAL_ENV:-${MODAL_ENVIRONMENT:-}}" ]]; then
      log "If the environment is missing, create it with: modal environment create ${MODAL_ENV:-${MODAL_ENVIRONMENT}}"
    else
      log "If you want to deploy to a named environment, create one with: modal environment create ${DEFAULT_MODAL_ENV}"
    fi
    exit ${status}
  fi

  log "Modal deploy completed successfully."
}

show_modal_logs() {
  load_env_file
  ensure_modal_auth
  ensure_python
  install_modal_deps
  (cd "${APP_DIR}" && "${VENV_PYTHON}" -m modal app logs modal/modal_app.py)
}

start_dev() {
  ensure_pnpm_and_node
  ensure_node_modules
  (cd "${APP_DIR}" && pnpm exec next dev -p 4321)
}

main() {
  local command="${1:-deploy}"
  case "${command}" in
    deploy)
      deploy_modal
      ;;
    logs)
      show_modal_logs
      ;;
    dev)
      start_dev
      ;;
    *)
      die "Unknown command '${command}'. Use: deploy | logs | dev"
      ;;
  esac
}

main "$@"
