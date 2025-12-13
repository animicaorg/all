#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# =========================
# Config
# =========================
OQS_VERSION="${OQS_VERSION:-0.14.0}"

# =========================
# Helpers
# =========================
log()  { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
err()  { echo "[setup][ERROR] $*" >&2; }
die()  { err "$*"; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS_DIR="${REPO_ROOT}/.deps"
OQS_PREFIX="${DEPS_DIR}/oqs-${OQS_VERSION}"
OQS_SRC="${DEPS_DIR}/src/liboqs-${OQS_VERSION}"
LIBOQSPY_SRC="${DEPS_DIR}/src/liboqs-python"
VENV_DIR="${REPO_ROOT}/.venv"

mkdir -p "${DEPS_DIR}/src"

# =========================
# OS deps (Ubuntu/Debian)
# =========================
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
  warn "apt-get not found; skipping system deps install (ensure: git, cmake, ninja, patchelf, python3-venv, build tools)"
fi

# =========================
# Python venv
# =========================
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

# IMPORTANT: liboqs-python uses hatchling backend; with --no-build-isolation we must have it installed.
log "Installing build backend deps (hatchling) into venv"
python -m pip install -U hatchling build packaging

# =========================
# Build/install liboqs (vendored)
# =========================
if [ -f "${OQS_PREFIX}/lib/liboqs.so" ] || [ -f "${OQS_PREFIX}/lib64/liboqs.so" ]; then
  log "liboqs already installed at ${OQS_PREFIX}"
else
  log "Building liboqs ${OQS_VERSION} into ${OQS_PREFIX}"

  rm -rf "${OQS_SRC}"
  mkdir -p "${OQS_SRC}"

  # Fetch release tarball (most reliable)
  TARBALL_URL="https://github.com/open-quantum-safe/liboqs/archive/refs/tags/${OQS_VERSION}.tar.gz"
  log "Downloading ${TARBALL_URL}"
  curl -fsSL "${TARBALL_URL}" | tar -xz -C "${DEPS_DIR}/src"
  if [ ! -d "${DEPS_DIR}/src/liboqs-${OQS_VERSION}" ]; then
    die "Expected source dir not found: ${DEPS_DIR}/src/liboqs-${OQS_VERSION}"
  fi

  BUILD_DIR="${DEPS_DIR}/src/liboqs-${OQS_VERSION}/build"
  rm -rf "${BUILD_DIR}"
  mkdir -p "${BUILD_DIR}"

  cmake -S "${DEPS_DIR}/src/liboqs-${OQS_VERSION}" -B "${BUILD_DIR}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${OQS_PREFIX}" \
    -DBUILD_SHARED_LIBS=ON

  ninja -C "${BUILD_DIR}"
  ninja -C "${BUILD_DIR}" install

  if [ -f "${OQS_PREFIX}/lib/liboqs.so" ] || [ -f "${OQS_PREFIX}/lib64/liboqs.so" ]; then
    log "liboqs installed OK under ${OQS_PREFIX}"
  else
    die "liboqs install failed; liboqs.so not found under ${OQS_PREFIX}"
  fi
fi

# Make the vendored liboqs visible to the dynamic loader (best-effort).
if [ "$(id -u)" -eq 0 ] && [ -d /etc/ld.so.conf.d ]; then
  CONF_FILE="/etc/ld.so.conf.d/animica-liboqs.conf"
  log "Registering vendored liboqs with ldconfig: ${CONF_FILE}"
  {
    echo "${OQS_PREFIX}/lib"
    echo "${OQS_PREFIX}/lib64"
  } > "${CONF_FILE}"
  ldconfig
else
  warn "Not root or no /etc/ld.so.conf.d; skipping ldconfig registration (LD_LIBRARY_PATH/RPATH will still be used)"
fi

# Export build+runtime env so the liboqs-python build finds the right headers/libs
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

# =========================
# Install liboqs-python from git clone
# =========================
log "Installing liboqs-python from a fresh git clone (linked to vendored liboqs ${OQS_VERSION})"

rm -rf "${LIBOQSPY_SRC}"
git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python "${LIBOQSPY_SRC}"

python -m pip uninstall -y liboqs-python oqs >/dev/null 2>&1 || true

# Build from source, without isolation, so our env is used (and hatchling is present)
python -m pip install --no-build-isolation --no-binary :all: "${LIBOQSPY_SRC}"

# =========================
# Patch the installed extension to force the correct liboqs at runtime
# =========================
log "Patching installed oqs extension RPATH to prefer vendored liboqs"
OQS_EXT_PATH="$(
python - <<'PY'
import pathlib
import oqs as pkg
pkg_dir = pathlib.Path(pkg.__file__).resolve().parent
cands = list(pkg_dir.glob("*.so"))
if not cands:
    cands = list(pkg_dir.glob("**/*.so"))
print(str(cands[0]) if cands else "", end="")
PY
)" || die "Failed importing oqs after install"

if [ -z "${OQS_EXT_PATH}" ] || [ ! -f "${OQS_EXT_PATH}" ]; then
  die "Could not locate installed oqs extension .so for RPATH patch"
fi

if ! have_cmd patchelf; then
  die "patchelf not available; install it (apt-get install patchelf) and rerun"
fi

patchelf --set-rpath "${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64" "${OQS_EXT_PATH}" || true

log "ldd of oqs extension (liboqs should resolve to ${OQS_PREFIX})"
ldd "${OQS_EXT_PATH}" | grep -E "liboqs\.so|not found" || true

# =========================
# Install animica + ensure `animica` works without ./animica
# =========================
log "Installing Animica into the venv (editable)"

if [ -f "${REPO_ROOT}/python/pyproject.toml" ] || [ -f "${REPO_ROOT}/python/setup.py" ]; then
  python -m pip install -e "${REPO_ROOT}/python"
elif [ -f "${REPO_ROOT}/pyproject.toml" ] || [ -f "${REPO_ROOT}/setup.py" ]; then
  python -m pip install -e "${REPO_ROOT}"
else
  warn "Could not find python package metadata in ./python or repo root; skipping animica install"
fi

# Wrap the venv animica entrypoint so it always exports the right LD_LIBRARY_PATH
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
  warn "No venv animica entrypoint found at ${ANIMICA_BIN} (install may have failed or package layout differs)"
fi

# Optional: system shim so `animica` works without activating venv
if [ "$(id -u)" -eq 0 ] && [ -d /usr/local/bin ] && [ -x "${ANIMICA_BIN}" ]; then
  if [ -e /usr/local/bin/animica ] && [ ! -L /usr/local/bin/animica ]; then
    warn "/usr/local/bin/animica exists and is not a symlink; not overwriting."
  else
    log "Creating /usr/local/bin/animica shim -> ${ANIMICA_BIN}"
    ln -sf "${ANIMICA_BIN}" /usr/local/bin/animica
  fi
fi

# =========================
# Verify versions
# =========================
log "Verifying oqs + liboqs versions (should NOT warn and liboqs should be ${OQS_VERSION})"
python - <<PY
import oqs
print("oqs_python_version:", getattr(oqs, "oqs_python_version", lambda: "unknown")())
print("oqs_version:", getattr(oqs, "oqs_version", lambda: "unknown")())
PY

log "Done."
log "Usage:"
log "  source .venv/bin/activate && animica --help"
if [ -x /usr/local/bin/animica ]; then
  log "  (or just) animica --help"
fi
