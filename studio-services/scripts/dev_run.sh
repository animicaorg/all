#!/usr/bin/env bash
# Local development launcher for studio-services with auto-reload.
# - Loads .env if present (overridable via the environment).
# - Runs uvicorn against the FastAPI factory (create_app) with --reload.
# - Watches the package source tree for changes.

set -euo pipefail

# --- Resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"              # studio-services/
PKG_DIR="${APP_ROOT}/studio_services"                                       # studio-services/studio_services

cd "${APP_ROOT}"

# --- Defaults (can be overridden via env or .env) ---------------------------
: "${HOST:=127.0.0.1}"
: "${PORT:=8080}"
: "${LOG_LEVEL:=info}"
: "${CHAIN_ID:=1337}"
: "${RPC_URL:=http://127.0.0.1:8545}"
: "${STORAGE_DIR:=${APP_ROOT}/.storage}"
: "${ALLOWED_ORIGINS:=http://localhost:5173,http://127.0.0.1:5173}"
: "${RATE_LIMITS:=deploy:5/m,verify:10/m,faucet:2/m,artifacts:60/m}"

# --- Load .env if present (shell-compatible) --------------------------------
# Prefer .env.development if present, otherwise .env
ENV_FILE=""
if [[ -f "${APP_ROOT}/.env.development" ]]; then
  ENV_FILE="${APP_ROOT}/.env.development"
elif [[ -f "${APP_ROOT}/.env" ]]; then
  ENV_FILE="${APP_ROOT}/.env"
fi

if [[ -n "${ENV_FILE}" ]]; then
  echo "→ Loading environment from ${ENV_FILE}"
  # shellcheck disable=SC1090
  set -a && source "${ENV_FILE}" && set +a
fi

# Ensure critical directories exist
mkdir -p "${STORAGE_DIR}"

# --- Sanity checks -----------------------------------------------------------
if ! command -v uvicorn >/dev/null 2>&1; then
  echo "ERROR: uvicorn not found on PATH."
  echo "  Try:  pip install 'uvicorn[standard]' 'fastapi' 'pydantic' 'msgspec' 'structlog' 'python-dotenv'"
  exit 1
fi

# Basic Python import check so reload target is valid
python - <<PYCHK || {
  echo "ERROR: Unable to import studio_services.app:create_app. Check your venv and PYTHONPATH." >&2
  exit 1
}
import importlib, sys
m = importlib.import_module("studio_services.app")
assert hasattr(m, "create_app"), "create_app() not found in studio_services.app"
print("OK: studio_services.app loaded; create_app present.")
PYCHK

# --- Runtime env -------------------------------------------------------------
export PYTHONPATH="${APP_ROOT}:${PYTHONPATH:-}"
export STUDIO_SERVICES_ENV="development"
export RPC_URL CHAIN_ID STORAGE_DIR ALLOWED_ORIGINS RATE_LIMITS

echo "======================================================================"
echo " studio-services dev run"
echo "----------------------------------------------------------------------"
echo "  HOST           : ${HOST}"
echo "  PORT           : ${PORT}"
echo "  LOG_LEVEL      : ${LOG_LEVEL}"
echo "  CHAIN_ID       : ${CHAIN_ID}"
echo "  RPC_URL        : ${RPC_URL}"
echo "  STORAGE_DIR    : ${STORAGE_DIR}"
echo "  ALLOWED_ORIGINS: ${ALLOWED_ORIGINS}"
echo "  RATE_LIMITS    : ${RATE_LIMITS}"
[[ -n "${ENV_FILE}" ]] && echo "  ENV FILE       : ${ENV_FILE}"
echo "----------------------------------------------------------------------"
echo "  Reloading dirs :"
printf "   - %s\n" "${PKG_DIR}" "${PKG_DIR}/routers" "${PKG_DIR}/services" "${PKG_DIR}/adapters" "${PKG_DIR}/security" "${PKG_DIR}/middleware"
echo "======================================================================"

# --- Launch uvicorn with auto-reload ----------------------------------------
# Use the factory pattern to build the FastAPI app:
#   target: studio_services.app:create_app
# Multiple --reload-dir improve reliability across subpackages.
exec uvicorn "studio_services.app:create_app" \
  --factory \
  --host "${HOST}" \
  --port "${PORT}" \
  --reload \
  --reload-dir "${PKG_DIR}" \
  --reload-dir "${PKG_DIR}/routers" \
  --reload-dir "${PKG_DIR}/services" \
  --reload-dir "${PKG_DIR}/adapters" \
  --reload-dir "${PKG_DIR}/security" \
  --reload-dir "${PKG_DIR}/middleware" \
  --log-level "${LOG_LEVEL}"
