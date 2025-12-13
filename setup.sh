#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# Animica bootstrapper
# - Builds vendored liboqs ${LIBOQS_VERSION} into .deps/liboqs/${LIBOQS_VERSION}
# - Pins python oqs bindings to the same version
# - Installs animica + dependencies into .venv
# - Creates a user-level shim so `animica` works without activating the venv

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup_$(date -u +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log()  { echo "[setup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
warn() { echo "[setup] WARN: $*" >&2; }
die()  { echo "[setup] ERROR: $*" >&2; exit 1; }

# Load shared liboqs helpers
if [[ ! -f "$REPO_ROOT/ops/liboqs.sh" ]]; then
  die "Missing ops/liboqs.sh (liboqs helpers)"
fi
# shellcheck disable=SC1091
source "$REPO_ROOT/ops/liboqs.sh"

CLEAN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    *) die "Unknown argument: $1" ;;
  esac
done

log "Repo root: $REPO_ROOT"
log "Log file:  $LOG_FILE"

if [[ $CLEAN -eq 1 ]]; then
  log "Cleaning previous environment"
  rm -rf "$REPO_ROOT/.venv" "$REPO_ROOT/.deps/liboqs"
fi

ensure_prereqs() {
  if command -v apt-get >/dev/null 2>&1; then
    if [[ ${ANIMICA_SKIP_APT:-0} -eq 1 ]]; then
      warn "Skipping apt-get because ANIMICA_SKIP_APT=1"
    else
      export DEBIAN_FRONTEND=noninteractive
      if ! apt-get update -y; then
        warn "apt-get update failed; relying on existing packages"
      else
        if ! apt-get install -y --no-install-recommends \
          git curl ca-certificates \
          build-essential pkg-config cmake ninja-build \
          python3 python3-venv python3-dev python3-pip \
          libssl-dev libgmp-dev; then
          warn "apt-get install failed; ensure build tools are present"
        fi
      fi
    fi
  else
    warn "apt-get not available; ensure cmake/ninja/python3-venv/git are installed manually"
  fi
}

ensure_venv() {
  if [[ ! -d "$REPO_ROOT/.venv" ]]; then
    log "Creating virtual environment"
    python3 -m venv "$REPO_ROOT/.venv"
  else
    log "Using existing virtual environment"
  fi
  "$REPO_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
}

install_python_stack() {
  local pip_cmd="$REPO_ROOT/.venv/bin/pip"
  local prefix lib
  prefix="$(liboqs_prefix)"
  lib="$(liboqs_locate_lib || true)"

  log "Installing python oqs bindings ${LIBOQS_VERSION} (from source, linked to vendored liboqs)"
  OQS_INSTALL_PATH="$prefix" LIBOQS_PATH="${lib:-$prefix/lib/liboqs.so}" LD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:${LD_LIBRARY_PATH:-}" \
    "$pip_cmd" install --no-binary oqs --no-cache-dir "oqs==${LIBOQS_VERSION}"

  log "Installing local packages (pq + animica)"
  if [[ -f "$REPO_ROOT/pq/pyproject.toml" ]]; then
    "$pip_cmd" install -e "$REPO_ROOT/pq"
  fi
  "$pip_cmd" install -e "$REPO_ROOT/python"
}

create_shim() {
  local prefix lib shim_dir shim_path
  prefix="$(liboqs_prefix)"
  lib="$(liboqs_locate_lib || true)"
  shim_dir="$HOME/.local/bin"
  shim_path="$shim_dir/animica"

  mkdir -p "$shim_dir"
  cat >"$shim_path" <<EOF_SHIM
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
REPO_ROOT="$REPO_ROOT"
LIBOQS_VERSION="${LIBOQS_VERSION}"
PREFIX="$prefix"
LIB_PATH="${lib:-$prefix/lib/liboqs.so}"
export OQS_INSTALL_PATH="$prefix"
export LIBOQS_PATH="$LIB_PATH"
export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:\${LD_LIBRARY_PATH:-}"
if [[ "$(uname -s)" == "Darwin" ]]; then
  export DYLD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:\${DYLD_LIBRARY_PATH:-}"
fi
exec "$REPO_ROOT/.venv/bin/animica" "$@"
EOF_SHIM
  chmod +x "$shim_path"

  case ":${PATH}:" in
    *:"${shim_dir}":*) log "animica shim installed at ${shim_path}" ;;
    *) warn "${shim_dir} is not on PATH. Add: export PATH=${shim_dir}:\${PATH}" ;;
  esac
}

post_checks() {
  local prefix lib
  prefix="$(liboqs_prefix)"
  lib="$(liboqs_locate_lib || true)"
  log "Sanity check: python oqs version"
  OQS_INSTALL_PATH="$prefix" LIBOQS_PATH="${lib:-$prefix/lib/liboqs.so}" LD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:${LD_LIBRARY_PATH:-}" \
    "$REPO_ROOT/.venv/bin/python" - <<'PY'
import oqs
print("liboqs:", oqs.oqs_version())
print("py:", getattr(oqs, "__version__", "?"))
if not oqs.oqs_version().startswith("0.14."):
    raise SystemExit("liboqs version mismatch; expected 0.14.x")
PY

  log "Sanity check: animica CLI"
  OQS_INSTALL_PATH="$prefix" LIBOQS_PATH="${lib:-$prefix/lib/liboqs.so}" LD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:${LD_LIBRARY_PATH:-}" \
    "$REPO_ROOT/.venv/bin/animica" --help >/dev/null
}

main() {
  ensure_prereqs
  liboqs_build
  ensure_venv
  install_python_stack
  create_shim
  post_checks
  log "Setup complete. You can now run 'animica --help' from a new shell (shim in ~/.local/bin)."
}

main "$@"
