#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Animica repo setup (Ubuntu-focused, defensive)
# - Creates/updates .venv
# - Installs Python deps (including cbor2 + requests)
# - Optionally builds liboqs (pinned) and installs liboqs-python
# - Avoids failing on non-PyPI deps (omni-sdk, animica-pq)
# ==============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
VENV_DIR="$ROOT_DIR/.venv"
DEPS_DIR="$ROOT_DIR/.deps"
LOG_DIR="$ROOT_DIR/logs"
TS="$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/setup_${TS}.log"

WITH_PQ=0
CLEAN=0

usage() {
  cat <<'EOF'
Usage: ./setup.sh [--with-pq] [--clean]

  --with-pq   Build/install liboqs + liboqs-python and wire env vars
  --clean     Remove .venv and .deps before installing

Notes:
- This script is defensive and idempotent.
- It intentionally does NOT attempt to pip-install non-PyPI deps like omni-sdk or animica-pq.
EOF
}

log() {
  local msg="$*"
  echo "[setup] $(date -u +%Y-%m-%dT%H:%M:%SZ) ${msg}" | tee -a "$LOG_FILE"
}

die() {
  log "ERROR: $*"
  log "Log: $LOG_FILE"
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo -n "$@" || die "Command failed (need sudo): $*"
  fi
}

trap 'die "setup failed at line $LINENO: $BASH_COMMAND"' ERR

mkdir -p "$LOG_DIR"

# --- args ---------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-pq) WITH_PQ=1; shift ;;
    --clean)   CLEAN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown arg: $1" ;;
  esac
done

log "================================================================="
log "Start"
log "================================================================="
log "Repo root: $ROOT_DIR"
log "Venv:      $VENV_DIR"
log "Deps:      $DEPS_DIR"
log "Log:       $LOG_FILE"

# --- OS info ------------------------------------------------------------------
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  log "Detected OS: ${PRETTY_NAME:-unknown}"
else
  log "Detected OS: unknown"
fi

# --- clean --------------------------------------------------------------------
if [[ "$CLEAN" -eq 1 ]]; then
  log "Cleaning .venv/.deps ..."
  rm -rf "$VENV_DIR" "$DEPS_DIR"
fi

mkdir -p "$DEPS_DIR"

# --- OS packages --------------------------------------------------------------
log "================================================================="
log "OS packages"
log "================================================================="
need_cmd apt-get
as_root apt-get update -y >>"$LOG_FILE" 2>&1

# Minimum set for python + building liboqs
OS_PKGS=(
  ca-certificates curl git jq
  build-essential pkg-config cmake ninja-build
  libssl-dev libffi-dev patchelf
  python3 python3-venv python3-dev python3-pip
)
as_root apt-get install -y "${OS_PKGS[@]}" >>"$LOG_FILE" 2>&1

# --- python venv --------------------------------------------------------------
log "================================================================="
log "Python / Virtualenv"
log "================================================================="
need_cmd python3
log "Using: $(python3 -V)"

if [[ ! -d "$VENV_DIR" ]]; then
  log "Creating venv..."
  python3 -m venv "$VENV_DIR" >>"$LOG_FILE" 2>&1
else
  log "Venv exists."
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
log "Activated venv: $VENV_DIR"

python -m pip install -U pip setuptools wheel >>"$LOG_FILE" 2>&1

# Make repo importable (root on sys.path)
SITE_PKGS="$(python - <<'PY'
import site
sp = site.getsitepackages()
print(sp[0] if sp else "")
PY
)"
[[ -n "$SITE_PKGS" ]] || die "Could not locate site-packages"

PTH_FILE="$SITE_PKGS/animica_repo_root.pth"
echo "$ROOT_DIR" > "$PTH_FILE"
log "Wrote: $PTH_FILE -> $ROOT_DIR"

# --- install python package (NO DEPS to avoid omni-sdk/animica-pq failures) ---
log "================================================================="
log "Install animica (editable, no-deps)"
log "================================================================="
if [[ -d "$ROOT_DIR/python" ]]; then
  python -m pip install -e "$ROOT_DIR/python" --no-deps >>"$LOG_FILE" 2>&1 || die "pip install -e python failed"
else
  die "Missing $ROOT_DIR/python directory"
fi

# --- runtime python deps (explicit, known PyPI) -------------------------------
log "================================================================="
log "Python deps (explicit)"
log "================================================================="
python -m pip install -U \
  typer rich requests cbor2 \
  httpx pydantic pyyaml \
  >>"$LOG_FILE" 2>&1

# If you want tests/dev tools available by default:
python -m pip install -U pytest pytest-asyncio anyio >>"$LOG_FILE" 2>&1 || true

# --- PQ setup -----------------------------------------------------------------
if [[ "$WITH_PQ" -eq 1 ]]; then
  log "================================================================="
  log "PQ: liboqs + liboqs-python"
  log "================================================================="

  OQS_VERSION="0.14.0"
  OQS_PREFIX="/usr/local/liboqs-${OQS_VERSION}"
  OQS_SRC="$DEPS_DIR/liboqs"

  # Build liboqs pinned to a version that still includes Dilithium
  if [[ ! -d "$OQS_SRC/.git" ]]; then
    log "Cloning liboqs..."
    git clone --depth 1 --branch "$OQS_VERSION" https://github.com/open-quantum-safe/liboqs.git "$OQS_SRC" >>"$LOG_FILE" 2>&1 \
      || die "Failed to clone liboqs (tag $OQS_VERSION)"
  else
    log "liboqs repo already present: $OQS_SRC"
  fi

  # Build/install (idempotent-ish)
  if [[ ! -f "$OQS_PREFIX/lib/liboqs.so" && ! -f "$OQS_PREFIX/lib64/liboqs.so" ]]; then
    log "Building liboqs -> $OQS_PREFIX ..."
    rm -rf "$OQS_SRC/build" || true
    cmake -S "$OQS_SRC" -B "$OQS_SRC/build" -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$OQS_PREFIX" \
      -DOQS_BUILD_ONLY_LIB=ON \
      -DOQS_BUILD_SHARED_LIBS=ON \
      -DBUILD_TESTING=OFF >>"$LOG_FILE" 2>&1
    cmake --build "$OQS_SRC/build" >>"$LOG_FILE" 2>&1
    as_root cmake --install "$OQS_SRC/build" >>"$LOG_FILE" 2>&1
  else
    log "liboqs already installed under $OQS_PREFIX (skipping build)."
  fi

  # Locate liboqs shared library (MUST be a file)
  LIBOQS_SO=""
  for cand in \
    "$OQS_PREFIX/lib/liboqs.so" \
    "$OQS_PREFIX/lib64/liboqs.so" \
    "/usr/local/lib/liboqs.so" \
    "/usr/local/lib64/liboqs.so"
  do
    if [[ -f "$cand" ]]; then
      LIBOQS_SO="$cand"
      break
    fi
  done
  [[ -n "$LIBOQS_SO" ]] || die "Could not find liboqs.so after install (looked in $OQS_PREFIX)"

  log "liboqs.so: $LIBOQS_SO"

  # Install liboqs-python pinned to matching version (ctypes wrapper)
  python -m pip install -U "liboqs-python==${OQS_VERSION}" >>"$LOG_FILE" 2>&1 \
    || die "Failed to install liboqs-python==${OQS_VERSION}"

  # Patch venv activation to export correct env vars (idempotent)
  ACTIVATE="$VENV_DIR/bin/activate"
  MARK_BEGIN="# >>> animica-setup pq env >>>"
  MARK_END="# <<< animica-setup pq env <<<"

  # Remove existing block if present
  if grep -qF "$MARK_BEGIN" "$ACTIVATE"; then
    log "Removing existing PQ env block from venv activate..."
    # portable-ish delete block
    perl -0777 -i -pe "s/\Q$MARK_BEGIN\E.*?\Q$MARK_END\E\n//s" "$ACTIVATE"
  fi

  log "Patching venv activate with PQ env..."
  cat >>"$ACTIVATE" <<EOF

$MARK_BEGIN
# Built by setup.sh --with-pq
export OQS_INSTALL_PATH="$OQS_PREFIX"
export LD_LIBRARY_PATH="$OQS_PREFIX/lib:$OQS_PREFIX/lib64:\${LD_LIBRARY_PATH:-}"
export LIBOQS_PATH="$LIBOQS_SO"
$MARK_END
EOF

  # Re-source to apply in current shell
  # shellcheck disable=SC1090
  source "$ACTIVATE"

  # Verify Dilithium is enabled
  log "Verifying oqs mechanisms..."
  python - <<'PY'
import oqs
mechs = oqs.get_enabled_sig_mechanisms()
print("enabled_sig_mechanisms_count =", len(mechs))
# show a short sample
print("sample =", mechs[:20])
ok = any("DILITHIUM" in m.upper() for m in mechs)
if not ok:
    raise SystemExit("ERROR: Dilithium mechanisms missing. You likely built/loaded a liboqs version without Dilithium.")
print("OK: Dilithium present")
PY
  log "PQ setup OK."
else
  log "PQ setup skipped (run with --with-pq to enable)."
fi

log "================================================================="
log "Done"
log "================================================================="
log "Activate venv: source $VENV_DIR/bin/activate"
