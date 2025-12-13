#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# -----------------------------
# Animica setup (super defensive)
# -----------------------------

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${ROOT}/logs/setup"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/setup_$(date -u +%Y%m%d_%H%M%S).log"

exec > >(tee -a "${LOG_FILE}") 2>&1

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[setup] $(ts) $*"; }
warn() { echo "[setup] $(ts) WARN: $*" >&2; }
die() { echo "[setup] $(ts) ERROR: $*" >&2; exit 1; }

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
  --no-node       Skip node/monorepo install steps
  --with-website  Also install website deps (pnpm) if present

This script is defensive:
- If editable install fails because a dependency is missing on PyPI (e.g. omni-sdk, animica-pq),
  it falls back to --no-deps and installs what it can, skipping missing packages.
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

log "================================================================="
log "Start"
log "================================================================="
log "Repo root: ${ROOT}"
log "Venv:      ${ROOT}/.venv"
log "Log:       ${LOG_FILE}"

# -----------------------------
# OS deps (Ubuntu/Debian)
# -----------------------------
if ! command -v apt-get >/dev/null 2>&1; then
  die "apt-get not found; this setup.sh currently targets Ubuntu/Debian."
fi

export DEBIAN_FRONTEND=noninteractive
log "================================================================="
log "OS packages"
log "================================================================="
apt-get update -y
apt-get install -y \
  ca-certificates curl git jq \
  build-essential pkg-config cmake ninja-build \
  libssl-dev libffi-dev \
  python3 python3-venv python3-dev python3-pip \
  patchelf \
  || die "apt-get install failed"

# -----------------------------
# Python venv
# -----------------------------
log "================================================================="
log "Python / Virtualenv"
log "================================================================="
if [[ "${CLEAN}" == "1" ]]; then
  log "--clean requested: removing ${ROOT}/.venv"
  rm -rf "${ROOT}/.venv"
fi

if [[ ! -d "${ROOT}/.venv" ]]; then
  log "Creating venv"
  python3 -m venv "${ROOT}/.venv"
else
  log "Venv exists: ${ROOT}/.venv"
fi

# shellcheck disable=SC1091
source "${ROOT}/.venv/bin/activate"
log "Activated venv: ${VIRTUAL_ENV}"

python -m pip install -U pip setuptools wheel

# Ensure repo root importable in venv
log "Make repo importable (pth)"
python - <<'PY'
import os, site, pathlib
root = os.environ.get("ANIMICA_REPO_ROOT", None) or pathlib.Path(".").resolve().as_posix()
sp = site.getsitepackages()[0]
pth = pathlib.Path(sp) / "animica_repo_root.pth"
pth.write_text(root + "\n", encoding="utf-8")
print("site-packages:", sp)
print("wrote:", pth, "->", root)
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

  if grep -q "${begin}" "${activate}"; then
    log "Removing existing PQ env block from venv activate"
    awk -v b="${begin}" -v e="${end}" '
      $0==b {skip=1; next}
      $0==e {skip=0; next}
      skip!=1 {print}
    ' "${activate}" > "${activate}.tmp"
    mv "${activate}.tmp" "${activate}"
  fi

  cat >> "${activate}" <<EOF

${begin}
# Auto-injected by ./setup.sh
export OQS_INSTALL_PATH="${OQS_PREFIX}"

# Prefer a shared library FILE for LIBOQS_PATH (avoid "Is a directory" errors)
if [[ -f "${OQS_PREFIX}/lib/liboqs.so" ]]; then
  export LIBOQS_PATH="${OQS_PREFIX}/lib/liboqs.so"
elif [[ -f "${OQS_PREFIX}/lib64/liboqs.so" ]]; then
  export LIBOQS_PATH="${OQS_PREFIX}/lib64/liboqs.so"
else
  # Fallback: directory (code should resolve liboqs.so within)
  export LIBOQS_PATH="${OQS_PREFIX}"
fi

export LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:\${LD_LIBRARY_PATH:-}"
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

  log "liboqs-dev not available on this OS; building liboqs from source (tag=${tag})"
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
  log "Installing liboqs-python (oqs)"
  if python -c "import oqs" >/dev/null 2>&1; then
    log "oqs already importable; skipping."
    return 0
  fi

  if python -m pip install --no-cache-dir "liboqs-python>=0.14,<0.16"; then
    log "Installed liboqs-python from PyPI"
    return 0
  fi

  warn "PyPI install failed; building liboqs-python from source"
  local src="${DEPS_DIR}/liboqs-python"
  rm -rf "${src}"
  git clone --depth 1 https://github.com/open-quantum-safe/liboqs-python.git "${src}"

  export OQS_INSTALL_PATH="${OQS_PREFIX}"
  export LD_LIBRARY_PATH="${OQS_PREFIX}/lib:${OQS_PREFIX}/lib64:${LD_LIBRARY_PATH:-}"
  python -m pip install --no-cache-dir "${src}"
}

check_pq_mechs() {
  log "Checking enabled PQ signature mechanisms (best-effort)"
  python - <<'PY'
try:
    import oqs
    mechs = oqs.get_enabled_sig_mechanisms() if hasattr(oqs, "get_enabled_sig_mechanisms") else []
    print("enabled_sig_mechanisms_count =", len(mechs))
    print("sample =", mechs[:20])
except Exception as e:
    print("WARN: PQ mechanism check failed:", repr(e))
PY
}

if [[ "${WITH_PQ}" == "1" ]]; then
  log "================================================================="
  log "PQ dependencies"
  log "================================================================="
  build_liboqs
  append_activate_block
  # reload env for current shell
  # shellcheck disable=SC1091
  source "${ROOT}/.venv/bin/activate"

  install_liboqs_python || warn "liboqs-python install failed (continuing)"
  check_pq_mechs || true
else
  log "--no-pq set: skipping liboqs/liboqs-python"
fi

# -----------------------------
# Baseline Python deps used by CLI
# -----------------------------
log "================================================================="
log "Python deps (baseline)"
log "================================================================="
python -m pip install -U requests cbor2 rich typer || die "Failed installing baseline deps"

# -----------------------------
# Install Animica Python package (editable)
# -----------------------------
log "================================================================="
log "Install Animica (Python)"
log "================================================================="
export ANIMICA_REPO_ROOT="${ROOT}"

install_animica_editable() {
  local pkg="${ROOT}/python"
  [[ -d "${pkg}" ]] || die "Expected python package dir at ${pkg}"

  log "Attempting: pip install -e ${pkg}"
  if python -m pip install -e "${pkg}"; then
    log "Editable install succeeded"
    return 0
  fi

  warn "Editable install failed (common cause: missing deps on PyPI like omni-sdk/animica-pq). Falling back to --no-deps."
  python -m pip install -e "${pkg}" --no-deps
  return 0
}

install_animica_editable

# -----------------------------
# Best-effort dependency install:
# - Parse pyproject deps
# - Skip known-missing deps (omni-sdk, animica-pq)
# - Install each dep one-by-one; failures become warnings
# -----------------------------
log "================================================================="
log "Best-effort dependency install (never hard-fails)"
log "================================================================="
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

def should_skip(dep: str) -> bool:
    s = dep.strip()
    bad = ("omni-sdk", "omni_sdk", "animica-pq", "animica_pq")
    if any(b in s for b in bad):
        return True
    # skip direct URL / git deps in this generic bootstrap
    if "://" in s or "git+" in s:
        return True
    return False

filtered = []
for d in deps:
    s = str(d).strip()
    if not s:
        continue
    if should_skip(s):
        continue
    filtered.append(s)

# Ensure baseline always present
baseline = ["requests", "cbor2", "rich", "typer"]
for b in baseline:
    if b not in filtered:
        filtered.append(b)

# Add common runtime deps that often exist in this repo (safe if already installed)
extras = ["httpx>=0.27.0", "pydantic>=2.7.0", "pyyaml>=6.0.1"]
for e in extras:
    if e not in filtered:
        filtered.append(e)

print("deps(filtered)=", filtered)

py = sys.executable
for dep in filtered:
    try:
        subprocess.check_call([py, "-m", "pip", "install", "-U", dep])
    except subprocess.CalledProcessError as e:
        print(f"WARN: pip install failed for {dep!r} (continuing): exit={e.returncode}")

print("Best-effort deps done.")
PY

# -----------------------------
# Optional Node/website deps (best-effort)
# -----------------------------
if [[ "${WITH_NODE}" == "1" ]]; then
  log "================================================================="
  log "Node workspace (best-effort)"
  log "================================================================="
  if [[ -f "${ROOT}/package.json" ]]; then
    if command -v corepack >/dev/null 2>&1; then
      corepack enable || true
    fi
    if command -v pnpm >/dev/null 2>&1; then
      pnpm -w install || warn "pnpm install failed (continuing)"
    else
      warn "pnpm not found; skipping Node install"
    fi
  else
    log "No root package.json; skipping Node install"
  fi
fi

if [[ "${WITH_WEBSITE}" == "1" ]]; then
  log "================================================================="
  log "Website deps (best-effort)"
  log "================================================================="
  if [[ -d "${ROOT}/website" ]]; then
    if command -v pnpm >/dev/null 2>&1; then
      (cd "${ROOT}/website" && pnpm install) || warn "website pnpm install failed"
    else
      warn "pnpm not found; skipping website deps"
    fi
  else
    warn "website/ not found; skipping"
  fi
fi

log "================================================================="
log "Setup complete"
log "================================================================="
log "Next:"
log "  source .venv/bin/activate"
log "  animica --help"
