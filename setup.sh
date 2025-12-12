#!/usr/bin/env bash
# setup.sh — super-defensive bootstrap for Animica on Ubuntu (incl. PQ/liboqs)
# Run:
#   sudo ./setup.sh
#
# Optional env:
#   CLEAN=1                 # wipe .venv before reinstall
#   LIBOQS_VERSION=0.15.0   # liboqs tag
#   PYTHON_BIN=python3      # python interpreter
#   FORCE_BUILD_LIBOQS=1    # rebuild liboqs even if found
#   VERIFY_WALLET_CREATE=0  # set to 1 to actually create a throwaway wallet at end

set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
DEPS_DIR="${DEPS_DIR:-$ROOT_DIR/.deps}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"

CLEAN="${CLEAN:-0}"
LIBOQS_VERSION="${LIBOQS_VERSION:-0.15.0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FORCE_BUILD_LIBOQS="${FORCE_BUILD_LIBOQS:-0}"
VERIFY_WALLET_CREATE="${VERIFY_WALLET_CREATE:-0}"

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
  warn "Log: $LOG_FILE"
  exit "$ec"
}
trap on_err ERR

section() {
  echo
  echo "[setup] ================================================================="
  log "$*"
  echo "[setup] ================================================================="
}

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }
is_root() { [[ ${EUID:-0} -eq 0 ]]; }

retry_cmd() {
  # retry_cmd <tries> <sleep_s> "<command string>"
  local tries="$1"; shift
  local sleep_s="$1"; shift
  local cmd="$1"; shift || true
  local i=1
  while true; do
    if bash -lc "$cmd"; then
      return 0
    fi
    if (( i >= tries )); then
      return 1
    fi
    warn "Command failed (attempt $i/$tries): $cmd"
    i=$((i+1))
    sleep "$sleep_s"
  done
}

detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    log "Detected OS: ${PRETTY_NAME:-unknown}"
  else
    warn "Cannot detect OS (/etc/os-release missing)."
  fi
}

apt_wait_locks() {
  local tries=60
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
  warn "apt locks did not clear; continuing anyway."
}

_APT_UPDATED=0
apt_update_once() {
  if [[ "$_APT_UPDATED" == "1" ]]; then
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
  apt-get install -y --no-install-recommends "$@"
}

clean_state() {
  if [[ "$CLEAN" == "1" ]]; then
    section "Cleaning"
    log "Removing venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
  fi
}

ensure_os_packages() {
  section "OS packages"

  if ! command -v apt-get >/dev/null 2>&1; then
    warn "apt-get not found; skipping OS package install step."
    return 0
  fi

  retry_cmd 3 5 "apt-get update -y"

  # Core build + python
  retry_cmd 3 5 "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git jq \
    build-essential pkg-config \
    cmake ninja-build \
    libssl-dev libffi-dev \
    python3 python3-venv python3-dev python3-pip \
    patchelf \
    && true"

  # Try liboqs-dev (may not exist on Ubuntu 24.04)
  if DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends liboqs-dev; then
    log "Installed liboqs-dev from apt."
  else
    warn "liboqs-dev not available via apt (expected on some Ubuntu versions). Will build liboqs from source."
  fi
}

ensure_python() {
  section "Python"
  need_cmd "$PYTHON_BIN"
  log "Using: $("$PYTHON_BIN" -V 2>&1)"
}

ensure_venv() {
  section "Virtualenv"
  if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating venv: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  else
    log "Venv exists: $VENV_DIR"
  fi

  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  log "Activated venv: $VENV_DIR"

  retry_cmd 3 5 "python -m pip install -U pip setuptools wheel"
}

write_repo_root_pth() {
  section "Make repo importable (pth)"
  local site_pkgs
  site_pkgs="$(python -c 'import site; print(site.getsitepackages()[0])')"
  log "site-packages: $site_pkgs"
  echo "$ROOT_DIR" > "$site_pkgs/animica_repo_root.pth"
  log "Wrote: $site_pkgs/animica_repo_root.pth -> $ROOT_DIR"
}

ensure_loader_paths() {
  section "Dynamic loader paths"
  # Ensure /usr/local/lib is in the loader config (liboqs install default)
  if [[ -f /etc/ld.so.conf.d/usr-local-lib.conf ]]; then
    :
  else
    echo "/usr/local/lib" > /etc/ld.so.conf.d/usr-local-lib.conf
    log "Created /etc/ld.so.conf.d/usr-local-lib.conf"
  fi
  ldconfig || true

  # Patch venv activation to find /usr/local/lib every time
  local act="$VENV_DIR/bin/activate"
  if [[ -f "$act" ]] && ! grep -q "ANIMICA_SETUP_LD_LIBRARY_PATH" "$act"; then
    cat >>"$act" <<'EOF'

# --- ANIMICA_SETUP_LD_LIBRARY_PATH (added by setup.sh) ---
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
export LIBOQS_PATH="${LIBOQS_PATH:-/usr/local}"
export OQS_INSTALL_PATH="${OQS_INSTALL_PATH:-/usr/local}"
EOF
    log "Patched venv activate with LD_LIBRARY_PATH/LIBOQS_PATH/OQS_INSTALL_PATH"
  fi

  # also set for *this* run
  export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
  export LIBOQS_PATH="${LIBOQS_PATH:-/usr/local}"
  export OQS_INSTALL_PATH="${OQS_INSTALL_PATH:-/usr/local}"
}

have_liboqs() {
  if ldconfig -p 2>/dev/null | grep -qi 'liboqs\.so'; then
    return 0
  fi
  # also check common install locations
  [[ -f /usr/local/lib/liboqs.so ]] && return 0
  [[ -f /usr/lib/x86_64-linux-gnu/liboqs.so ]] && return 0
  return 1
}

build_liboqs_from_source() {
  section "liboqs build (source)"

  if [[ "$FORCE_BUILD_LIBOQS" != "1" ]] && have_liboqs; then
    log "liboqs already present; skipping source build (set FORCE_BUILD_LIBOQS=1 to rebuild)."
    return 0
  fi

  local dir="$DEPS_DIR/liboqs"
  if [[ ! -d "$dir/.git" ]]; then
    retry_cmd 3 5 "git clone --depth=1 --branch '$LIBOQS_VERSION' https://github.com/open-quantum-safe/liboqs '$dir'"
  else
    retry_cmd 3 5 "cd '$dir' && git fetch --tags --prune && git checkout -f '$LIBOQS_VERSION'"
  fi

  retry_cmd 2 2 "cmake -S '$dir' -B '$dir/build' \
    -DBUILD_SHARED_LIBS=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DOQS_USE_OPENSSL=ON"

  retry_cmd 2 2 "cmake --build '$dir/build' --parallel '$(nproc)'"
  retry_cmd 2 2 "cmake --build '$dir/build' --target install"

  ldconfig || true

  if ! have_liboqs; then
    die "liboqs build/install completed but liboqs.so still not visible. Check /usr/local/lib and ldconfig."
  fi

  log "liboqs installed and visible."
}

install_liboqs_python() {
  section "liboqs-python (oqs) install"

  # Remove conflicting python packages (best effort)
  python -m pip uninstall -y oqs liboqs-python python-oqs pyoqs >/dev/null 2>&1 || true

  # Install from GitHub source (more reliable than wheels / version mismatch)
  local dir="$DEPS_DIR/liboqs-python"
  if [[ ! -d "$dir/.git" ]]; then
    retry_cmd 3 5 "git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python '$dir'"
  else
    retry_cmd 3 5 "cd '$dir' && git pull --ff-only || true"
  fi

  retry_cmd 2 2 "cd '$dir' && python -m pip install --no-cache-dir -U ."

  # Verify enabled signature mechanisms (must include Dilithium)
  python - <<'PY'
import oqs
mechs = oqs.get_enabled_sig_mechanisms()
print("enabled_sig_mechanisms_count =", len(mechs))
print("sample =", mechs[:12])
if not mechs:
    raise SystemExit("ERROR: oqs installed but no signature mechanisms enabled (liboqs not detected at runtime)")
if not any("DILITHIUM" in m.upper() for m in mechs):
    raise SystemExit("ERROR: Dilithium mechanisms missing; expected at least Dilithium3")
PY

  log "oqs OK (mechanisms enabled)."
}

install_python_deps() {
  section "Python deps (explicit, defensive)"
  retry_cmd 3 5 "python -m pip install -U \
    typer rich \
    requests httpx \
    cbor2 \
    pyyaml python-dotenv \
    cryptography \
    pytest || true"
}

install_local_pq() {
  section "Install local pq package (fix 'No module named pq')"

  if [[ -f "$ROOT_DIR/pq/pyproject.toml" || -f "$ROOT_DIR/pq/setup.py" ]]; then
    retry_cmd 2 2 "python -m pip install -e '$ROOT_DIR/pq' --no-deps"
    log "Installed ./pq"
    return 0
  fi

  if [[ -f "$ROOT_DIR/python/pq/pyproject.toml" || -f "$ROOT_DIR/python/pq/setup.py" ]]; then
    retry_cmd 2 2 "python -m pip install -e '$ROOT_DIR/python/pq' --no-deps"
    log "Installed ./python/pq"
    return 0
  fi

  warn "No local pq package found at ./pq or ./python/pq (continuing)."
  return 0
}

install_animica() {
  section "Install Animica (editable, no-deps to avoid omni-sdk PyPI failure)"

  [[ -d "$ROOT_DIR/python" ]] || die "Missing directory: $ROOT_DIR/python"
  [[ -f "$ROOT_DIR/python/pyproject.toml" || -f "$ROOT_DIR/python/setup.py" ]] || die "Missing python/pyproject.toml or python/setup.py"

  # IMPORTANT: --no-deps prevents pip from trying to resolve omni-sdk>=0.1.0 from PyPI
  retry_cmd 2 2 "python -m pip install -e '$ROOT_DIR/python' --no-deps"
}

pip_check() {
  section "pip check"
  python -m pip check || warn "pip check found issues (some optional deps may be missing)"
}

sanity_checks() {
  section "Sanity checks"
  python - <<'PY'
import sys
import requests, cbor2
print("python =", sys.version.split()[0])
print("requests =", requests.__version__)
print("cbor2 =", getattr(cbor2, "__version__", "unknown"))
import oqs
mechs = oqs.get_enabled_sig_mechanisms()
print("oqs mechs =", len(mechs))
print("has Dilithium =", any("DILITHIUM" in m.upper() for m in mechs))
PY

  python - <<'PY'
import animica
print("animica import OK; version =", getattr(animica, "__version__", "unknown"))
PY
}

optional_wallet_verify() {
  if [[ "$VERIFY_WALLET_CREATE" != "1" ]]; then
    log "VERIFY_WALLET_CREATE=0; skipping wallet-create verification."
    return 0
  fi

  section "Verify: animica wallet create (creates a throwaway wallet)"
  local label="__setup_verify__$(date +%s)"
  set +e
  animica wallet create --label "$label"
  local ec=$?
  set -e
  if [[ "$ec" -ne 0 ]]; then
    die "animica wallet create failed. See output above."
  fi
  log "Wallet create OK (label=$label)."
}

main() {
  section "Start"
  log "Repo root: $ROOT_DIR"
  log "Venv: $VENV_DIR"
  log "Log: $LOG_FILE"

  if ! is_root; then
    die "Run with sudo/root: sudo ./setup.sh"
  fi

  detect_os
  clean_state
  ensure_os_packages
  ensure_python
  ensure_venv
  write_repo_root_pth
  ensure_loader_paths

  # PQ stack
  build_liboqs_from_source
  install_liboqs_python

  # Project deps
  install_python_deps
  install_local_pq
  install_animica

  pip_check
  sanity_checks
  optional_wallet_verify

  section "Finished"
  log "Activate venv: source \"$VENV_DIR/bin/activate\""
  log "If you want to verify PQ mechanisms manually:"
  log "  python -c \"import oqs; print(oqs.get_enabled_sig_mechanisms()[:20])\""
  log "Done."
}

main "$@"
