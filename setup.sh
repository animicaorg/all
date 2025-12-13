#!/usr/bin/env bash
set -euo pipefail

log() { printf "[setup] %s %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*"; }
warn() { printf "[setup][WARN] %s\n" "$*" >&2; }
die() { printf "[setup][ERROR] %s\n" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Pin liboqs to 0.14.0 (as requested)
LIBOQS_VERSION="${LIBOQS_VERSION:-0.14.0}"

DEPS_DIR="$ROOT_DIR/.deps"
SRC_DIR="$DEPS_DIR/src"
PREFIX="$DEPS_DIR/oqs-$LIBOQS_VERSION"

LIBOQS_GIT_URL="${LIBOQS_GIT_URL:-https://github.com/open-quantum-safe/liboqs.git}"
LIBOQS_PY_GIT_URL="${LIBOQS_PY_GIT_URL:-https://github.com/open-quantum-safe/liboqs-python}"

mkdir -p "$DEPS_DIR" "$SRC_DIR"

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
    python3-dev
  )

  if [ "$(id -u)" -ne 0 ]; then
    if have sudo; then
      log "Installing system deps with sudo apt-get..."
      sudo apt-get update -y
      sudo apt-get install -y "${pkgs[@]}" || true
    else
      warn "Not root and sudo not found; cannot install apt dependencies automatically."
    fi
  else
    log "Installing system deps with apt-get..."
    apt-get update -y
    apt-get install -y "${pkgs[@]}" || true
  fi
}

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

  local so=""
  if [ -f "$PREFIX/lib/liboqs.so" ]; then so="$PREFIX/lib/liboqs.so"; fi
  if [ -z "$so" ] && [ -f "$PREFIX/lib64/liboqs.so" ]; then so="$PREFIX/lib64/liboqs.so"; fi
  [ -n "$so" ] || die "liboqs build finished but liboqs.so not found under $PREFIX/lib or $PREFIX/lib64"
  log "liboqs installed OK: $so"
}

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

rewrite_activate_block() {
  local act="$ROOT_DIR/.venv/bin/activate"
  [ -f "$act" ] || die "Venv activate not found at $act"

  local start="# >>> ANIMICA OQS ENV >>>"
  local end="# <<< ANIMICA OQS ENV <<<"

  local tmp
  tmp="$(mktemp)"

  awk -v start="$start" -v end="$end" '
    $0==start {inblock=1; next}
    $0==end {inblock=0; next}
    inblock!=1 {print}
  ' "$act" > "$tmp"

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

export_oqs_env_for_this_shell() {
  export OQS_INSTALL_PATH="$PREFIX"
  export LD_LIBRARY_PATH="$OQS_INSTALL_PATH/lib:$OQS_INSTALL_PATH/lib64:${LD_LIBRARY_PATH:-}"
  export CMAKE_PREFIX_PATH="$OQS_INSTALL_PATH:${CMAKE_PREFIX_PATH:-}"
  export LIBRARY_PATH="$OQS_INSTALL_PATH/lib:$OQS_INSTALL_PATH/lib64:${LIBRARY_PATH:-}"
  export CPATH="$OQS_INSTALL_PATH/include:${CPATH:-}"
}

oqs_is_present_and_compatible() {
  # Fail if the mismatch warning appears
  python -W error::UserWarning -c "import oqs" >/dev/null 2>&1
}

install_liboqs_python_from_git_clone() {
  export_oqs_env_for_this_shell

  log "Installing liboqs-python from git clone (depth=1) + pip install"
  local dst="$SRC_DIR/liboqs-python"
  rm -rf "$dst"
  git clone --depth=1 "$LIBOQS_PY_GIT_URL" "$dst"

  # Exactly what you asked (but using venv pip via python -m pip)
  ( cd "$dst" && python -m pip install . )

  if ! oqs_is_present_and_compatible; then
    die "Installed oqs, but it is still warning about a liboqs version mismatch. This usually means the installed python binding expects a different liboqs major/minor than $LIBOQS_VERSION."
  fi

  log "liboqs-python installed and compatible with pinned liboqs."
}

install_animica() {
  local target="."
  if [ -f "$ROOT_DIR/python/pyproject.toml" ] || [ -f "$ROOT_DIR/python/setup.py" ]; then
    target="./python"
  fi

  log "Installing Animica editable from $target"
  python -m pip install -e "$target"

  if [ ! -x "$ROOT_DIR/.venv/bin/animica" ]; then
    die "Animica CLI entrypoint not found at .venv/bin/animica after install."
  fi
}

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
    case ":$PATH:" in
      *":$HOME/.local/bin:"*) : ;;
      *) warn "Add to PATH: export PATH=\"$HOME/.local/bin:\$PATH\"" ;;
    esac
  fi
}

verify() {
  export_oqs_env_for_this_shell

  log "Verifying oqs import has no mismatch warning..."
  python -W error::UserWarning -c "import oqs; print('oqs import OK (no mismatch warning)')"

  log "Verifying liboqs reports 0.14.x..."
  python - <<'PY'
import os, ctypes
p = os.environ.get("OQS_INSTALL_PATH")
assert p, "OQS_INSTALL_PATH missing"
candidates = [os.path.join(p,"lib","liboqs.so"), os.path.join(p,"lib64","liboqs.so")]
so = next((c for c in candidates if os.path.exists(c)), None)
assert so, f"liboqs.so not found under {p}/lib or {p}/lib64"
lib = ctypes.CDLL(so)
lib.OQS_version.restype = ctypes.c_char_p
ver = lib.OQS_version().decode("utf-8", "replace")
print("Loaded liboqs:", so)
print("OQS_version():", ver)
if not ver.startswith("0.14."):
    raise SystemExit(f"Expected liboqs 0.14.x, got: {ver}")
PY

  animica --help >/dev/null
  log "Verification OK. You can run: animica --help"
}

main() {
  log "Repo: $ROOT_DIR"
  install_apt_deps
  build_liboqs
  ensure_venv
  rewrite_activate_block

  # Re-activate to load the newly written activate block for future shells;
  # and export env for THIS shell right now.
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
  export_oqs_env_for_this_shell

  if oqs_is_present_and_compatible; then
    log "oqs already installed and compatible; skipping liboqs-python install."
  else
    log "oqs missing or mismatched; installing liboqs-python via git clone + pip install."
    install_liboqs_python_from_git_clone
  fi

  install_animica
  link_animica_on_path
  verify

  log "Done."
  log "Tip: open a new shell (or run: source .venv/bin/activate) so OQS env vars are always applied."
}

main "$@"
