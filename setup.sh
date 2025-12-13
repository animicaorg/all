#!/usr/bin/env bash
set -Eeuo pipefail

# Animica setup (defensive, idempotent)
# - Builds a local liboqs into .deps/liboqs-install
# - Installs animica + animica-pq from the repo (no non-PyPI deps)
# - Patches venv activate to point at the actual liboqs .so

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
  run python -m pip install -e "$ROOT_DIR" --no-deps
fi

# Install runtime deps explicitly (avoid omni-sdk / animica-pq from PyPI)
run python -m pip install -U \
  typer rich requests httpx pydantic pyyaml cbor2

# Install local animica-pq package to satisfy dependency without PyPI
if [[ -f "$ROOT_DIR/pq/pyproject.toml" ]]; then
  run python -m pip install -e "$ROOT_DIR/pq"
fi

# ---------------- PQ / liboqs ----------------
if [[ $WITH_PQ -eq 1 ]]; then
  DEPS="$ROOT_DIR/.deps"
  OQS_PREFIX="$DEPS/liboqs-install"
  mkdir -p "$DEPS"

  select_tag() {
    local repo="$1" requested="$2"
    python - <<'PY' "$repo" "$requested"
import re, subprocess, sys
repo, requested = sys.argv[1], sys.argv[2]
out = subprocess.check_output(["git", "ls-remote", "--tags", "--refs", repo], text=True)
tags = []
for line in out.strip().splitlines():
    name = line.split()[-1].removeprefix("refs/tags/")
    norm = name.lstrip("v")
    if re.fullmatch(r"\d+\.\d+\.\d+", norm):
        tags.append((norm, name))
if not tags:
    print("", file=sys.stderr)
    raise SystemExit("No version tags found for %s" % repo)
tags.sort(key=lambda t: tuple(map(int, t[0].split("."))))
req_norm = requested.lstrip("v")
selected = None
for norm, name in tags:
    if norm == req_norm or name == requested:
        selected = name
        break
if selected is None:
    selected = tags[-1][1]
    print(f"WARN: requested tag {requested} not found on {repo}; using latest {selected}", file=sys.stderr)
print(selected)
PY
  }

  OQS_REQUESTED="${LIBOQS_VERSION:-0.15.0}"
  if ! OQS_TAG=$(select_tag "https://github.com/open-quantum-safe/liboqs.git" "$OQS_REQUESTED"); then
    die "Could not determine liboqs tag"
  fi
  log "liboqs tag selected: $OQS_TAG"

  if [[ ! -f "$OQS_PREFIX/lib/liboqs.so" && ! -f "$OQS_PREFIX/lib64/liboqs.so" && ! -f "$OQS_PREFIX/lib/liboqs.so.0" && ! -f "$OQS_PREFIX/lib64/liboqs.so.0" ]]; then
    log "Building liboqs $OQS_TAG into $OQS_PREFIX"
    rm -rf "$DEPS/liboqs-src"
    run git clone --depth 1 --branch "$OQS_TAG" https://github.com/open-quantum-safe/liboqs.git "$DEPS/liboqs-src"
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

  OQS_LIB=""
  for candidate in \
    "$OQS_PREFIX/lib/liboqs.so" \
    "$OQS_PREFIX/lib64/liboqs.so" \
    "$OQS_PREFIX/lib/liboqs.so.0" \
    "$OQS_PREFIX/lib64/liboqs.so.0" \
    "$OQS_PREFIX/lib/liboqs.so.5" \
    "$OQS_PREFIX/lib64/liboqs.so.5"; do
    if [[ -f "$candidate" ]]; then
      OQS_LIB="$candidate"
      break
    fi
  done
  [[ -n "$OQS_LIB" ]] || die "Could not locate liboqs shared library under $OQS_PREFIX"
  log "Using liboqs: $OQS_LIB"

  # Install matching liboqs-python
  OQS_PY_REQUESTED="$OQS_TAG"
  if ! OQS_PY_TAG=$(select_tag "https://github.com/open-quantum-safe/liboqs-python.git" "$OQS_PY_REQUESTED"); then
    die "Could not determine liboqs-python tag"
  fi
  log "liboqs-python tag selected: $OQS_PY_TAG"
  if ! python -m pip install --no-deps --no-build-isolation "git+https://github.com/open-quantum-safe/liboqs-python.git@$OQS_PY_TAG"; then
    log "WARN: git install of liboqs-python failed; falling back to PyPI"
    run python -m pip install -U liboqs-python
  fi

  # Patch venv activate (REPLACE any old animica-liboqs block)
  ACT="$ROOT_DIR/.venv/bin/activate"
  perl -0777 -i -pe 's/# >>> animica-liboqs >>>.*?# <<< animica-liboqs <<<\n//s' "$ACT" || true

  cat >> "$ACT" <<ACTEOF
# >>> animica-liboqs >>>
export OQS_INSTALL_PATH='$OQS_PREFIX'
export LIBOQS_PATH='$OQS_LIB'
export LD_LIBRARY_PATH='$OQS_PREFIX/lib:$OQS_PREFIX/lib64:'"\${LD_LIBRARY_PATH:-}"
# <<< animica-liboqs <<<
ACTEOF

  log "Patched venv activate with LIBOQS_PATH=$OQS_LIB"

  # Re-source activate to apply env vars now
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"

  log "PQ sanity check (enabled sig mechanisms)"
  LD_LIBRARY_PATH="$OQS_PREFIX/lib:$OQS_PREFIX/lib64:${LD_LIBRARY_PATH:-}" LIBOQS_PATH="$OQS_LIB" python - <<'PY'
import oqs, json
mechs = list(getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])())
print("oqs version:", getattr(oqs, "__version__", "unknown"))
print("enabled sig mechanisms:", len(mechs))
print("sample:", [m for m in mechs if ("DILITHIUM" in m.upper() or "ML-DSA" in m.upper())][:20])
if len(mechs) == 0:
    raise SystemExit("No enabled PQ signature mechanisms (liboqs build failed)")
PY
fi

log "Done."
