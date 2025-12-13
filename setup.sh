#!/usr/bin/env bash
set -euo pipefail

# Animica setup script
# - Builds & pins liboqs 0.14.0 into repo-local .deps/
# - Installs liboqs-python (NOT the unrelated PyPI "oqs") and forces it to use the pinned liboqs
# - Installs Animica CLI into a venv so `animica` works (no ./animica required)
# - Idempotent: safe to re-run

log() { printf "[setup] %s %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*"; }
warn() { printf "[setup][WARN] %s\n" "$*" >&2; }
die() { printf "[setup][ERROR] %s\n" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# ---- Versions you want pinned ----
LIBOQS_VERSION="${LIBOQS_VERSION:-0.14.0}"
# liboqs-python versions usually track liboqs versions; keep aligned.
LIBOQS_PY_VERSION="${LIBOQS_PY_VERSION:-0.14.0}"

DEPS_DIR="$ROOT_DIR/.deps"
SRC_DIR="$DEPS_DIR/src"
PREFIX="$DEPS_DIR/oqs-$LIBOQS_VERSION"

LIBOQS_GIT_URL="${LIBOQS_GIT_URL:-https://github.com/open-quantum-safe/liboqs.git}"
LIBOQS_PY_GIT_URL="${LIBOQS_PY_GIT_URL:-https://github.com/open-quantum-safe/liboqs-python.git}"

mkdir -p "$DEPS_DIR" "$SRC_DIR"

# ---- OS deps (Ubuntu/Debian) ----
install_apt_deps() {
  if ! have apt-get; then
    warn "apt-get not found; skipping system dependency install."
    return 0
  fi

  local pkgs=(
    git
    ca-certificates
    build-essential
    pkg-config
    cmake
    ninja-build
    libssl-dev
    python3-venv
    python3-pip
  )

  if [ "$(id -u)" -ne 0 ]; then
    if have sudo; then
      log "Installing system deps with sudo apt-get (if needed)..."
      sudo apt-get update -y
      sudo apt-get install -y "${pkgs[@]}"
    else
      warn "Not root and sudo not found; cannot install apt dependencies automatically."
    fi
  else
    log "Installing system deps with apt-get (if needed)..."
    apt-get update -y
    apt-get install -y "${pkgs[@]}"
  fi
}

# ---- Build liboqs into repo-local prefix ----
build_liboqs() {
  local needed=0
  if [ ! -f "$PREFIX/lib/liboqs.so" ] && [ ! -f "$PREFIX/lib64/liboqs.so" ]; then
    needed=1
  fi
  if [ ! -d "$PREFIX/include/oqs" ]; then
    needed=1
  fi

  if [ "$needed" -eq 0 ]; then
    log "liboqs $LIBOQS_VERSION already present at $PREFIX; skipping build."
    return 0
  fi

  local src="$SRC_DIR/liboqs-$LIBOQS_VERSION"
  log "Building liboqs $LIBOQS_VERSION into $PREFIX"
  rm -rf "$src"
  git clone --depth 1 --branch "$LIBOQS_VERSION" "$LIBOQS_GIT_URL" "$src"

  local gen="Unix Makefiles"
  if have ninja; then gen="Ninja"; fi

  cmake -S "$src" -B "$src/build" -G "$gen" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON \
    -DOQS_USE_OPENSSL=ON \
    -DCMAKE_INSTALL_PREFIX="$PREFIX"

  cmake --build "$src/build" -- -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
  cmake --install "$src/build"

  # Sanity check
  local so=""
  if [ -f "$PREFIX/lib/liboqs.so" ]; then so="$PREFIX/lib/liboqs.so"; fi
  if [ -z "$so" ] && [ -f "$PREFIX/lib64/liboqs.so" ]; then so="$PREFIX/lib64/liboqs.so"; fi
  [ -n "$so" ] || die "liboqs build finished but liboqs.so not found under $PREFIX/lib or $PREFIX/lib64"
  log "liboqs installed OK: $so"
}

# ---- Create venv ----
ensure_venv() {
  local py="${PYTHON:-}"
  if [ -z "$py" ]; then
    if have python3.12; then py="python3.12"
    elif have python3; then py="python3"
    else die "Python not found (need python3)."
    fi
  fi

  if [ ! -d "$ROOT_DIR/.venv" ]; then
    log "Creating venv at $ROOT_DIR/.venv using $py"
    "$py" -m venv "$ROOT_DIR/.venv"
  else
    log "Venv already exists at $ROOT_DIR/.venv"
  fi

  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"

  log "Upgrading pip/setuptools/wheel"
  python -m pip install --upgrade pip setuptools wheel
}

# ---- Ensure activate exports correct OQS paths (and does NOT pick system 0.15.x) ----
rewrite_activate_block() {
  local act="$ROOT_DIR/.venv/bin/activate"
  [ -f "$act" ] || die "Venv activate not found at $act"

  local start="# >>> ANIMICA OQS ENV >>>"
  local end="# <<< ANIMICA OQS ENV <<<"

  local tmp
  tmp="$(mktemp)"

  # Remove old block if present
  awk -v start="$start" -v end="$end" '
    $0==start {inblock=1; next}
    $0==end {inblock=0; next}
    inblock!=1 {print}
  ' "$act" > "$tmp"

  # Append fresh block
  {
    echo ""
    echo "$start"
    echo "export OQS_INSTALL_PATH=\"$PREFIX\""
    echo 'export LD_LIBRARY_PATH="$OQS_INSTALL_PATH/lib:$OQS_INSTALL_PATH/lib64:${LD_LIBRARY_PATH:-}"'
    echo "$end"
    echo ""
  } >> "$tmp"

  mv "$tmp" "$act"
  chmod +x "$act"
}

# ---- Install liboqs-python correctly (NOT the unrelated PyPI "oqs") ----
install_liboqs_python() {
  # We force a source build so it links against our pinned liboqs.
  # IMPORTANT: Do NOT "pip install oqs==..." — that is the wrong package.
  export OQS_INSTALL_PATH="$PREFIX"
  export LD_LIBRARY_PATH="$OQS_INSTALL_PATH/lib:$OQS_INSTALL_PATH/lib64:${LD_LIBRARY_PATH:-}"

  log "Installing liboqs-python $LIBOQS_PY_VERSION (forcing source build, linked to $OQS_INSTALL_PATH)"

  # First try PyPI pinned version
  if python -m pip install --no-binary :all: --no-build-isolation "liboqs-python==${LIBOQS_PY_VERSION}"; then
    log "Installed liboqs-python==${LIBOQS_PY_VERSION} from PyPI"
    return 0
  fi

  warn "PyPI install failed for liboqs-python==${LIBOQS_PY_VERSION}. Falling back to Git install."
  # Fallback to installing from Git tag/branch
  python -m pip install --no-binary :all: --no-build-isolation \
    "git+${LIBOQS_PY_GIT_URL}@${LIBOQS_PY_VERSION}"

  log "Installed liboqs-python from Git (${LIBOQS_PY_GIT_URL}@${LIBOQS_PY_VERSION})"
}

# ---- Install Animica editable so entrypoint exists ----
install_animica() {
  # Detect where the Python package lives
  local target="."
  if [ -f "$ROOT_DIR/python/pyproject.toml" ] || [ -f "$ROOT_DIR/python/setup.py" ]; then
    target="./python"
  fi

  log "Installing Animica editable from $target"
  python -m pip install -e "$target"

  if [ ! -x "$ROOT_DIR/.venv/bin/animica" ]; then
    die "Animica CLI entrypoint not found at .venv/bin/animica after install. Check packaging/entry_points."
  fi
}

# ---- Put animica on PATH as a normal command (no ./animica) ----
link_animica_on_path() {
  local src="$ROOT_DIR/.venv/bin/animica"
  [ -x "$src" ] || die "Expected animica at $src"

  if [ "$(id -u)" -eq 0 ]; then
    local dst="/usr/local/bin/animica"
    log "Linking animica into PATH: $dst -> $src"
    mkdir -p /usr/local/bin
    ln -sf "$src" "$dst"
  else
    local dst="$HOME/.local/bin/animica"
    log "Linking animica into PATH: $dst -> $src"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$src" "$dst"

    # Best-effort PATH hint
    case ":$PATH:" in
      *":$HOME/.local/bin:"*) : ;;
      *)
        warn "NOTE: $HOME/.local/bin is not on PATH in this shell."
        warn "Add this to your shell rc: export PATH=\"$HOME/.local/bin:\$PATH\""
        ;;
    esac
  fi
}

# ---- Verification ----
verify() {
  # Ensure env is active here too
  export OQS_INSTALL_PATH="$PREFIX"
  export LD_LIBRARY_PATH="$OQS_INSTALL_PATH/lib:$OQS_INSTALL_PATH/lib64:${LD_LIBRARY_PATH:-}"

  log "Verifying liboqs version and liboqs-python compatibility"

  # 1) Hard-fail on the version mismatch warning by converting UserWarning into an error
  python -W error::UserWarning -c "import oqs; print('oqs import OK (no version mismatch warning)')"

  # 2) Confirm our pinned liboqs reports the correct version string
  python - <<'PY'
import os, ctypes
p = os.environ.get("OQS_INSTALL_PATH")
assert p, "OQS_INSTALL_PATH missing"
candidates = [os.path.join(p,"lib","liboqs.so"), os.path.join(p,"lib64","liboqs.so")]
so = next((c for c in candidates if os.path.exists(c)), None)
assert so, f"liboqs.so not found under {p}/lib or {p}/lib64"
lib = ctypes.CDLL(so)
# liboqs exposes OQS_version() returning const char*
lib.OQS_version.restype = ctypes.c_char_p
ver = lib.OQS_version().decode("utf-8", "replace")
print("Loaded liboqs:", so)
print("OQS_version():", ver)
if not ver.startswith("0.14."):
    raise SystemExit(f"Expected liboqs 0.14.x, got: {ver}")
PY

  # 3) Ensure animica command exists
  animica --help >/dev/null
  log "Verification OK. You can now run: animica --help"
}

main() {
  log "Repo: $ROOT_DIR"
  install_apt_deps
  build_liboqs
  ensure_venv
  rewrite_activate_block

  # Reload activation to pick up block (for current shell)
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"

  install_liboqs_python
  install_animica
  link_animica_on_path
  verify

  log "Done."
  log "Tip: open a new shell (or run: source .venv/bin/activate) to ensure env vars are loaded."
}

main "$@"
