#!/usr/bin/env bash
set -Eeuo pipefail

# Animica setup (defensive, idempotent)
# - Builds a local liboqs into .deps/oqs-install
# - Installs animica + animica-pq from the repo (no non-PyPI deps)
# - Patches venv activate to point at the actual liboqs .so

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup_$(date -u +%Y%m%d_%H%M%S).log"

exec > >(tee -a "$LOG_FILE") 2>&1

log()  { echo "[setup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
die()  { echo "[setup] ERROR: $*" >&2; exit 1; }
warn() { echo "[setup] WARN: $*" >&2; }
run()  { log "RUN  $*"; "$@"; }
pip_install() {
  local args=("$@")
  if python -m pip install "${args[@]}"; then
    return 0
  fi
  warn "pip install failed for: python -m pip ${args[*]} (retrying from cache if available)"
  if python -m pip install --no-index --find-links="$HOME/.cache/pip" "${args[@]}"; then
    return 0
  fi
  warn "pip install still failed; proceeding with existing environment"
  return 1
}

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
  if [[ ${ANIMICA_SKIP_APT:-1} -eq 1 ]]; then
    warn "Skipping apt-get (ANIMICA_SKIP_APT=1 or offline environment)"
  else
    export DEBIAN_FRONTEND=noninteractive
    if ! apt-get update -y; then
      warn "apt-get update failed (network/proxy?). Continuing with existing packages."
    else
      if ! apt-get install -y --no-install-recommends \
        ca-certificates curl git jq \
        build-essential pkg-config cmake ninja-build \
        python3 python3-venv python3-dev python3-pip \
        libssl-dev libffi-dev; then
        warn "apt-get install failed; assuming build deps already present"
      fi
    fi
  fi
fi

# ---------------- Python venv ----------------
if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  run python3 -m venv "$ROOT_DIR/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT_DIR/.venv/bin/activate"
pip_install -U pip setuptools wheel || warn "pip bootstrap (pip/setuptools/wheel) incomplete; using bundled versions"

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
  PIP_NO_BUILD_ISOLATION=1 pip_install --no-build-isolation -e "$ROOT_DIR/python" --no-deps || \
    warn "Editable install of animica python package skipped"
else
  PIP_NO_BUILD_ISOLATION=1 pip_install --no-build-isolation -e "$ROOT_DIR" --no-deps || \
    warn "Editable install of repo package skipped"
fi

# Install runtime deps explicitly (avoid omni-sdk / animica-pq from PyPI)
pip_install -U typer rich requests httpx pydantic pyyaml cbor2 || \
  warn "Runtime dependency install skipped (offline?)"

# Install local animica-pq package to satisfy dependency without PyPI
if [[ -f "$ROOT_DIR/pq/pyproject.toml" ]]; then
  PIP_NO_BUILD_ISOLATION=1 pip_install --no-build-isolation -e "$ROOT_DIR/pq" --no-deps || \
    warn "Failed to install local animica-pq package"
fi

# ---------------- PQ / liboqs ----------------
if [[ $WITH_PQ -eq 1 ]]; then
  DEPS="$ROOT_DIR/.deps"
  OQS_PREFIX="$DEPS/oqs-install"
  mkdir -p "$DEPS"

  select_tag() {
    local repo="$1" preferred_major_minor="$2"
    python - <<'PY' "$repo" "$preferred_major_minor"
import re, subprocess, sys

repo, preferred = sys.argv[1], sys.argv[2]
out = subprocess.check_output(["git", "ls-remote", "--tags", "--refs", repo], text=True)
tags = []
for line in out.strip().splitlines():
    name = line.split()[-1].removeprefix("refs/tags/")
    norm = name.lstrip("v")
    if re.fullmatch(r"\d+\.\d+\.\d+", norm):
        major, minor, patch = map(int, norm.split("."))
        tags.append(((major, minor, patch), name))

if not tags:
    raise SystemExit("No version tags found for %s" % repo)

tags.sort()

preferred_tuple = None
if preferred:
    try:
        major, minor = map(int, preferred.split("."))
        preferred_tuple = (major, minor)
    except Exception:
        preferred_tuple = None

selected = None
if preferred_tuple:
    preferred_matches = [
        name for (maj, minr, _), name in tags if (maj, minr) == preferred_tuple
    ]
    if preferred_matches:
        selected = preferred_matches[-1]

if selected is None:
    selected = tags[-1][1]

print(selected)
PY
  }

  OQS_REQUESTED_PREFIX="${LIBOQS_VERSION:-0.15.0}"
  if ! OQS_TAG=$(select_tag "https://github.com/open-quantum-safe/liboqs.git" "$OQS_REQUESTED_PREFIX"); then
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
  OQS_LIB_DIR="$(dirname "$OQS_LIB")"
  export OQS_INSTALL_PATH="$OQS_PREFIX"
  export LIBOQS_PATH="$OQS_LIB"
  export LD_LIBRARY_PATH="$OQS_LIB_DIR:$OQS_PREFIX/lib:$OQS_PREFIX/lib64:${LD_LIBRARY_PATH:-}"
  OQS_VERSION="${OQS_TAG#v}"
  log "Using liboqs: $OQS_LIB (version $OQS_VERSION)"

  # Install liboqs-python aligned with liboqs
  install_ok=0
  if OQS_INSTALL_PATH="$OQS_PREFIX" LIBOQS_PATH="$OQS_LIB" LD_LIBRARY_PATH="$LD_LIBRARY_PATH" \
      pip_install -U --no-cache-dir "liboqs-python==${OQS_VERSION}"; then
    install_ok=1
  else
    warn "pip install liboqs-python==${OQS_VERSION} failed; attempting source build"
    rm -rf "$DEPS/liboqs-python"
    if run git clone --depth 1 https://github.com/open-quantum-safe/liboqs-python.git "$DEPS/liboqs-python"; then
      pushd "$DEPS/liboqs-python" >/dev/null
        if ! git checkout -q "v${OQS_VERSION}"; then
          die "liboqs-python tag v${OQS_VERSION} not found; set LIBOQS_VERSION to an available release"
        fi
        if OQS_INSTALL_PATH="$OQS_PREFIX" LIBOQS_PATH="$OQS_LIB" LD_LIBRARY_PATH="$LD_LIBRARY_PATH" \
            pip_install -U --no-cache-dir .; then
          install_ok=1
        else
          warn "liboqs-python source build failed"
        fi
      popd >/dev/null
    fi
  fi

  [[ $install_ok -eq 1 ]] || die "Failed to install liboqs-python aligned with $OQS_VERSION"

  # Patch venv activate (REPLACE any old animica-liboqs block)
  ACT="$ROOT_DIR/.venv/bin/activate"
  perl -0777 -i -pe 's/# >>> animica-liboqs >>>.*?# <<< animica-liboqs <<<\n//s' "$ACT" || true

  cat >> "$ACT" <<ACTEOF
# >>> animica-liboqs >>>
export OQS_INSTALL_PATH='$OQS_PREFIX'
export LIBOQS_PATH='$OQS_LIB'
export LD_LIBRARY_PATH='$OQS_LIB_DIR:$OQS_PREFIX/lib:$OQS_PREFIX/lib64:'"\${LD_LIBRARY_PATH:-}"
if [ ! -f "\${LIBOQS_PATH}" ]; then
  echo "[animica] ERROR: LIBOQS_PATH=\${LIBOQS_PATH} is missing; re-run setup.sh --with-pq" >&2
fi
# <<< animica-liboqs <<<
ACTEOF

  log "Patched venv activate with LIBOQS_PATH=$OQS_LIB"

  # Re-source activate to apply env vars now
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"

  if [[ ! -f "${LIBOQS_PATH:-}" ]]; then
    die "LIBOQS_PATH=${LIBOQS_PATH:-unset} does not exist after activation"
  fi

  log "PQ sanity check (enabled sig mechanisms)"
  LD_LIBRARY_PATH="$LD_LIBRARY_PATH" LIBOQS_PATH="$LIBOQS_PATH" OQS_INSTALL_PATH="$OQS_INSTALL_PATH" python - <<'PY'
import os
import subprocess
import sys

try:
    import oqs
except Exception as e:  # pragma: no cover - diagnostic
    print("Failed to import oqs:", e)
    print("LIBOQS_PATH:", os.environ.get("LIBOQS_PATH"))
    print("LD_LIBRARY_PATH:", os.environ.get("LD_LIBRARY_PATH"))
    raise

mechs = list(getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])())
print("oqs version:", getattr(oqs, "__version__", "unknown"))
print("enabled sig mechanisms:", len(mechs))
print("sample:", [m for m in mechs if ("DILITHIUM" in m.upper() or "ML-DSA" in m.upper())][:20])
if len(mechs) == 0:
    print("No enabled PQ signature mechanisms; diagnostics:")
    print("  LIBOQS_PATH=", os.environ.get("LIBOQS_PATH"))
    print("  OQS_INSTALL_PATH=", os.environ.get("OQS_INSTALL_PATH"))
    print("  LD_LIBRARY_PATH=", os.environ.get("LD_LIBRARY_PATH"))
    ext_path = getattr(oqs, "__file__", "<unknown>")
    print("  oqs extension path=", ext_path)
    if ext_path not in (None, "<unknown>"):
        try:
            out = subprocess.check_output(["ldd", ext_path], text=True)
            print("  ldd oqs extension:\n" + out)
        except Exception as ldd_err:  # pragma: no cover - diagnostics only
            print("  ldd error:", ldd_err)
    raise SystemExit("No enabled PQ signature mechanisms (liboqs build failed)")

# Quick self-test: choose ML-DSA-65 if available, else first enabled mechanism
mech = None
if "ML-DSA-65" in mechs:
    mech = "ML-DSA-65"
elif mechs:
    mech = mechs[0]
else:
    raise SystemExit("No PQ signature mechanisms enabled")

print("self-test mechanism:", mech)
with oqs.Signature(mech) as signer:
    pk = signer.generate_keypair()
    sk = signer.export_secret_key()
    msg = b"animica-pq-self-test"
    sig = signer.sign(msg)

with oqs.Signature(mech, secret_key=sk) as s2:
    assert s2.verify(msg, sig, pk), "oqs verify failed"

print("oqs self-test: PASS")
PY
fi

log "Done."
