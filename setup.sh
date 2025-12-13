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
LIBOQS_SO="$LIBOQS_LIBDIR/liboqs.so"

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

install_system_deps() {
  if ! have apt-get; then
    warn "apt-get not found; skipping system deps install."
    return
  fi

  log "Installing system build deps via apt-get (ubuntu24-style)"
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

  if [ ! -e "$LIBOQS_SO" ]; then
    die "liboqs install finished but $LIBOQS_SO not found"
  fi

  log "liboqs installed OK: $LIBOQS_LIBDIR/liboqs.so*"
}

install_liboqs_python_014_from_git() {
  log "Installing liboqs-python from a fresh git clone (builds import 'oqs')"

  rm -rf "$SRC_DIR/liboqs-python"
  git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python "$SRC_DIR/liboqs-python"

  (
    cd "$SRC_DIR/liboqs-python"
    OQS_INSTALL_PATH="$LIBOQS_PREFIX" \
    LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    LD_PRELOAD="$LIBOQS_SO${LD_PRELOAD:+:$LD_PRELOAD}" \
    python -m pip install .
  )

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
  log "Installing venv sitecustomize.py to preload vendored liboqs (forces 0.14.0 at runtime)"

  local sp
  sp="$(python - <<'PY'
import site
paths = site.getsitepackages()
print(paths[0] if paths else "")
PY
)"
  [ -n "$sp" ] || die "Could not resolve site-packages path"

  cat > "$sp/sitecustomize.py" <<'PY'
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
                pass
            break
PY
}

patch_venv_activate_env() {
  local ACT="$VENV_DIR/bin/activate"
  log "Patching venv activate to always export OQS env"

  cat >> "$ACT" <<EOF

# ANIMICA_OQS_ENV_BEGIN
export OQS_INSTALL_PATH="$LIBOQS_PREFIX"
export LD_LIBRARY_PATH="$LIBOQS_LIBDIR:\${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="$LIBOQS_SO\${LD_PRELOAD:+:\$LD_PRELOAD}"
# ANIMICA_OQS_ENV_END
EOF
}

verify_oqs_no_mismatch_warning() {
  log "Verifying: importing oqs MUST NOT warn about liboqs 0.15.x"
  OQS_INSTALL_PATH="$LIBOQS_PREFIX" \
  LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  LD_PRELOAD="$LIBOQS_SO${LD_PRELOAD:+:$LD_PRELOAD}" \
  python -W error -c "import oqs" \
    || die "oqs import still raised a warning/error (still loading wrong liboqs somewhere)."
}

pyproject_path_for_animica() {
  if [ -f "$ROOT/python/pyproject.toml" ]; then
    echo "$ROOT/python/pyproject.toml"
  elif [ -f "$ROOT/pyproject.toml" ]; then
    echo "$ROOT/pyproject.toml"
  else
    echo ""
  fi
}

install_animica_deps_filtered() {
  local pp
  pp="$(pyproject_path_for_animica)"
  if [ -z "$pp" ]; then
    warn "No pyproject.toml found at ./python or repo root. Skipping dependency preinstall."
    return
  fi

  log "Installing Animica dependencies from $(basename "$pp") (excluding animica-pq*)"

  # Parse PEP 621 [project].dependencies with tomllib (py3.11+). Ubuntu 24.04 has 3.12 so OK.
  mapfile -t DEPS < <(python - <<PY
import tomllib, pathlib, sys
pp = pathlib.Path(r"$pp")
data = tomllib.loads(pp.read_text())
deps = data.get("project", {}).get("dependencies", []) or []
out = []
for d in deps:
    s = str(d).strip()
    if not s:
        continue
    # filter out animica-pq (not on PyPI)
    if s.lower().startswith("animica-pq"):
        continue
    out.append(s)
print("\\n".join(out))
PY
)

  if [ "${#DEPS[@]}" -eq 0 ]; then
    warn "No dependencies found (or all filtered)."
    return
  fi

  python -m pip install -U "${DEPS[@]}"
}

install_local_animica_pq_if_present() {
  # If animica-pq exists in-repo, install it editable so imports resolve.
  log "Searching repo for a local 'animica-pq' package (pyproject name == animica-pq)"

  local found
  found="$(python - <<'PY'
import os, pathlib, tomllib

root = pathlib.Path(os.environ["ROOT"])
candidates = []
# search a few common roots first
bases = [
  root / "python",
  root / "packages",
  root,
]
seen = set()
for b in bases:
  if not b.exists():
    continue
  for pp in b.rglob("pyproject.toml"):
    # avoid scanning venv/deps if they somehow exist
    if ".venv" in pp.parts or ".deps" in pp.parts:
      continue
    try:
      data = tomllib.loads(pp.read_text())
    except Exception:
      continue
    name = (data.get("project", {}) or {}).get("name", "")
    if isinstance(name, str) and name.strip().lower() == "animica-pq":
      candidates.append(str(pp.parent))
for c in candidates:
  if c not in seen:
    print(c)
    break
PY
  )"

  if [ -n "$found" ] && [ -d "$found" ]; then
    log "Found local animica-pq at: $found"
    python -m pip install -e "$found"
  else
    warn "No local animica-pq package found. Continuing without it (Animica should use fallback PQ-disabled mode)."
  fi
}

install_animica_editable_no_deps() {
  log "Installing Animica editable WITHOUT deps (prevents pip from trying to fetch animica-pq from PyPI)"

  if [ -d "$ROOT/python" ] && { [ -f "$ROOT/python/pyproject.toml" ] || [ -f "$ROOT/python/setup.py" ]; }; then
    python -m pip install -e "$ROOT/python" --no-deps
  elif [ -f "$ROOT/pyproject.toml" ] || [ -f "$ROOT/setup.py" ]; then
    python -m pip install -e "$ROOT" --no-deps
  else
    die "Could not find a Python project to install (no pyproject.toml/setup.py)."
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
LIBOQS_SO=\"$LIBOQS_SO\"

export OQS_INSTALL_PATH=\"\$LIBOQS_PREFIX\"
export LD_LIBRARY_PATH=\"\$LIBOQS_LIBDIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"
export LD_PRELOAD=\"\$LIBOQS_SO\${LD_PRELOAD:+:\$LD_PRELOAD}\"

if [ -x \"\$VENV/bin/animica\" ]; then
  exec \"\$VENV/bin/animica\" \"\$@\"
else
  exec \"\$VENV/bin/python\" -m animica \"\$@\"
fi
EOF"
  $SUDO chmod +x "$TARGET"
}

main() {
  fresh_nuke_repo_state
  install_system_deps
  ensure_venv
  # venv active

  build_and_install_liboqs

  export OQS_INSTALL_PATH="$LIBOQS_PREFIX"
  export LD_LIBRARY_PATH="$LIBOQS_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export LD_PRELOAD="$LIBOQS_SO${LD_PRELOAD:+:$LD_PRELOAD}"

  install_liboqs_python_014_from_git
  install_sitecustomize_preload_liboqs
  patch_venv_activate_env
  verify_oqs_no_mismatch_warning

  # Fix for your current error: install deps ourselves (minus animica-pq), then install animica with --no-deps
  install_animica_deps_filtered
  install_local_animica_pq_if_present
  install_animica_editable_no_deps

  install_global_animica_wrapper

  log "Done."
  log "Try: animica --help"
  log "If you want the venv in your shell: source .venv/bin/activate"
}

main "$@"
