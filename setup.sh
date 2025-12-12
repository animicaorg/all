#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
DEPS_DIR="${DEPS_DIR:-$ROOT_DIR/.deps}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"
LIBOQS_VERSION="${LIBOQS_VERSION:-0.15.0}"
EXTRAS="${EXTRAS:-dev,stratum}"

mkdir -p "$DEPS_DIR" "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup_$(date -u +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[setup] $(ts) $*"; }
die() { log "ERROR: $*"; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

APT_UPDATED=0
apt_install() {
  if [[ "$APT_UPDATED" == "0" ]]; then
    log "apt-get update"
    apt-get update -y
    APT_UPDATED=1
  fi
  log "apt-get install: $*"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
}

ensure_build_deps() {
  apt_install ca-certificates curl git build-essential pkg-config \
    cmake ninja-build \
    libssl-dev \
    python3 python3-venv python3-dev python3-pip \
    jq
}

ensure_venv() {
  local pybin="${PYTHON_BIN:-python3}"
  need_cmd "$pybin"
  if [[ ! -d "$VENV_DIR" ]]; then
    log "Create venv: $VENV_DIR"
    "$pybin" -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  log "Activate venv: source $VENV_DIR/bin/activate"
  python -m pip install -U pip setuptools wheel
}

install_repo_packages() {
  # Install local PQ wrapper if present (fixes: No module named 'pq')
  if [[ -f "$ROOT_DIR/pq/pyproject.toml" || -f "$ROOT_DIR/pq/setup.py" ]]; then
    log "Install local pq package (editable)"
    python -m pip install -e "$ROOT_DIR/pq" --no-deps
  elif [[ -f "$ROOT_DIR/python/pq/pyproject.toml" || -f "$ROOT_DIR/python/pq/setup.py" ]]; then
    log "Install local pq package (editable) from python/pq"
    python -m pip install -e "$ROOT_DIR/python/pq" --no-deps
  else
    log "No local pq package found (this may be OK if pq is elsewhere)"
  fi

  # Install animica (editable) without deps (we install deps ourselves to avoid omni-sdk failures)
  if [[ -f "$ROOT_DIR/python/pyproject.toml" || -f "$ROOT_DIR/python/setup.py" ]]; then
    log "Install animica (editable, no-deps) from ./python"
    python -m pip install -e "$ROOT_DIR/python" --no-deps
  else
    die "Expected ./python package not found (missing python/pyproject.toml or python/setup.py)"
  fi
}

install_python_deps_from_pyproject() {
  local pyproject="$ROOT_DIR/python/pyproject.toml"
  [[ -f "$pyproject" ]] || die "Missing $pyproject"

  log "Resolve Python deps from python/pyproject.toml (excluding omni-sdk) extras=$EXTRAS"
  mapfile -t REQS < <(
    python - <<'PY'
import os, re, sys
import tomllib

root = os.environ.get("ROOT_DIR", ".")
pyproject = os.path.join(root, "python", "pyproject.toml")
extras = [x.strip() for x in os.environ.get("EXTRAS", "dev,stratum").split(",") if x.strip()]
with open(pyproject, "rb") as f:
    data = tomllib.load(f)

proj = data.get("project", {})
deps = list(proj.get("dependencies", []) or [])
opt = proj.get("optional-dependencies", {}) or {}

for ex in extras:
    deps.extend(opt.get(ex, []) or [])

# Drop omni-sdk (non-PyPI) and any empty entries
out = []
for d in deps:
    if not isinstance(d, str):
        continue
    s = d.strip()
    if not s:
        continue
    name = re.split(r"[<=>!~ \[]", s, 1)[0].strip().lower()
    if name in {"omni-sdk", "omni_sdk"}:
        continue
    out.append(s)

# Add must-haves explicitly (covers the errors you hit)
must = [
    "cbor2>=5.6.0",
    "requests>=2.31.0",
]
for m in must:
    nm = re.split(r"[<=>!~ \[]", m, 1)[0].strip().lower()
    if all(re.split(r"[<=>!~ \[]", x, 1)[0].strip().lower() != nm for x in out):
        out.append(m)

for x in out:
    print(x)
PY
  )

  if [[ "${#REQS[@]}" -gt 0 ]]; then
    log "pip install ${#REQS[@]} requirements"
    python -m pip install -U "${REQS[@]}"
  else
    log "No requirements found in pyproject (unexpected); installing minimum deps"
    python -m pip install -U cbor2 requests typer rich pydantic httpx PyYAML python-dotenv
  fi
}

build_and_install_liboqs() {
  log "Install/upgrade liboqs (shared) v$LIBOQS_VERSION"
  local dir="$DEPS_DIR/liboqs"
  if [[ ! -d "$dir/.git" ]]; then
    git clone --depth=1 --branch "$LIBOQS_VERSION" https://github.com/open-quantum-safe/liboqs "$dir"
  else
    (cd "$dir" && git fetch --tags --prune && git checkout -f "$LIBOQS_VERSION")
  fi

  # Build shared library as recommended by OQS docs
  cmake -S "$dir" -B "$dir/build" \
    -DBUILD_SHARED_LIBS=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DOQS_USE_OPENSSL=ON

  cmake --build "$dir/build" --parallel "$(nproc)"
  cmake --build "$dir/build" --target install

  # Ensure dynamic loader can find /usr/local/lib
  if [[ ! -f /etc/ld.so.conf.d/usr-local-lib.conf ]]; then
    echo "/usr/local/lib" >/etc/ld.so.conf.d/usr-local-lib.conf
  fi
  ldconfig

  export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
  export OQS_INSTALL_PATH="/usr/local"
  export LIBOQS_PATH="/usr/local"
}

install_liboqs_python() {
  log "Install liboqs-python (oqs module) + verify mechanisms"

  # Clean out conflicting installs
  python -m pip uninstall -y oqs liboqs-python python-oqs pyoqs pyoqs-sdk >/dev/null 2>&1 || true

  # First try: PyPI
  python -m pip install --no-cache-dir -U liboqs-python || true

  # Verify: must expose at least one signature mechanism (Dilithium preferred)
  if python - <<'PY'
import sys
try:
    import oqs
    mechs = oqs.get_enabled_sig_mechanisms()
    ok = bool(mechs)
    want = any("DILITHIUM" in m.upper() for m in mechs) if ok else False
    print("enabled_sig_mechanisms_count=", len(mechs))
    print("has_dilithium=", want)
    sys.exit(0 if ok else 1)
except Exception as e:
    print("oqs_import_or_query_failed:", e)
    sys.exit(2)
PY
  then
    log "liboqs-python OK (sign mechanisms present)"
    return 0
  fi

  # Fallback: install from GitHub (often fixes wheels that can’t locate liboqs)
  log "PyPI liboqs-python did not expose mechanisms; installing from GitHub source"
  local dir="$DEPS_DIR/liboqs-python"
  if [[ ! -d "$dir/.git" ]]; then
    git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python "$dir"
  else
    (cd "$dir" && git pull --ff-only)
  fi

  (cd "$dir" && python -m pip install --no-cache-dir -U .)

  # Re-verify
  python - <<'PY'
import oqs
mechs = oqs.get_enabled_sig_mechanisms()
print("enabled_sig_mechanisms_count=", len(mechs))
print("sample=", mechs[:10])
if not mechs:
    raise SystemExit("ERROR: oqs installed but no signature mechanisms enabled (liboqs not detected at runtime)")
PY
  log "liboqs-python OK after GitHub install"
}

verify_animica_pq() {
  log "Verify Animica PQ diagnostics (wallet create should work)"
  python - <<'PY'
import os
print("LD_LIBRARY_PATH=", os.environ.get("LD_LIBRARY_PATH",""))
print("OQS_INSTALL_PATH=", os.environ.get("OQS_INSTALL_PATH",""))
print("LIBOQS_PATH=", os.environ.get("LIBOQS_PATH",""))
try:
    import oqs
    mechs = oqs.get_enabled_sig_mechanisms()
    print("oqs enabled sig mechs:", len(mechs))
    print("contains Dilithium:", any("DILITHIUM" in m.upper() for m in mechs))
except Exception as e:
    raise SystemExit(f"oqs check failed: {e}")
PY
}

main() {
  log "Bootstrapping dependencies"
  ensure_build_deps
  ensure_venv

  # Install liboqs + liboqs-python so PQ wallets/signing work
  build_and_install_liboqs
  install_liboqs_python

  # Install repo + python deps (avoid omni-sdk resolution failures)
  install_repo_packages
  install_python_deps_from_pyproject

  verify_animica_pq

  log "Done."
  log "Activate venv: source $VENV_DIR/bin/activate"
  log "Try: animica wallet create --label test1"
}

ROOT_DIR="$ROOT_DIR" EXTRAS="$EXTRAS" main "$@"
