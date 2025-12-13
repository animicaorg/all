#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# -----------------------------
# Animica setup (defensive)
# -----------------------------

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${ROOT}/logs/setup"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/setup_$(date -u +%Y%m%d_%H%M%S).log"

exec > >(tee -a "${LOG_FILE}") 2>&1

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*"; }
die() { echo "[$(ts)] ERROR: $*" >&2; exit 1; }

trap 'die "Failed at line ${LINENO}: ${BASH_COMMAND}"' ERR

CLEAN=0
WITH_PQ=1
WITH_NODE=1
WITH_WEBSITE=0

usage() {
  cat <<EOF
Usage: ./setup.sh [--clean] [--no-pq] [--no-node] [--with-website]

  --clean         Delete .venv and rebuild from scratch
  --no-pq         Skip liboqs / liboqs-python setup
  --no-node       Skip node build steps (only Python tooling)
  --with-website  Also install website deps (pnpm) if present

This script is defensive: it tolerates missing PyPI deps (e.g. omni-sdk),
and will fall back to editable install --no-deps + explicit dependency list.
EOF
}

for arg in "${@:-}"; do
  case "${arg}" in
    --clean) CLEAN=1 ;;
    --no-pq) WITH_PQ=0 ;;
    --no-node) WITH_NODE=0 ;;
    --with-website) WITH_WEBSITE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown arg: ${arg}" ;;
  esac
done

log "Repo root: ${ROOT}"
log "Log file:  ${LOG_FILE}"

# -----------------------------
# OS deps
# -----------------------------
ensure_apt() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    log "apt-get update"
    apt-get update -y
    log "Installing base packages"
    apt-get install -y \
      ca-certificates curl git jq \
      build-essential pkg-config cmake ninja-build \
      python3 python3-venv python3-dev \
      openssl libssl-dev \
      || die "apt-get install failed"
  else
    die "apt-get not found; this script currently targets Ubuntu/Debian."
  fi
}
ensure_apt

# -----------------------------
# Python venv
# -----------------------------
if [[ "${CLEAN}" == "1" ]]; then
  log "--clean requested: removing ${ROOT}/.venv"
  rm -rf "${ROOT}/.venv"
fi

if [[ ! -d "${ROOT}/.venv" ]]; then
  log "Creating venv"
  python3 -m venv "${ROOT}/.venv"
fi

# shellcheck disable=SC1091
source "${ROOT}/.venv/bin/activate"

log "Upgrading pip tooling"
python -m pip install -U pip setuptools wheel

# Ensure repo root importable in venv (so pq/ and other top-level modules resolve)
log "Writing .pth so repo root is on sys.path"
ANIMICA_REPO_ROOT="${ROOT}" python - <<'PY'
import os, site, pathlib
root = os.environ["ANIMICA_REPO_ROOT"]
sp = site.getsitepackages()[0]
pth = pathlib.Path(sp) / "animica_repo_root.pth"
pth.write_text(root + "\n", encoding="utf-8")
print("wrote", pth)
PY

# -----------------------------
# PQ (liboqs + liboqs-python)
# -----------------------------
DEPS_DIR="${ROOT}/.deps"
OQS_PREFIX="${DEPS_DIR}/oqs-install"
mkdir -p "${DEPS_DIR}"

append_activate_block() {
  local activate="${ROOT}/.venv/bin/activate"
  local begin="# >>> animica pq env >>>"
  local end="# <<< animica pq env <<<"

  # Remove old block if present
  if grep -q "${begin}" "${activate}"; then
    log "Removing existing PQ env block from venv activate"
    awk -v b="${begin}" -v e="${end}" '
      $0==b {skip=1; next}
      $0==e {skip=0; next}
      skip!=1 {print}
    ' "${activate}" > "${activate}.tmp"
    mv "${activate}.tmp" "${activate}"
  fi

  # Append new block
  cat >> "${activate}" <<EOF

${begin}
# Auto-injected by ./setup.sh
export OQS_INSTALL_PATH="${OQS_PREFIX}"
# Prefer an actual shared library file for LIBOQS_PATH (ctypes backends).
if [[ -f "${OQS_PREFIX}/lib/liboqs.so" ]]; then
  export LIBOQS_PATH="${OQS_PREFIX}/lib/liboqs.so"
elif [[ -f "${OQS_PREFIX}/lib64/liboqs.so" ]]; then
  export LIBOQS_PATH="${OQS_PREFIX}/lib64/liboqs.so"
else
  # Fallback: keep as directory; newer code should resolve .so within.
  export LIBOQS_PATH="${OQS_PREFIX}"
fi
export LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:\${LD_LIBRARY_PATH:-}"
# Hint to prefer the Python OQS binding if available
export ANIMICA_PQ_BACKEND="python"
${end}
EOF
}

build_liboqs() {
  local tag="${LIBOQS_TAG:-0.15.0}"
  local src="${DEPS_DIR}/liboqs"
  local bld="${DEPS_DIR}/liboqs-build"

  if [[ -f "${OQS_PREFIX}/lib/liboqs.so" || -f "${OQS_PREFIX}/lib64/liboqs.so" ]]; then
    log "liboqs already present under ${OQS_PREFIX}; skipping build."
    return 0
  fi

  log "Building liboqs from source (tag=${tag})"
  rm -rf "${src}" "${bld}"
  git clone --depth 1 --branch "${tag}" https://github.com/open-quantum-safe/liboqs.git "${src}"
  cmake -S "${src}" -B "${bld}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON \
    -DCMAKE_INSTALL_PREFIX="${OQS_PREFIX}"
  cmake --build "${bld}"
  cmake --install "${bld}"

  [[ -f "${OQS_PREFIX}/lib/liboqs.so" || -f "${OQS_PREFIX}/lib64/liboqs.so" ]] || die "liboqs.so not found after install"
}

install_liboqs_python() {
  log "Installing liboqs-python (prefer PyPI wheel; fallback to source)"
  if python -c "import oqs" >/dev/null 2>&1; then
    log "liboqs-python already importable; skipping."
    return 0
  fi

  if python -m pip install --no-cache-dir "liboqs-python>=0.14,<0.16"; then
    log "Installed liboqs-python from PyPI"
    return 0
  fi

  log "PyPI install failed; building liboqs-python from source"
  local src="${DEPS_DIR}/liboqs-python"
  rm -rf "${src}"
  git clone --depth 1 https://github.com/open-quantum-safe/liboqs-python.git "${src}"
  # Ensure it finds liboqs
  export OQS_INSTALL_PATH="${OQS_PREFIX}"
  export LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:${LD_LIBRARY_PATH:-}"
  python -m pip install --no-cache-dir "${src}"
}

check_pq_mechs() {
  log "Checking enabled PQ signature mechanisms"
  python - <<'PY'
import oqs
mechs = []
if hasattr(oqs, "get_enabled_sig_mechanisms"):
    mechs = oqs.get_enabled_sig_mechanisms()
print("enabled sig mechanisms sample:", mechs[:40])
want = {"Dilithium3", "ML-DSA-65", "ML-DSA-44", "ML-DSA-87"}
if not any(m in set(mechs) for m in want):
    raise SystemExit("Expected Dilithium/ML-DSA mechanisms not enabled. This is usually fine if your node has PQ disabled, but tx signing will fail.")
print("PQ mechanism check: OK")
PY
}

if [[ "${WITH_PQ}" == "1" ]]; then
  build_liboqs
  append_activate_block

  # Reload env block for current shell
  # shellcheck disable=SC1091
  source "${ROOT}/.venv/bin/activate"

  install_liboqs_python || true
  check_pq_mechs || true
else
  log "--no-pq set: skipping liboqs/liboqs-python"
fi

# -----------------------------
# Python deps / install
# -----------------------------
log "Installing baseline Python deps used by CLI (requests, cbor2, rich/typer)"
python -m pip install -U requests cbor2 rich typer

install_animica_editable() {
  local pkg="${ROOT}/python"
  [[ -d "${pkg}" ]] || die "Expected python package dir at ${pkg}"

  log "Attempting: pip install -e ${pkg}"
  if python -m pip install -e "${pkg}"; then
    log "Editable install succeeded"
    return 0
  fi

  log "Editable install failed (common cause: omni-sdk not on PyPI). Falling back to --no-deps."
  python -m pip install -e "${pkg}" --no-deps

  # Best-effort: install declared deps except known-bad ones
  log "Best-effort dependency install (skipping omni-sdk if present)"
  python - <<'PY'
import os, sys, pathlib, subprocess
root = pathlib.Path(os.environ["ANIMICA_REPO_ROOT"])
pyproj = root / "python" / "pyproject.toml"
deps = []

if pyproj.exists():
    import tomllib
    data = tomllib.loads(pyproj.read_text("utf-8"))
    proj = data.get("project", {})
    deps = list(proj.get("dependencies", []) or [])
else:
    # Nothing to parse; keep minimal list
    deps = []

# Filter out omni-sdk and any direct URL deps you don't want here
filtered = []
for d in deps:
    s = str(d).strip()
    if s.startswith("omni-sdk") or s.startswith("omni_sdk") or "omni-sdk" in s:
        continue
    # skip direct git/url deps in this fallback
    if "://" in s or "git+" in s:
        continue
    filtered.append(s)

# Always ensure these exist for tx tooling
baseline = ["requests", "cbor2", "rich", "typer"]
for b in baseline:
    if b not in filtered:
        filtered.append(b)

print("deps(filtered)=", filtered)
if filtered:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", *filtered])
PY
}

export ANIMICA_REPO_ROOT="${ROOT}"
install_animica_editable

# -----------------------------
# Optional Node/website deps
# -----------------------------
if [[ "${WITH_NODE}" == "1" ]]; then
  if [[ -f "${ROOT}/package.json" ]]; then
    log "Root package.json found; ensuring pnpm deps are installed (best-effort)"
    if command -v corepack >/dev/null 2>&1; then
      corepack enable || true
    fi
    if command -v pnpm >/dev/null 2>&1; then
      pnpm -w install || true
    else
      log "pnpm not found; skipping Node workspace install"
    fi
  else
    log "No root package.json; skipping Node workspace install"
  fi
fi

if [[ "${WITH_WEBSITE}" == "1" ]]; then
  if [[ -d "${ROOT}/website" ]]; then
    log "Installing website deps (pnpm) under ./website (best-effort)"
    (cd "${ROOT}/website" && pnpm install) || true
  fi
fi

log "Setup complete."
log "Next: source .venv/bin/activate && animica --help"
