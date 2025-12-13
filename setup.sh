cat > setup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

log() { echo "[setup] $*"; }
die() { echo "[setup] ERROR: $*" >&2; exit 1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

log "Bootstrapping dependencies in $ROOT_DIR"

# --- system deps (Ubuntu/Debian) ---
if command -v apt-get >/dev/null 2>&1; then
  log "Installing system dependencies (apt-get)"
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -y
  sudo apt-get install -y \
    build-essential cmake ninja-build pkg-config git curl ca-certificates \
    python3 python3-venv python3-pip python3-dev \
    libssl-dev
fi

# --- python venv ---
if [[ ! -d ".venv" ]]; then
  log "Creating .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip wheel setuptools

# --- node deps (if repo uses pnpm) ---
if [[ -f "pnpm-workspace.yaml" || -f "package.json" ]]; then
  if command -v pnpm >/dev/null 2>&1; then
    log "Installing Node workspace dependencies with pnpm"
    pnpm install
  else
    log "pnpm not found; skipping Node install (ok if you only need python/node already set up)"
  fi
fi

# --- Build + install liboqs (shared lib) ---
# IMPORTANT: The Python package 'oqs' loads liboqs via the dynamic linker.
# Do NOT set LIBOQS_PATH to a directory and expect it to be a file.
LIBOQS_PREFIX="/usr/local"

log "Building liboqs (shared library) into ${LIBOQS_PREFIX}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --depth 1 --branch 0.15.0 https://github.com/open-quantum-safe/liboqs.git "$TMP/liboqs"
cmake -S "$TMP/liboqs" -B "$TMP/liboqs/build" -G Ninja \
  -DCMAKE_INSTALL_PREFIX="${LIBOQS_PREFIX}" \
  -DBUILD_SHARED_LIBS=ON
cmake --build "$TMP/liboqs/build"
sudo cmake --install "$TMP/liboqs/build"
sudo ldconfig || true

# --- Install liboqs-python / oqs ---
# We want a modern 'oqs' python package that matches liboqs naming (ML-DSA).
# If pip already has it, this is the easiest + most stable.
log "Installing Python 'oqs' package"
python -m pip install -U liboqs-python || python -m pip install -U oqs || true

# --- Ensure runtime linker can find liboqs ---
# (Most of the time /usr/local/lib is already in ldconfig; we keep LD_LIBRARY_PATH too.)
export LD_LIBRARY_PATH="/usr/local/lib:/usr/local/lib64:${LD_LIBRARY_PATH:-}"
log "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"

# --- IMPORTANT: Stop poisoning LIBOQS_PATH ---
# If you previously exported LIBOQS_PATH=/usr/local, that is a directory and breaks loaders.
# We intentionally do NOT export LIBOQS_PATH here. If you set it in your shell rc, remove it.
if [[ "${LIBOQS_PATH:-}" == "/usr/local" ]]; then
  log "WARNING: LIBOQS_PATH is set to /usr/local (a directory). Unset it in your shell:  unset LIBOQS_PATH"
fi

# --- Install repo python deps ---
if [[ -f "python/pyproject.toml" ]]; then
  log "Installing python package (editable) from python/pyproject.toml"
  python -m pip install -e python
elif [[ -f "python/setup.py" ]]; then
  log "Installing python package (editable) from python/setup.py"
  python -m pip install -e python
else
  log "No python/pyproject.toml or python/setup.py found; skipping editable install"
fi

# --- sanity check: can we instantiate the ML-DSA/Dilithium mechanism? ---
log "PQ sanity-check: import oqs + instantiate ML-DSA-65/Dilithium3"
python - <<'PY'
import sys
try:
    import oqs
except Exception as e:
    print("FAILED: import oqs:", e)
    sys.exit(1)

names = []
for fn in ("get_enabled_sig_mechanisms","get_enabled_mechanisms"):
    if hasattr(oqs, fn):
        try:
            names = list(getattr(oqs, fn)())
            break
        except Exception:
            pass

want = ["ML-DSA-65","Dilithium3"]
print("enabled mechanisms sample:", names[:15])
picked = None
for w in want:
    if w in names:
        picked = w
        break
picked = picked or "ML-DSA-65"

try:
    s = oqs.Signature(picked)
    pk = s.generate_keypair()
    sk = s.export_secret_key()
    msg = b"animica-pq-selftest"
    sig = s.sign(msg)
    ok = s.verify(msg, sig, pk)
    print("OK:", picked, "pk_len=", len(pk), "sk_len=", len(sk), "sig_len=", len(sig), "verify=", ok)
    sys.exit(0 if ok else 2)
except Exception as e:
    print("FAILED: Signature mechanism init/selftest:", picked, e)
    sys.exit(3)
PY

log "Done."
EOF
chmod +x setup.sh
