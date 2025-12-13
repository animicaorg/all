#!/usr/bin/env bash
set -euo pipefail

log() { echo "[setup] $(date -u +%FT%TZ) $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
die() { echo "[setup][ERROR] $*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS_DIR="$ROOT/.deps"
SRC_DIR="$DEPS_DIR/src"

LIBOQS_VERSION="0.14.0"
LIBOQS_PREFIX="$DEPS_DIR/oqs-$LIBOQS_VERSION"
LIBOQS_LIBDIR="$LIBOQS_PREFIX/lib"

VENV_DIR="$ROOT/.venv"

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

install_system_deps() {
  if ! have apt-get; then
    warn "apt-get not found; skipping system deps install."
    return
  fi

  log "Installing system build deps via apt-get (idempotent)"
  $SUDO apt-get update -y
  $SUDO apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    build-essential pkg-config \
    cmake ninja-build \
    python3 python3-venv python3-dev \
    libssl-dev \
    patchelf \
    unzip

  # Nice-to-have for diagnosing loader issues
  $SUDO apt-get install -y --no-install-recommends \
    binutils || true
}

ensure_venv() {
  if [ ! -d "$VENV_DIR" ]; then
    log "Creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  else
    log "Venv already exists at $VENV_DIR"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  log "Upgrading pip/setuptools/wheel + build backends"
  python -m pip install -U pip setuptools wheel hatchling
}

build_and_install_liboqs() {
  if [ -f "$LIBOQS_LIBDIR/liboqs.so.$LIBOQS_VERSION" ] || [ -f "$LIBOQS_LIBDIR/liboqs.so.0.$LIBOQS_VERSION" ]; then
    log "liboqs already installed at $LIBOQS_PREFIX"
  else
    log "Building liboqs $LIBOQS_VERSION into $LIBOQS_PREFIX"
    rm -rf "$SRC_DIR/liboqs"
    mkdir -p "$SRC_DIR/liboqs"

    # Prefer tag/branch clone; fall back gracefully if needed
    if git clone --depth=1 --branch "$LIBOQS_VERSION" https://github.com/open-quantum-safe/liboqs "$SRC_DIR/liboqs" 2>/dev/null; then
      :
    else
      warn "Could not clone liboqs tag/branch '$LIBOQS_VERSION' with depth=1; cloning default branch then checking out tag."
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

    if [ ! -d "$LIBOQS_LIBDIR" ]; then
      die "liboqs install finished but $LIBOQS_LIBDIR not found"
    fi
    log "liboqs installed OK: $LIBOQS_LIBDIR/liboqs.so*"
  fi

  # Make loader prefer vendored liboqs. (This is important because liboqs-python
  # will otherwise try to detect liboqs at runtime and may download a different one.) :contentReference[oaicite:2]{index=2}
  export OQS_INSTALL_PATH="$LIBOQS_PREFIX"
  export LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

  # Also register with ldconfig when possible (helps outside of the current shell)
  if [ -n "$SUDO" ] || [ "${EUID:-$(id -u)}" -eq 0 ]; then
    log "Registering vendored liboqs with ldconfig: /etc/ld.so.conf.d/animica-liboqs.conf"
    $SUDO bash -c "cat > /etc/ld.so.conf.d/animica-liboqs.conf <<EOF
$LIBOQS_LIBDIR
EOF"
    $SUDO ldconfig || true
  else
    warn "Not root; skipping ldconfig registration. LD_LIBRARY_PATH will still be used for this shell."
  fi
}

install_liboqs_python() {
  # Ensure we don't have the unrelated PyPI 'oqs' package hanging around.
  # We want the liboqs-python 'oqs' package.
  log "Uninstalling any existing oqs/liboqs-python packages (best effort)"
  python -m pip uninstall -y oqs liboqs-python >/dev/null 2>&1 || true

  log "Installing liboqs-python from a fresh git clone (linked to vendored liboqs $LIBOQS_VERSION)"
  rm -rf "$SRC_DIR/liboqs-python"
  git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python "$SRC_DIR/liboqs-python"

  # IMPORTANT: set OQS_INSTALL_PATH so liboqs-python uses our installed liboqs
  # instead of triggering its runtime auto-download behavior. :contentReference[oaicite:3]{index=3}
  (
    cd "$SRC_DIR/liboqs-python"
    OQS_INSTALL_PATH="$LIBOQS_PREFIX" \
    LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    python -m pip install .
  )

  # Verify import with warnings treated as errors.
  # If this fails, it means it's still not loading vendored liboqs.
  log "Verifying oqs loads without liboqs version mismatch warnings"
  OQS_INSTALL_PATH="$LIBOQS_PREFIX" \
  LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  python -W error -c "import oqs" \
    || die "oqs import raised warnings/errors. To debug, run: LD_DEBUG=libs OQS_INSTALL_PATH='$LIBOQS_PREFIX' LD_LIBRARY_PATH='$LIBOQS_LIBDIR' python -c 'import oqs'"
}

install_animica_python() {
  log "Installing Animica python package (editable) so 'animica' command exists"

  # Try common layouts
  if [ -f "$ROOT/python/pyproject.toml" ] || [ -f "$ROOT/python/setup.py" ]; then
    python -m pip install -e "$ROOT/python"
  elif [ -f "$ROOT/pyproject.toml" ] || [ -f "$ROOT/setup.py" ]; then
    python -m pip install -e "$ROOT"
  else
    warn "Could not find pyproject.toml/setup.py at repo root or ./python; skipping pip install -e. You may need to install manually."
  fi

  # Sanity check command existence inside venv
  if [ -x "$VENV_DIR/bin/animica" ]; then
    log "animica entrypoint installed in venv: $VENV_DIR/bin/animica"
  else
    warn "animica entrypoint not found at $VENV_DIR/bin/animica (install may still have succeeded under a different entrypoint)"
  fi
}

patch_venv_activate_env() {
  # Make sure that *when you activate the venv*, it always prefers vendored liboqs.
  local ACT="$VENV_DIR/bin/activate"
  if [ -f "$ACT" ] && ! grep -q "ANIMICA_OQS_ENV_BEGIN" "$ACT"; then
    log "Patching venv activate to export OQS_INSTALL_PATH + LD_LIBRARY_PATH"
    cat >> "$ACT" <<EOF

# ANIMICA_OQS_ENV_BEGIN
# Force liboqs-python to use Animica's vendored liboqs (avoids runtime auto-download / mismatches)
export OQS_INSTALL_PATH="$LIBOQS_PREFIX"
export LD_LIBRARY_PATH="$LIBOQS_LIBDIR:\${LD_LIBRARY_PATH:-}"
# ANIMICA_OQS_ENV_END
EOF
  fi
}

install_global_animica_wrapper() {
  # This is what stops you needing "./animica" or "source .venv/bin/activate" just to run the CLI.
  local TARGET="/usr/local/bin/animica"

  if [ -n "$SUDO" ] || [ "${EUID:-$(id -u)}" -eq 0 ]; then
    log "Installing global wrapper at $TARGET"
    $SUDO bash -c "cat > '$TARGET' <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT=\"$ROOT\"
VENV=\"$ROOT/.venv\"

LIBOQS_PREFIX=\"$LIBOQS_PREFIX\"
LIBOQS_LIBDIR=\"$LIBOQS_LIBDIR\"

export OQS_INSTALL_PATH=\"$LIBOQS_PREFIX\"
export LD_LIBRARY_PATH=\"$LIBOQS_LIBDIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"

if [ -x \"\$VENV/bin/animica\" ]; then
  exec \"\$VENV/bin/animica\" \"\$@\"
else
  exec \"\$VENV/bin/python\" -m animica \"\$@\"
fi
EOF"
    $SUDO chmod +x "$TARGET"
  else
    warn "Not root; cannot write $TARGET. You can still run: source .venv/bin/activate && animica ..."
  fi
}

main() {
  install_system_deps
  ensure_venv
  build_and_install_liboqs
  install_liboqs_python
  install_animica_python
  patch_venv_activate_env
  install_global_animica_wrapper

  log "Done."
  log "Try: animica --help"
  log "If you want to stay in this shell venv: source .venv/bin/activate"
}

main "$@"
