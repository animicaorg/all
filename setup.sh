#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# --------------------------------------------------------------------
# Super-defensive setup for Animica (Ubuntu 24.04+)
#
# Goals:
# - Build/install liboqs pinned to v0.14.0 (last line that still includes Dilithium)
# - Install liboqs-python pinned to 0.14.1
# - Create/refresh .venv
# - Install animica in editable mode WITHOUT trying to pip-resolve unpublished deps
# - Patch venv activate to export correct LIBOQS_PATH + LD_LIBRARY_PATH
# --------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
if command -v git >/dev/null 2>&1; then
  if git -C "${SCRIPT_DIR}" rev-parse --show-toplevel >/dev/null 2>&1; then
    REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
  fi
fi

VENV_DIR="${REPO_ROOT}/.venv"
DEPS_DIR="${REPO_ROOT}/.deps"
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${DEPS_DIR}" "${LOG_DIR}"

TS="$(date -u +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_DIR}/setup_${TS}.log"

# tee everything
exec > >(tee -a "${LOG_FILE}") 2>&1

log()  { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*"; }
warn() { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") WARN: $*" >&2; }
die()  { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") ERROR: $*" >&2; exit 1; }

WITH_PQ=0
CLEAN=0

usage() {
  cat <<USAGE
Usage: ./setup.sh [--with-pq] [--clean]

  --with-pq   Build/install liboqs (pinned) + liboqs-python and patch env vars.
  --clean     Remove .venv and .deps/liboqs* build dirs before setup.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-pq) WITH_PQ=1; shift;;
    --clean)   CLEAN=1; shift;;
    -h|--help) usage; exit 0;;
    *) die "Unknown argument: $1";;
  esac
done

log "================================================================="
log "Start"
log "================================================================="
log "Repo root: ${REPO_ROOT}"
log "Venv:      ${VENV_DIR}"
log "Log:       ${LOG_FILE}"

if [[ "${CLEAN}" -eq 1 ]]; then
  log "Clean requested: removing ${VENV_DIR} and selected deps"
  rm -rf "${VENV_DIR}"
  rm -rf "${DEPS_DIR}/liboqs" "${DEPS_DIR}/liboqs-build" "${DEPS_DIR}/liboqs-install"
fi

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    die "Not root and sudo not available"
  fi
fi

log "================================================================="
log "OS packages"
log "================================================================="
if [[ -n "${SUDO}" ]]; then
  log "Using sudo for apt operations"
fi

# Minimal + safe build deps
${SUDO} apt-get update -y
${SUDO} apt-get install -y \
  ca-certificates curl git jq \
  build-essential pkg-config cmake ninja-build \
  libssl-dev libffi-dev patchelf \
  python3 python3-venv python3-dev python3-pip

need_cmd python3
need_cmd git
need_cmd cmake
need_cmd ninja

PYVER="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
log "Python: ${PYVER}"

log "================================================================="
log "Virtualenv"
log "================================================================="
if [[ ! -d "${VENV_DIR}" ]]; then
  log "Creating venv: ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
else
  log "Venv exists: ${VENV_DIR}"
fi

# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"
log "Activated venv: ${VENV_DIR}"

python -m pip install -U pip setuptools wheel

SITE_PACKAGES="$(python -c 'import site; print(site.getsitepackages()[0])')"
log "site-packages: ${SITE_PACKAGES}"

log "================================================================="
log "Make repo importable (.pth)"
log "================================================================="
PTH_FILE="${SITE_PACKAGES}/animica_repo_root.pth"
echo "${REPO_ROOT}" > "${PTH_FILE}"
log "Wrote: ${PTH_FILE} -> ${REPO_ROOT}"

log "================================================================="
log "Install Animica (editable, no deps)"
log "================================================================="
# Avoid failing on unpublished deps (omni-sdk, animica-pq, etc.)
# Try common layouts:
#  1) python/ contains the package
#  2) repo root is the package
INSTALL_OK=0
if [[ -f "${REPO_ROOT}/python/pyproject.toml" || -f "${REPO_ROOT}/python/setup.py" || -f "${REPO_ROOT}/python/setup.cfg" ]]; then
  log "pip install -e ./python --no-deps"
  if python -m pip install -e "${REPO_ROOT}/python" --no-deps; then
    INSTALL_OK=1
  else
    warn "Editable install from ./python failed; will try repo root"
  fi
fi

if [[ "${INSTALL_OK}" -eq 0 ]]; then
  log "pip install -e . --no-deps"
  python -m pip install -e "${REPO_ROOT}" --no-deps || die "Editable install failed"
fi

log "================================================================="
log "Runtime Python deps (best effort)"
log "================================================================="
# Skip any internal/unpublished deps by not pip-resolving them.
python -m pip install -U \
  typer rich httpx pydantic pyyaml requests cbor2

# --------------------------------------------------------------------
# PQ section
# --------------------------------------------------------------------
if [[ "${WITH_PQ}" -eq 1 ]]; then
  log "================================================================="
  log "Post-quantum deps (liboqs pinned + liboqs-python)"
  log "================================================================="

  OQS_TAG="0.14.0"   # IMPORTANT: last line with Dilithium support
  OQS_SRC="${DEPS_DIR}/liboqs"
  OQS_BUILD="${DEPS_DIR}/liboqs-build"
  OQS_PREFIX="${DEPS_DIR}/liboqs-install"

  if [[ ! -d "${OQS_SRC}/.git" ]]; then
    log "Cloning liboqs into ${OQS_SRC}"
    git clone https://github.com/open-quantum-safe/liboqs.git "${OQS_SRC}"
  else
    log "liboqs repo exists; fetching updates"
    git -C "${OQS_SRC}" fetch --tags --force
  fi

  log "Checking out liboqs tag ${OQS_TAG}"
  if git -C "${OQS_SRC}" checkout "${OQS_TAG}" >/dev/null 2>&1; then
    :
  elif git -C "${OQS_SRC}" checkout "v${OQS_TAG}" >/dev/null 2>&1; then
    :
  else
    die "Could not checkout liboqs tag ${OQS_TAG} (tried ${OQS_TAG} and v${OQS_TAG})"
  fi

  rm -rf "${OQS_BUILD}"
  mkdir -p "${OQS_BUILD}" "${OQS_PREFIX}"

  log "Configuring liboqs (shared) -> ${OQS_PREFIX}"
  cmake -S "${OQS_SRC}" -B "${OQS_BUILD}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON \
    -DOQS_USE_OPENSSL=ON \
    -DCMAKE_INSTALL_PREFIX="${OQS_PREFIX}"

  log "Building + installing liboqs"
  ninja -C "${OQS_BUILD}" -j"$(nproc)"
  ninja -C "${OQS_BUILD}" install

  # Find installed library file
  LIBOQS_SO=""
  for cand in \
    "${OQS_PREFIX}/lib/liboqs.so" \
    "${OQS_PREFIX}/lib/liboqs.so."* \
    "${OQS_PREFIX}/lib64/liboqs.so" \
    "${OQS_PREFIX}/lib64/liboqs.so."*; do
    if [[ -f "${cand}" ]]; then
      LIBOQS_SO="${cand}"
      break
    fi
  done
  [[ -n "${LIBOQS_SO}" ]] || die "liboqs installed but liboqs.so not found under ${OQS_PREFIX}/lib*"

  log "liboqs installed: ${LIBOQS_SO}"

  # Patch venv activate in a robust, idempotent way
  ACTIVATE="${VENV_DIR}/bin/activate"
  MARK_BEGIN="# >>> animica-liboqs >>>"
  MARK_END="# <<< animica-liboqs <<<"
  if ! grep -qF "${MARK_BEGIN}" "${ACTIVATE}"; then
    log "Patching venv activate with LIBOQS_PATH + LD_LIBRARY_PATH"
    cat >> "${ACTIVATE}" <<ACTEOF

${MARK_BEGIN}
# Animica PQ runtime (auto-added by setup.sh)
export LIBOQS_PATH='${LIBOQS_SO}'
export OQS_INSTALL_PATH='${OQS_PREFIX}'
export LD_LIBRARY_PATH='${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:'"\${LD_LIBRARY_PATH:-}"
${MARK_END}
ACTEOF
  else
    log "activate already patched (marker present)"
  fi

  # Apply to current shell too
  export LIBOQS_PATH="${LIBOQS_SO}"
  export OQS_INSTALL_PATH="${OQS_PREFIX}"
  export LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:${LD_LIBRARY_PATH:-}"

  log "Installing liboqs-python==0.14.1"
  python -m pip install -U "liboqs-python==0.14.1"

  log "Verifying oqs mechanisms include Dilithium3"
  python - <<'PY'
import os, oqs
mechs = oqs.get_enabled_sig_mechanisms()
d = [m for m in mechs if "DILITHIUM" in m.upper()]
print("enabled_sig_mechanisms_count =", len(mechs))
print("dilithium_mechs =", d[:20])
if not any(m.replace("-", "").lower() == "dilithium3" for m in d):
    raise SystemExit(
        "ERROR: Dilithium3 not enabled. "
        "Ensure LIBOQS_PATH/LD_LIBRARY_PATH point to liboqs v0.14.0."
    )
PY

  log "PQ setup OK"
else
  log "PQ setup skipped (run ./setup.sh --with-pq to enable)"
fi

log "================================================================="
log "Done"
log "================================================================="
log "Next:"
log "  source ${VENV_DIR}/bin/activate"
log "  python -c \"import oqs; print([m for m in oqs.get_enabled_sig_mechanisms() if 'DILITHIUM' in m.upper()][:10])\""
