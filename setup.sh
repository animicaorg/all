
#!/usr/bin/env bash
set -Eeuo pipefail

# Animica setup (defensive, idempotent)
# - Builds a local liboqs into .deps/liboqs-install
# - Patches venv activate to point at the *actual liboqs .so*, not /usr/local
# - Installs python deps without trying to pip-install non-PyPI packages (omni-sdk, animica-pq)

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup_$(date -u +%Y%m%d_%H%M%S).log"

exec > >(tee -a "$LOG_FILE") 2>&1

log()  { echo "[setup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
die()  { echo "[setup] ERROR: $*" >&2; exit 1; }
run()  { log "RUN  $*"; "$@"; }

WITH_PQ=0
CLEAN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-pq) WITH_PQ=1; shift ;;
    --clean)   CLEAN=1; shift ;;
    *) die "Unknown argument: $1" ;;
  esac
done

log "Root: $ROOT_DIR"
log "Log:  $LOG_FILE"

if [[ $CLEAN -eq 1 ]]; then
  log "Cleaning .venv and .deps"
  rm -rf "$ROOT_DIR/.venv" "$ROOT_DIR/.deps"
fi

# ---------------- OS deps (Ubuntu) ----------------
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  run apt-get update -y
  run apt-get install -y --no-install-recommends \
    ca-certificates curl git jq \
    build-essential pkg-config cmake ninja-build \
    python3 python3-venv python3-dev python3-pip \
    libssl-dev libffi-dev
fi

# ---------------- Python venv ----------------
if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  run python3 -m venv "$ROOT_DIR/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT_DIR/.venv/bin/activate"
run python -m pip install -U pip setuptools wheel

# Make repo root importable (so `import pq...` works without a pip package)
SITE_DIR="$(python - <<'PY'
import site
print(site.getsitepackages()[0])
PY
)"
PTH="$SITE_DIR/animica_repo_root.pth"
echo "$ROOT_DIR" > "$PTH"
log "Wrote $PTH -> $ROOT_DIR"

# Install Animica python package itself (but do NOT pull deps that include non-PyPI pkgs)
if [[ -d "$ROOT_DIR/python" ]]; then
  run python -m pip install -e "$ROOT_DIR/python" --no-deps
else
  # fallback if pyproject is at repo root
  run python -m pip install -e "$ROOT_DIR" --no-deps
fi

# Install runtime deps explicitly (avoid omni-sdk / animica-pq)
run python -m pip install -U \
  typer rich requests httpx pydantic pyyaml cbor2

# ---------------- PQ / liboqs ----------------
if [[ $WITH_PQ -eq 1 ]]; then
  DEPS="$ROOT_DIR/.deps"
  OQS_PREFIX="$DEPS/liboqs-install"
  OQS_LIB=""
  OQS_VER="${LIBOQS_VERSION:-0.14.1}"   # override with env if you want

  mkdir -p "$DEPS"

  if [[ ! -f "$OQS_PREFIX/lib/liboqs.so" && ! -f "$OQS_PREFIX/lib64/liboqs.so" ]]; then
    log "Building liboqs $OQS_VER into $OQS_PREFIX"
    rm -rf "$DEPS/liboqs-src"
    run git clone --depth 1 --branch "$OQS_VER" https://github.com/open-quantum-safe/liboqs.git "$DEPS/liboqs-src"
    mkdir -p "$DEPS/liboqs-src/build"
    pushd "$DEPS/liboqs-src/build" >/dev/null
      run cmake -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$OQS_PREFIX" \
        -DBUILD_SHARED_LIBS=ON \
        -DOQS_USE_OPENSSL=ON \
        ..
      run ninja
      run ninja install
    popd >/dev/null
  else
    log "liboqs already present in $OQS_PREFIX (skipping build)"
  fi

  if [[ -f "$OQS_PREFIX/lib/liboqs.so" ]]; then
    OQS_LIB="$OQS_PREFIX/lib/liboqs.so"
  elif [[ -f "$OQS_PREFIX/lib64/liboqs.so" ]]; then
    OQS_LIB="$OQS_PREFIX/lib64/liboqs.so"
  elif [[ -f "$OQS_PREFIX/lib/liboqs.so.0" ]]; then
    OQS_LIB="$OQS_PREFIX/lib/liboqs.so.0"
  elif [[ -f "$OQS_PREFIX/lib64/liboqs.so.0" ]]; then
    OQS_LIB="$OQS_PREFIX/lib64/liboqs.so.0"
  else
    die "Could not locate liboqs shared library under $OQS_PREFIX"
  fi

  log "Using liboqs: $OQS_LIB"

  # Install liboqs-python (oqs) – let pip pick a compatible wheel/sdist.
  # If you already have oqs installed, pip will upgrade/downgrade as needed.
  run python -m pip install -U liboqs-python

  # Patch venv activate (REPLACE any old animica-liboqs block)
  ACT="$ROOT_DIR/.venv/bin/activate"
  perl -0777 -i -pe 's/# >>> animica-liboqs >>>.*?# <<< animica-liboqs <<<\n//s' "$ACT" || true

  cat >> "$ACT" <<ACTEOF

# >>> animica-liboqs >>>
export OQS_INSTALL_PATH='$OQS_PREFIX'
export LIBOQS_PATH='$OQS_LIB'
# Ensure our local liboqs is preferred
export LD_LIBRARY_PATH='$OQS_PREFIX/lib:$OQS_PREFIX/lib64:'"\${LD_LIBRARY_PATH:-}"
# <<< animica-liboqs <<<
ACTEOF

  log "Patched venv activate with LIBOQS_PATH=$OQS_LIB"

  # Re-source activate to apply env vars now
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"

  log "PQ sanity check (show first 20 enabled sig mechs containing DILITHIUM or ML-DSA)"
  LD_PRELOAD="$LIBOQS_PATH" python - <<'PY'
import oqs
mechs = [m for m in oqs.get_enabled_sig_mechanisms() if ("DILITHIUM" in m.upper() or "ML-DSA" in m.upper())]
print(mechs[:20])
PY

fi

log "Done."

