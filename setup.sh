#!/usr/bin/env bash
# setup.sh — SUPER DEFENSIVE full bootstrap for Animica on Ubuntu (Contabo/VPS friendly)
#
# Goals:
# - Create/refresh .venv
# - Install OS deps needed for building PQ (liboqs) + Python packages
# - Build + install liboqs v0.15.0 from source if apt package not available (Ubuntu 24.04 often lacks liboqs-dev)
# - Install liboqs-python *matching* liboqs (prefer GitHub source if wheels mismatch)
# - Install Animica python package editable WITHOUT failing on omni-sdk (not on PyPI)
# - Ensure runtime deps: cbor2, requests, etc.
# - Install local pq package (so "No module named 'pq'" never happens)
#
# Usage:
#   sudo ./setup.sh
# Optional env:
#   CLEAN=1              # wipe .venv before install
#   LIBOQS_VERSION=0.15.0
#   PYTHON_BIN=python3.12 (or python3)
#   EXTRAS=dev,stratum   # (only used for optional pip installs; script is safe if extras missing)

set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
DEPS_DIR="${DEPS_DIR:-$ROOT_DIR/.deps}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"
LIBOQS_VERSION="${LIBOQS_VERSION:-0.15.0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
EXTRAS="${EXTRAS:-dev,stratum}"
CLEAN="${CLEAN:-0}"

mkdir -p "$DEPS_DIR" "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup_$(date -u +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[setup] $(ts) $*"; }
warn() { echo "[setup] $(ts) WARN: $*" >&2; }
die() { echo "[setup] $(ts) ERROR: $*" >&2; exit 1; }

on_err() {
  local ec=$?
  warn "FAILED (exit=$ec) at line ${BASH_LINENO[0]}: ${BASH_COMMAND}"
  warn "Log saved at: $LOG_FILE"
  exit "$ec"
}
trap on_err ERR

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

is_root() { [[ ${EUID:-0} -eq 0 ]]; }

require_root() {
  if ! is_root; then
    die "Run as root (or sudo): sudo ./setup.sh"
  fi
}

detect_ubuntu() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    log "Detected OS: ${PRETTY_NAME:-unknown}"
  else
    warn "Cannot detect OS (/etc/os-release missing). Proceeding anyway."
  fi
}

apt_wait_locks() {
  # Wait for apt/dpkg locks to clear
  local tries=40
  local sleep_s=2
  for ((i=1; i<=tries; i++)); do
    if fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
       fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
       fuser /var/lib/apt/lists/lock >/dev/null 2>&1; then
      log "apt lock detected; waiting ${sleep_s}s (attempt $i/$tries)..."
      sleep "$sleep_s"
      continue
    fi
    return 0
  done
  warn "apt locks did not clear after ${tries} attempts; continuing (may fail)."
}

apt_update_once() {
  if [[ "${_APT_UPDATED:-0}" == "1" ]]; then
    return 0
  fi
  apt_wait_locks
  export DEBIAN_FRONTEND=noninteractive
  log "apt-get update"
  apt-get update -y
  _APT_UPDATED=1
}

apt_install() {
  apt_update_once
  apt_wait_locks
  export DEBIAN_FRONTEND=noninteractive
  log "apt-get install: $*"
  apt-get install -y --no-install-recommends "$@" || return 1
}

retry() {
  # retry <tries> <sleep> -- <cmd...>
  local tries="$1"; shift
  local sleep_s="$1"; shift
  [[ "$1" == "--" ]] && shift
  local n=1
  while true; do
    if "$@"; then
      return 0
    fi
    if (( n >= tries )); then
      return 1
    fi
    warn "Command failed (attempt $n/$tries): $*"
    n=$((n+1))
    sleep "$sleep_s"
  done
}

clean_state() {
  if [[ "$CLEAN" == "1" ]]; then
    log "CLEAN=1 set; removing $VENV_DIR"
    rm -rf "$VENV_DIR"
  fi
}

ensure_os_deps() {
  section "OS dependencies"
  apt_update_once

  # Core toolchain
  retry 3 5 -- apt_install \
    ca-certificates curl git jq \
    build-essential pkg-config \
    cmake ninja-build \
    libssl-dev libffi-dev \
    python3 python3-venv python3-dev python3-pip || die "Failed to install base OS deps"

  # NOTE: liboqs-dev is often missing on Ubuntu 24.04 repos.
  # We'll *try* it, but if not found we build from source.
  if apt_install liboqs-dev >/dev/null 2>&1; then
    log "Installed liboqs-dev from apt"
  else
    warn "liboqs-dev not available via apt on this system (expected on some Ubuntu versions). Will build liboqs from source."
  fi

  # For ldconfig
  retry 3 5 -- apt_install libc6-dev || true
}

section() {
  echo
  echo "[setup] ================================================================="
  log "$*"
  echo "[setup] ================================================================="
}

ensure_python_bin() {
  section "Python interpreter"
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="${PYTHON_BIN:-python3.12}"
  fi
  need_cmd "$PYTHON_BIN"
  log "Using Python: $("$PYTHON_BIN" -V 2>&1)"
}

ensure_venv() {
  section "Virtual environment"
  if [[ -d "$VENV_DIR" ]]; then
    log "Venv already exists: $VENV_DIR"
  else
    log "Creating venv: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  log "Activated venv: $VENV_DIR"

  log "Upgrading pip tooling"
  retry 3 5 -- python -m pip install -U pip setuptools wheel
}

write_repo_root_pth() {
  section "Ensure repo root is importable (pth)"
  local site_pkgs
  site_pkgs="$(python -c 'import site; print(site.getsitepackages()[0])')"
  log "site-packages: $site_pkgs"
  echo "$ROOT_DIR" > "$site_pkgs/animica_repo_root.pth"
  log "Wrote: $site_pkgs/animica_repo_root.pth -> $ROOT_DIR"
}

build_install_liboqs_from_source() {
  section "liboqs (build from source if needed)"

  # If liboqs is already in ldconfig cache, good enough
  if ldconfig -p 2>/dev/null | grep -qi 'liboqs\.so'; then
    log "liboqs shared library found in ldconfig cache."
    return 0
  fi

  log "Building liboqs v$LIBOQS_VERSION from source (shared libs)"
  local dir="$DEPS_DIR/liboqs"

  if [[ ! -d "$dir/.git" ]]; then
    retry 3 5 -- git clone --depth=1 --branch "$LIBOQS_VERSION" https://github.com/open-quantum-safe/liboqs "$dir" \
      || die "Failed to clone liboqs"
  else
    (cd "$dir" && git fetch --tags --prune && git checkout -f "$LIBOQS_VERSION")
  fi

  # Configure/build/install
  retry 2 2 -- cmake -S "$dir" -B "$dir/build" \
    -DBUILD_SHARED_LIBS=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DOQS_USE_OPENSSL=ON || die "cmake configure failed for liboqs"

  retry 2 2 -- cmake --build "$dir/build" --parallel "$(nproc)" || die "cmake build failed for liboqs"
  retry 2 2 -- cmake --build "$dir/build" --target install || die "cmake install failed for liboqs"

  # Ensure /usr/local/lib is in loader paths
  if [[ ! -f /etc/ld.so.conf.d/usr-local-lib.conf ]]; then
    echo "/usr/local/lib" > /etc/ld.so.conf.d/usr-local-lib.conf
  fi
  ldconfig || true

  if ! ldconfig -p 2>/dev/null | grep -qi 'liboqs\.so'; then
    warn "liboqs still not visible in ldconfig. We'll force LD_LIBRARY_PATH in venv activation."
  else
    log "liboqs installed and visible to loader."
  fi
}

install_liboqs_python() {
  section "liboqs-python (oqs module) install + verification"

  # Always ensure loader sees /usr/local/lib for this shell
  export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"

  log "Removing any conflicting oqs installs (best-effort)"
  python -m pip uninstall -y oqs liboqs-python python-oqs pyoqs >/dev/null 2>&1 || true

  # Prefer source install from GitHub to avoid version mismatch wheels (your warning showed 0.14.1)
  log "Installing liboqs-python from GitHub (most reliable with liboqs=$LIBOQS_VERSION)"
  local dir="$DEPS_DIR/liboqs-python"
  if [[ ! -d "$dir/.git" ]]; then
    retry 3 5 -- git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python "$dir" \
      || die "Failed to clone liboqs-python"
  else
    (cd "$dir" && git pull --ff-only) || true
  fi

  # Build/install (this binds to the liboqs you installed)
  retry 2 2 -- (cd "$dir" && python -m pip install --no-cache-dir -U .) \
    || die "Failed to install liboqs-python from source"

  log "Verifying oqs mechanisms are enabled"
  python - <<'PY'
import oqs
mechs = oqs.get_enabled_sig_mechanisms()
print("enabled_sig_mechanisms_count =", len(mechs))
print("sample =", mechs[:12])
if not mechs:
    raise SystemExit("ERROR: oqs installed but no signature mechanisms enabled (liboqs not detected at runtime)")
want = any("DILITHIUM" in m.upper() for m in mechs)
print("has_dilithium =", want)
if not want:
    raise SystemExit("ERROR: Dilithium mechanisms missing; expected at least Dilithium3")
PY

  log "oqs OK."
}

ensure_ld_library_path_persisted() {
  section "Persist loader paths for venv"
  # Make sure every activation can find liboqs installed under /usr/local/lib
  local act="$VENV_DIR/bin/activate"
  if [[ -f "$act" ]]; then
    if ! grep -q "ANIMICA_SETUP_LD_LIBRARY_PATH" "$act" 2>/dev/null; then
      cat >>"$act" <<'EOF'

# --- ANIMICA_SETUP_LD_LIBRARY_PATH (added by setup.sh) ---
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
export LIBOQS_PATH="${LIBOQS_PATH:-/usr/local}"
export OQS_INSTALL_PATH="${OQS_INSTALL_PATH:-/usr/local}"
EOF
      log "Patched venv activate to export LD_LIBRARY_PATH/LIBOQS_PATH"
    else
      log "Venv activate already patched for LD_LIBRARY_PATH"
    fi
  fi
}

install_python_runtime_deps() {
  section "Python runtime deps (defensive install)"

  # Important: install deps explicitly because repo may reference non-PyPI omni-sdk.
  retry 3 5 -- python -m pip install -U \
    typer rich \
    requests httpx \
    cbor2 \
    pyyaml python-dotenv \
    cryptography

  # Often handy for tooling/tests; harmless if unused:
  python -m pip install -U pytest || true
}

install_local_pq_package() {
  section "Install local pq package (fixes 'No module named pq')"

  if [[ -f "$ROOT_DIR/pq/pyproject.toml" || -f "$ROOT_DIR/pq/setup.py" ]]; then
    log "Installing ./pq (editable)"
    retry 2 2 -- python -m pip install -e "$ROOT_DIR/pq" --no-deps
    return 0
  fi

  if [[ -f "$ROOT_DIR/python/pq/pyproject.toml" || -f "$ROOT_DIR/python/pq/setup.py" ]]; then
    log "Installing ./python/pq (editable)"
    retry 2 2 -- python -m pip install -e "$ROOT_DIR/python/pq" --no-deps
    return 0
  fi

  warn "No local pq package found at ./pq or ./python/pq; leaving as-is."
  return 0
}

install_animica_editable() {
  section "Install Animica Python package (editable, no-deps to avoid omni-sdk)"

  [[ -d "$ROOT_DIR/python" ]] || die "Missing $ROOT_DIR/python directory"

  if [[ -f "$ROOT_DIR/python/pyproject.toml" || -f "$ROOT_DIR/python/setup.py" ]]; then
    log "pip install -e ./python --no-deps"
    retry 2 2 -- python -m pip install -e "$ROOT_DIR/python" --no-deps
  else
    die "python package missing pyproject.toml/setup.py under ./python"
  fi
}

pip_check_and_report() {
  section "pip check"
  python -m pip check || warn "pip check reported issues (some optional deps may be unresolved)"
}

verify_wallet_create() {
  section "Verify PQ wallet creation"
  set +e
  out="$(animica wallet create --label __setup_verify__ 2>&1)"
  ec=$?
  set -e
  echo "$out"
  if [[ "$ec" -ne 0 ]]; then
    die "animica wallet create failed. Review output above. (Log: $LOG_FILE)"
  fi
  log "Wallet create succeeded."
}

main() {
  require_root
  detect_ubuntu
  clean_state
  ensure_python_bin
  ensure_os_deps
  ensure_venv
  write_repo_root_pth

  # PQ chain (build source + python wrapper)
  build_install_liboqs_from_source
  install_liboqs_python
  ensure_ld_library_path_persisted

  # python deps + repo installs
  install_python_runtime_deps
  install_local_pq_package
  install_animica_editable
  pip_check_and_report

  verify_wallet_create

  section "Finished"
  log "Activate venv: source \"$VENV_DIR/bin/activate\""
  log "If you ever see oqs/liboqs warnings, run:"
  log "  python -c \"import oqs; print(oqs.get_enabled_sig_mechanisms()[:20])\""
  log "Log saved at: $LOG_FILE"
}

main "$@"
