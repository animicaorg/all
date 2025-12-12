#!/usr/bin/env bash
# setup.sh — SUPER DEFENSIVE Animica bootstrap for Ubuntu 24.04+
#
# Fixes the exact issue you hit:
# - On OQS 0.15.0, Dilithium is renamed to ML-DSA (FIPS 204), so oqs.get_enabled_sig_mechanisms()
#   will NOT contain "DILITHIUM" even though you DO have PQ sigs enabled.
# - This script now treats ML-DSA-* as acceptable (and will not fail).
#
# Also:
# - Works even when liboqs-dev is not in apt (builds liboqs from source if needed)
# - Installs liboqs-python from GitHub source (more reliable than wheels)
# - Installs Animica editable with --no-deps (avoids omni-sdk PyPI failure)
# - Installs cbor2 + requests + other runtime deps
# - Installs local pq package if present
#
# Run:
#   sudo ./setup.sh --with-pq --clean
#
# Optional env:
#   LIBOQS_VERSION=0.15.0
#   PYTHON_BIN=python3
#   FORCE_BUILD_LIBOQS=1
#   VERIFY_WALLET_CREATE=0|1

set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
DEPS_DIR="${DEPS_DIR:-$ROOT_DIR/.deps}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"

LIBOQS_VERSION="${LIBOQS_VERSION:-0.15.0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FORCE_BUILD_LIBOQS="${FORCE_BUILD_LIBOQS:-0}"
VERIFY_WALLET_CREATE="${VERIFY_WALLET_CREATE:-0}"

WITH_PQ=0
CLEAN=0

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

usage() {
  cat <<EOF
Usage: sudo ./setup.sh [--with-pq] [--clean] [--verify-wallet]

  --with-pq         Build/install liboqs + liboqs-python and verify mechanisms.
  --clean           Remove .venv before reinstall.
  --verify-wallet   Run 'animica wallet create' at the end (creates a throwaway wallet).

Env:
  LIBOQS_VERSION=0.15.0
  PYTHON_BIN=python3
  FORCE_BUILD_LIBOQS=1
  VERIFY_WALLET_CREATE=0|1
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with-pq) WITH_PQ=1; shift ;;
      --clean) CLEAN=1; shift ;;
      --verify-wallet) VERIFY_WALLET_CREATE=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *)
        die "Unknown arg: $1 (try --help)"
        ;;
    esac
  done
}

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

ensure_os_packages() {
  section "OS packages"

  if ! command -v apt-get >/dev/null 2>&1; then
    warn "apt-get not found; skipping OS package install step."
    return 0
  fi

  apt_wait_locks
  retry_cmd 3 5 "apt-get update -y"

  apt_wait_locks
  retry_cmd 3 5 "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git jq \
    build-essential pkg-config \
    cmake ninja-build \
    libssl-dev libffi-dev \
    python3 python3-venv python3-dev python3-pip \
    patchelf"

  if [[ "$WITH_PQ" == "1" ]]; then
    # liboqs-dev frequently missing; try but don't fail
    if DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends liboqs-dev; then
      log "Installed liboqs-dev from apt."
    else
      warn "liboqs-dev not available via apt (expected on some Ubuntu versions). Will build liboqs from source."
    fi
  fi
}

clean_state() {
  if [[ "$CLEAN" == "1" ]]; then
    section "Cleaning"
    log "Removing venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
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
  if [[ -f /etc/ld.so.conf.d/usr-local-lib.conf ]]; then
    :
  else
    echo "/usr/local/lib" > /etc/ld.so.conf.d/usr-local-lib.conf
    log "Created /etc/ld.so.conf.d/usr-local-lib.conf"
  fi
  ldconfig || true

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

  export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
  export LIBOQS_PATH="${LIBOQS_PATH:-/usr/local}"
  export OQS_INSTALL_PATH="${OQS_INSTALL_PATH:-/usr/local}"
}

have_liboqs() {
  if ldconfig -p 2>/dev/null | grep -qi 'liboqs\.so'; then
    return 0
  fi
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
  have_liboqs || die "liboqs build/install finished but liboqs.so not visible."
  log "liboqs installed and visible."
}

install_liboqs_python() {
  section "liboqs-python (oqs) install"
  python -m pip uninstall -y oqs liboqs-python python-oqs pyoqs >/dev/null 2>&1 || true

  local dir="$DEPS_DIR/liboqs-python"
  if [[ ! -d "$dir/.git" ]]; then
    retry_cmd 3 5 "git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python '$dir'"
  else
    retry_cmd 3 5 "cd '$dir' && git pull --ff-only || true"
  fi

  retry_cmd 2 2 "cd '$dir' && python -m pip install --no-cache-dir -U ."

  # ✅ FIX: accept either classic Dilithium OR the FIPS rename ML-DSA
  python - <<'PY'
import oqs
mechs = oqs.get_enabled_sig_mechanisms()
print("enabled_sig_mechanisms_count =", len(mechs))
print("sample =", mechs[:12])

if not mechs:
    raise SystemExit("ERROR: oqs installed but no signature mechanisms enabled (liboqs not detected at runtime)")

upper = [m.upper() for m in mechs]
has_dilithium = any("DILITHIUM" in m for m in upper)
has_mldsa = any(m.startswith("ML-DSA-") for m in upper)

print("has_dilithium =", has_dilithium)
print("has_ml_dsa    =", has_mldsa)

# OQS 0.15+ uses ML-DSA (FIPS 204) instead of Dilithium naming.
if not (has_dilithium or has_mldsa):
    raise SystemExit("ERROR: Neither Dilithium nor ML-DSA mechanisms found; PQ signing likely unusable")
PY

  log "oqs OK (PQ mechanisms enabled)."
}

install_python_deps() {
  section "Python deps (explicit, defensive)"
  retry_cmd 3 5 "python -m pip install -U \
    typer rich \
    requests httpx \
    cbor2 \
    pyyaml python-dotenv \
    cryptography"
  python -m pip install -U pytest >/dev/null 2>&1 || true
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
  section "Install Animica (editable, no-deps to avoid omni-sdk)"
  [[ -d "$ROOT_DIR/python" ]] || die "Missing directory: $ROOT_DIR/python"
  [[ -f "$ROOT_DIR/python/pyproject.toml" || -f "$ROOT_DIR/python/setup.py" ]] || die "Missing python/pyproject.toml or python/setup.py"
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
try:
    import oqs
    mechs = oqs.get_enabled_sig_mechanisms()
    print("oqs mechs =", len(mechs))
    upper = [m.upper() for m in mechs]
    print("has Dilithium =", any("DILITHIUM" in m for m in upper))
    print("has ML-DSA =", any(m.startswith("ML-DSA-") for m in upper))
except Exception as e:
    print("oqs not installed:", e)
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
  animica wallet create --label "$label"
  log "Wallet create OK (label=$label)."
}

main() {
  section "Start"
  log "Repo root: $ROOT_DIR"
  log "Venv: $VENV_DIR"
  log "Log: $LOG_FILE"

  if ! is_root; then
    die "Run with sudo/root: sudo ./setup.sh --with-pq"
  fi

  detect_os
  clean_state
  ensure_os_packages
  ensure_python
  ensure_venv
  write_repo_root_pth
  ensure_loader_paths

  if [[ "$WITH_PQ" == "1" ]]; then
    build_liboqs_from_source
    install_liboqs_python
  else
    log "--with-pq not set; skipping liboqs/liboqs-python."
  fi

  install_python_deps
  install_local_pq
  install_animica

  pip_check
  sanity_checks
  optional_wallet_verify

  section "Finished"
  log "Activate venv: source \"$VENV_DIR/bin/activate\""
  log "Done."
}

parse_args "$@"
main
