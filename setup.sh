#!/usr/bin/env bash
set -euo pipefail

log()  { echo "[setup] $(date -u +%FT%TZ) $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
die()  { echo "[setup][ERROR] $*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS_DIR="$ROOT/.deps"
SRC_DIR="$DEPS_DIR/src"

VENV_DIR="$ROOT/.venv"

LIBOQS_VERSION="0.14.0"
LIBOQS_PREFIX="$DEPS_DIR/oqs-$LIBOQS_VERSION"
LIBOQS_LIBDIR="$LIBOQS_PREFIX/lib"

# Use sudo when not root
SUDO=""
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  if have sudo; then
    SUDO="sudo"
  else
    die "This script needs root privileges (or sudo)."
  fi
fi

mkdir -p "$SRC_DIR"

fresh_nuke_repo_state() {
  log "Fresh-mode: removing repo venv/deps (like a brand new Ubuntu box)"
  rm -rf "$VENV_DIR" "$DEPS_DIR"
  mkdir -p "$SRC_DIR"
}

backup_system_liboqs_if_any() {
  # The mismatch you're seeing (0.15.0) usually happens because an older/newer liboqs
  # was installed globally (often in /usr/local/lib) and is being picked up at runtime.
  # For "fresh ubuntu" behavior, we back it up out of the way.
  local backup_dir="$ROOT/.deps_system_liboqs_backup_$(date -u +%Y%m%dT%H%M%SZ)"
  local found=0

  # candidate directories
  local dirs=(/usr/local/lib /usr/local/lib64 /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu)

  for d in "${dirs[@]}"; do
    [ -d "$d" ] || continue
    if ls "$d"/liboqs.so* >/dev/null 2>&1; then
      found=1
      mkdir -p "$backup_dir$d"
      log "Backing up system liboqs from $d -> $backup_dir$d"
      $SUDO bash -c "mv -f $d/liboqs.so* '$backup_dir$d/'" || true
    fi
  done

  if [ "$found" -eq 1 ]; then
    log "System liboqs backup done. Running ldconfig."
    $SUDO ldconfig || true
    log "If you ever need to restore, copy files back from: $backup_dir"
  else
    log "No system liboqs.so* found in common locations (good)."
  fi
}

install_system_deps() {
  if ! have apt-get; then
    warn "apt-get not found; skipping system deps install."
    return
  fi

  log "Installing system build deps via apt-get (fresh/ubuntu24-style)"
  $SUDO apt-get update -y
  $SUDO apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    build-essential pkg-config \
    cmake ninja-build \
    python3 python3-venv python3-dev \
    libssl-dev \
    patchelf \
    unzip \
    binutils

  # optional but useful
  $SUDO apt-get install -y --no-install-recommends \
    libffi-dev || true
}

ensure_venv() {
  log "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  log "Upgrading pip/setuptools/wheel + build backends"
  python -m pip install -U pip setuptools wheel build hatchling
}

build_and_install_liboqs() {
  log "Building liboqs $LIBOQS_VERSION into $LIBOQS_PREFIX"

  rm -rf "$SRC_DIR/liboqs"
  mkdir -p "$SRC_DIR/liboqs"

  # Try a lightweight clone of the version tag/branch first; fall back to full clone.
  if git clone --depth=1 --branch "$LIBOQS_VERSION" https://github.com/open-quantum-safe/liboqs "$SRC_DIR/liboqs" 2>/dev/null; then
    :
  else
    warn "Could not shallow-clone liboqs '$LIBOQS_VERSION'. Doing full clone + checkout."
    rm -rf "$SRC_DIR/liboqs"
    git clone https://github.com/open-quantum-safe/liboqs "$SRC_DIR/liboqs"
    ( cd "$SRC_DIR/liboqs" && git checkout -q "$LIBOQS_VERSION" )
  fi

  cmake -S "$SRC_DIR/liboqs" -B "$SRC_DIR/liboqs/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON \
    -DCMAKE_INSTALL_PREFIX="$LIBOQS_PREFIX"

  cmake --build "$SRC_DIR/liboqs/build" --parallel "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
  cmake --install "$SRC_DIR/liboqs/build"

  if [ ! -f "$LIBOQS_LIBDIR/liboqs.so" ] && [ ! -f "$LIBOQS_LIBDIR/liboqs.so.$LIBOQS_VERSION" ]; then
    die "liboqs install finished but $LIBOQS_LIBDIR/liboqs.so* not found"
  fi

  log "liboqs installed OK: $LIBOQS_LIBDIR/liboqs.so*"

  log "Registering vendored liboqs with ldconfig: /etc/ld.so.conf.d/00-animica-liboqs.conf"
  $SUDO bash -c "cat > /etc/ld.so.conf.d/00-animica-liboqs.conf <<EOF
$LIBOQS_LIBDIR
EOF"
  $SUDO ldconfig || true
}

install_liboqs_python_014_from_git() {
  # We always reinstall from scratch (fresh mode).
  log "Installing liboqs-python from a fresh git clone (expecting version 0.14.0)"

  rm -rf "$SRC_DIR/liboqs-python"
  git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python "$SRC_DIR/liboqs-python"

  # IMPORTANT:
  # - liboqs-python is the package, but it installs import name "oqs"
  # - we force it to use OUR liboqs by setting OQS_INSTALL_PATH at build/install time
  (
    cd "$SRC_DIR/liboqs-python"
    OQS_INSTALL_PATH="$LIBOQS_PREFIX" \
    LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    python -m pip install .
  )

  # Verify installed version equals 0.14.0 (fail hard otherwise)
  log "Verifying installed liboqs-python version is exactly 0.14.0"
  python - <<'PY'
import sys
from importlib.metadata import version, PackageNotFoundError
try:
  v = version("liboqs-python")
except PackageNotFoundError:
  print("ERROR: liboqs-python not found in metadata")
  sys.exit(2)
print("liboqs-python version:", v)
if v.strip() != "0.14.0":
  print("ERROR: Expected liboqs-python==0.14.0")
  sys.exit(3)
PY
}

install_sitecustomize_preload_liboqs() {
  # This is the BIG FIX:
  # Some environments keep finding a globally-installed liboqs (0.15.0) at runtime.
  # By preloading our vendored liboqs with RTLD_GLOBAL on Python startup, we force
  # subsequent loads to use the already-loaded library.
  #
  # sitecustomize.py is auto-imported by Python on startup if present on sys.path.
  log "Installing venv sitecustomize.py to preload vendored liboqs (forces 0.14.0 at runtime)"

  local sp
  sp="$(python - <<'PY'
import site, sys
paths = site.getsitepackages()
print(paths[0] if paths else "")
PY
)"
  [ -n "$sp" ] || die "Could not resolve site-packages path"

  cat > "$sp/sitecustomize.py" <<PY
# Auto-loaded by Python on startup (unless -S). We use it to force vendored liboqs.
import os
import ctypes

prefix = os.environ.get("OQS_INSTALL_PATH", "").strip()
if prefix:
    candidates = [
        os.path.join(prefix, "lib", "liboqs.so"),
        os.path.join(prefix, "lib64", "liboqs.so"),
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                mode = getattr(ctypes, "RTLD_GLOBAL", 0)
                ctypes.CDLL(p, mode=mode)
            except Exception:
                # Don't block Python startup; worst case oqs import will still warn and setup will fail later.
                pass
            break
PY
}

patch_venv_activate_env() {
  local ACT="$VENV_DIR/bin/activate"
  log "Patching venv activate to always export OQS_INSTALL_PATH + LD_LIBRARY_PATH"

  cat >> "$ACT" <<EOF

# ANIMICA_OQS_ENV_BEGIN
export OQS_INSTALL_PATH="$LIBOQS_PREFIX"
export LD_LIBRARY_PATH="$LIBOQS_LIBDIR:\${LD_LIBRARY_PATH:-}"
# ANIMICA_OQS_ENV_END
EOF
}

verify_oqs_no_mismatch_warning() {
  log "Verifying: importing oqs MUST NOT warn about liboqs 0.15.x"
  OQS_INSTALL_PATH="$LIBOQS_PREFIX" \
  LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  python -W error -c "import oqs" \
    || die "oqs import still raised a warning/error. A system liboqs is still being loaded somehow."
}

install_animica_python() {
  log "Installing Animica so 'animica' command exists (editable install)"
  # Try ./python first (common in your repo), then root.
  if [ -f "$ROOT/python/pyproject.toml" ] || [ -f "$ROOT/python/setup.py" ]; then
    python -m pip install -e "$ROOT/python"
  elif [ -f "$ROOT/pyproject.toml" ] || [ -f "$ROOT/setup.py" ]; then
    python -m pip install -e "$ROOT"
  else
    warn "Could not find pyproject.toml/setup.py at repo root or ./python; skipping pip install -e."
  fi
}

install_global_animica_wrapper() {
  local TARGET="/usr/local/bin/animica"
  log "Installing /usr/local/bin/animica wrapper (so you don't need ./animica)"

  $SUDO bash -c "cat > '$TARGET' <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT=\"$ROOT\"
VENV=\"\$ROOT/.venv\"

LIBOQS_PREFIX=\"$LIBOQS_PREFIX\"
LIBOQS_LIBDIR=\"$LIBOQS_LIBDIR\"

export OQS_INSTALL_PATH=\"\$LIBOQS_PREFIX\"
export LD_LIBRARY_PATH=\"\$LIBOQS_LIBDIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"

if [ -x \"\$VENV/bin/animica\" ]; then
  exec \"\$VENV/bin/animica\" \"\$@\"
else
  exec \"\$VENV/bin/python\" -m animica \"\$@\"
fi
EOF"
  $SUDO chmod +x "$TARGET"
}

main() {
  # Always fresh: you explicitly asked to reinstall EVERYTHING each time.
  fresh_nuke_repo_state

  install_system_deps

  # For truly "fresh ubuntu" behavior: move any global liboqs out of the way.
  # This directly targets the 0.15.0 mismatch you're seeing.
  backup_system_liboqs_if_any

  ensure_venv
  # venv is active from ensure_venv

  build_and_install_liboqs

  # Ensure our env is set during all subsequent steps
  export OQS_INSTALL_PATH="$LIBOQS_PREFIX"
  export LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

  install_liboqs_python_014_from_git
  install_sitecustomize_preload_liboqs
  patch_venv_activate_env

  # Now that sitecustomize is in place, verify the mismatch is gone
  verify_oqs_no_mismatch_warning

  install_animica_python
  install_global_animica_wrapper

  log "Done."
  log "Try: animica --help"
  log "If you want the venv in your shell: source .venv/bin/activate"
}

main "$@"
