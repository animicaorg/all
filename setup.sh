#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

OQS_VERSION="${OQS_VERSION:-0.14.0}"

log()  { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
err()  { echo "[setup][ERROR] $*" >&2; }
die()  { err "$*"; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS_DIR="${REPO_ROOT}/.deps"
OQS_PREFIX="${DEPS_DIR}/oqs-${OQS_VERSION}"
VENV_DIR="${REPO_ROOT}/.venv"
SRC_DIR="${DEPS_DIR}/src"
LIBOQSPY_SRC="${SRC_DIR}/liboqs-python"

mkdir -p "${SRC_DIR}"

# -------------------------
# System deps (Ubuntu/Debian)
# -------------------------
if have_cmd apt-get; then
  log "Installing system build deps via apt-get (idempotent)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null
  apt-get install -y --no-install-recommends \
    ca-certificates curl git build-essential pkg-config \
    cmake ninja-build \
    python3 python3-venv python3-dev \
    patchelf \
    >/dev/null
else
  warn "apt-get not found; skipping system deps install"
fi

# -------------------------
# Python venv
# -------------------------
if [ ! -d "${VENV_DIR}" ]; then
  log "Creating venv at ${VENV_DIR}"
  if have_cmd python3.12; then
    python3.12 -m venv "${VENV_DIR}"
  else
    python3 -m venv "${VENV_DIR}"
  fi
else
  log "Venv already exists at ${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

log "Upgrading pip/setuptools/wheel"
python -m pip install -U pip setuptools wheel

# liboqs-python uses hatchling backend; we install it because we use --no-build-isolation.
log "Installing build backend deps (hatchling) into venv"
python -m pip install -U hatchling build packaging

# -------------------------
# Build/install liboqs (vendored)
# -------------------------
if [ -f "${OQS_PREFIX}/lib/liboqs.so" ] || [ -f "${OQS_PREFIX}/lib64/liboqs.so" ] || [ -f "${OQS_PREFIX}/lib/liboqs.so.0" ] || [ -f "${OQS_PREFIX}/lib64/liboqs.so.0" ]; then
  log "liboqs already installed at ${OQS_PREFIX}"
else
  log "Building liboqs ${OQS_VERSION} into ${OQS_PREFIX}"

  rm -rf "${SRC_DIR}/liboqs-${OQS_VERSION}"
  TARBALL_URL="https://github.com/open-quantum-safe/liboqs/archive/refs/tags/${OQS_VERSION}.tar.gz"
  log "Downloading ${TARBALL_URL}"
  curl -fsSL "${TARBALL_URL}" | tar -xz -C "${SRC_DIR}"

  [ -d "${SRC_DIR}/liboqs-${OQS_VERSION}" ] || die "Missing source dir ${SRC_DIR}/liboqs-${OQS_VERSION}"

  BUILD_DIR="${SRC_DIR}/liboqs-${OQS_VERSION}/build"
  rm -rf "${BUILD_DIR}"
  mkdir -p "${BUILD_DIR}"

  cmake -S "${SRC_DIR}/liboqs-${OQS_VERSION}" -B "${BUILD_DIR}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${OQS_PREFIX}" \
    -DBUILD_SHARED_LIBS=ON

  ninja -C "${BUILD_DIR}"
  ninja -C "${BUILD_DIR}" install
fi

# IMPORTANT: Ensure SONAME-style symlinks exist (liboqs-python commonly needs liboqs.so.0)
fix_soname_links() {
  local d="$1"
  [ -d "$d" ] || return 0

  if [ -f "$d/liboqs.so" ] && [ ! -e "$d/liboqs.so.0" ]; then
    ln -sf "liboqs.so" "$d/liboqs.so.0"
  fi

  # Some builds produce only versioned file; try to link it to .so.0 and .so
  local verfile=""
  verfile="$(ls -1 "$d"/liboqs.so.* 2>/dev/null | head -n 1 || true)"
  if [ -n "$verfile" ]; then
    local base
    base="$(basename "$verfile")"
    [ -e "$d/liboqs.so" ]   || ln -sf "$base" "$d/liboqs.so"
    [ -e "$d/liboqs.so.0" ] || ln -sf "$base" "$d/liboqs.so.0"
  fi
}
fix_soname_links "${OQS_PREFIX}/lib"
fix_soname_links "${OQS_PREFIX}/lib64"

if [ -f "${OQS_PREFIX}/lib/liboqs.so" ] || [ -f "${OQS_PREFIX}/lib64/liboqs.so" ] || [ -f "${OQS_PREFIX}/lib/liboqs.so.0" ] || [ -f "${OQS_PREFIX}/lib64/liboqs.so.0" ]; then
  log "liboqs installed OK: ${OQS_PREFIX}"
else
  die "liboqs install failed; no liboqs shared library found under ${OQS_PREFIX}/lib*"
fi

# Best-effort ldconfig registration (not required if LD_LIBRARY_PATH/RPATH works)
if [ "$(id -u)" -eq 0 ] && [ -d /etc/ld.so.conf.d ]; then
  CONF_FILE="/etc/ld.so.conf.d/animica-liboqs.conf"
  log "Registering vendored liboqs with ldconfig: ${CONF_FILE}"
  {
    echo "${OQS_PREFIX}/lib"
    echo "${OQS_PREFIX}/lib64"
  } > "${CONF_FILE}"
  ldconfig || true
fi

# Export env so builds & runtime see vendored liboqs first
export OQS_ROOT="${OQS_PREFIX}"
export CMAKE_PREFIX_PATH="${OQS_PREFIX}:${CMAKE_PREFIX_PATH:-}"
export PKG_CONFIG_PATH="${OQS_PREFIX}/lib/pkgconfig:${OQS_PREFIX}/lib64/pkgconfig:${PKG_CONFIG_PATH:-}"
export LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:${LIBRARY_PATH:-}"
export CPATH="${OQS_PREFIX}/include:${CPATH:-}"
export CFLAGS="-I${OQS_PREFIX}/include ${CFLAGS:-}"
export LDFLAGS="-L${OQS_PREFIX}/lib -L${OQS_PREFIX}/lib64 ${LDFLAGS:-}"
export CMAKE_ARGS="${CMAKE_ARGS:-} -DCMAKE_PREFIX_PATH=${OQS_PREFIX}"
export SKBUILD_CONFIGURE_OPTIONS="${SKBUILD_CONFIGURE_OPTIONS:-} -DCMAKE_PREFIX_PATH=${OQS_PREFIX}"

# -------------------------
# Install liboqs-python from git clone
# -------------------------
log "Installing liboqs-python from a fresh git clone (linked to vendored liboqs ${OQS_VERSION})"

rm -rf "${LIBOQSPY_SRC}"
git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python "${LIBOQSPY_SRC}"

# Remove any conflicting pip packages
python -m pip uninstall -y liboqs-python oqs >/dev/null 2>&1 || true

# Build from source, no isolation (uses our env; hatchling is installed above)
python -m pip install --no-build-isolation --no-binary :all: "${LIBOQSPY_SRC}"

# -------------------------
# Locate oqs extension reliably + patch RPATH
# -------------------------
if ! have_cmd patchelf; then
  die "patchelf not available; install it (apt-get install patchelf) and rerun"
fi

log "Locating oqs extension module path"
OQS_EXT_PATH="$(
  env LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64" \
  python - <<'PY'
import importlib.util
spec = importlib.util.find_spec("oqs.oqs")
print(spec.origin if spec and spec.origin else "", end="")
PY
)"

if [ -z "${OQS_EXT_PATH}" ] || [ ! -f "${OQS_EXT_PATH}" ]; then
  err "Could not find oqs.oqs extension via importlib"
  err "Debug listing of likely locations:"
  python - <<'PY'
import site, pathlib
paths = []
for p in site.getsitepackages():
    paths.append(pathlib.Path(p))
for p in paths:
    if p.exists():
        for cand in p.rglob("oqs*.so"):
            print(cand)
        for cand in p.rglob("*/oqs/*.so"):
            print(cand)
PY
  die "Could not locate installed oqs extension .so for RPATH patch"
fi

log "Found oqs extension: ${OQS_EXT_PATH}"
log "Patching oqs extension RPATH to prefer vendored liboqs"
patchelf --force-rpath --set-rpath "${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64" "${OQS_EXT_PATH}" || true

log "ldd of oqs extension (liboqs should resolve into ${OQS_PREFIX})"
ldd "${OQS_EXT_PATH}" | grep -E "liboqs\.so|not found" || true

# Verify runtime liboqs version (should be 0.14.x)
log "Verifying oqs runtime liboqs version"
env LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64" \
python - <<'PY'
import oqs
v = oqs.oqs_version() if hasattr(oqs, "oqs_version") else "unknown"
print("oqs_version():", v)
PY

# -------------------------
# Install animica so `animica` works (no ./animica)
# -------------------------
log "Installing Animica into the venv (editable)"
if [ -f "${REPO_ROOT}/python/pyproject.toml" ] || [ -f "${REPO_ROOT}/python/setup.py" ]; then
  python -m pip install -e "${REPO_ROOT}/python"
elif [ -f "${REPO_ROOT}/pyproject.toml" ] || [ -f "${REPO_ROOT}/setup.py" ]; then
  python -m pip install -e "${REPO_ROOT}"
else
  warn "Could not find python package metadata in ./python or repo root; skipping animica install"
fi

# Wrap venv animica to always force the vendored liboqs path at runtime
ANIMICA_BIN="${VENV_DIR}/bin/animica"
if [ -x "${ANIMICA_BIN}" ]; then
  if [ ! -f "${VENV_DIR}/bin/animica.real" ]; then
    log "Wrapping venv animica entrypoint to force vendored liboqs at runtime"
    mv "${ANIMICA_BIN}" "${VENV_DIR}/bin/animica.real"
  fi

  cat > "${ANIMICA_BIN}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:\${LD_LIBRARY_PATH:-}"
exec "${VENV_DIR}/bin/animica.real" "\$@"
EOF
  chmod +x "${ANIMICA_BIN}"
else
  warn "No venv animica entrypoint found at ${ANIMICA_BIN}"
fi

# Optional system shim so `animica` works without venv activation
if [ "$(id -u)" -eq 0 ] && [ -d /usr/local/bin ] && [ -x "${ANIMICA_BIN}" ]; then
  if [ -e /usr/local/bin/animica ] && [ ! -L /usr/local/bin/animica ]; then
    warn "/usr/local/bin/animica exists and is not a symlink; not overwriting."
  else
    log "Creating /usr/local/bin/animica shim -> ${ANIMICA_BIN}"
    ln -sf "${ANIMICA_BIN}" /usr/local/bin/animica
  fi
fi

log "Done."
log "Usage:"
log "  source .venv/bin/activate && animica --help"
if [ -x /usr/local/bin/animica ]; then
  log "  (or just) animica --help"
fi
