#!/usr/bin/env bash
# Build the Animica node binary (animica-node or animicad)
# Creates a redistributable daemon artifact for the current platform
#
# Usage:
#   ./ops/build/build-node-binary.sh [OPTIONS]
#
# Options:
#   --network NETWORK    Network type (default: mainnet)
#   --out-dir DIR        Output directory (default: dist/)
#   --clean              Clean previous builds before building
#   --version VERSION    Override version string
#   --help               Show this help

set -euo pipefail

# Source common helpers
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/build/common.sh
source "$SCRIPT_DIR/common.sh"

# ============================================================================
# Configuration
# ============================================================================

REPO_ROOT="$(get_repo_root)"
DEFAULT_OUT_DIR="$REPO_ROOT/dist"
DEFAULT_NETWORK="mainnet"

OUT_DIR=""
NETWORK=""
VERSION=""
CLEAN=false

# ============================================================================
# Parse arguments
# ============================================================================

usage() {
    cat <<EOF
Build Animica node binary

Usage:
  $0 [OPTIONS]

Options:
  --network NETWORK    Network type (default: mainnet)
  --out-dir DIR        Output directory (default: dist/)
  --clean              Clean previous builds before building
  --version VERSION    Override version string
  --help               Show this help

Examples:
  $0 --clean
  $0 --network testnet --out-dir /tmp/build
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --network)
            NETWORK="$2"
            shift 2
            ;;
        --out-dir)
            OUT_DIR="$2"
            shift 2
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --version)
            VERSION="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1 (use --help for usage)"
            ;;
    esac
done

# Apply defaults
OUT_DIR="${OUT_DIR:-$DEFAULT_OUT_DIR}"
NETWORK="${NETWORK:-$DEFAULT_NETWORK}"
VERSION="${VERSION:-$(compute_version)}"

# ============================================================================
# Preflight checks
# ============================================================================

log "Building Animica node binary"
log "Repository: $REPO_ROOT"
log "Output directory: $OUT_DIR"
log "Network: $NETWORK"
log "Version: $VERSION"

# Find Python
PY="$(find_python3)" || die "Python 3 not found. Install Python 3.10+"
check_python_version "$PY"

# ============================================================================
# Clean if requested
# ============================================================================

if [[ "$CLEAN" == "true" ]]; then
    log "Cleaning previous builds..."
    safe_rm_rf "$OUT_DIR"
fi

# Create output directory
mkdir -p "$OUT_DIR"

# Create isolated build venv to avoid system/site-packages leakage
VENV_DIR="$OUT_DIR/.node-build-venv"
log "Creating build venv: $VENV_DIR"
"$PY" -m venv "$VENV_DIR"
PY="$VENV_DIR/bin/python"
check_python_version "$PY"

# ============================================================================
# Setup build environment
# ============================================================================

setup_python_build_env

# Install build dependencies
log "Installing build dependencies..."
pip_install "$PY" pip setuptools wheel pyinstaller pyinstaller-hooks-contrib

# Determine Python project directories
NODE_PKG_DIR="$REPO_ROOT"
PYTHON_PKG_DIR="$REPO_ROOT/python"

validate_python_package "$NODE_PKG_DIR" "Animica node package"
validate_python_package "$PYTHON_PKG_DIR" "Animica CLI package"

log "Using node package root: $NODE_PKG_DIR"
log "Using CLI package root: $PYTHON_PKG_DIR"

# Install repo in editable mode (ensures all dependencies are available)
log "Installing Animica node package in editable mode..."
"$PY" -m pip install --quiet -e "$NODE_PKG_DIR"

log "Installing Animica CLI package in editable mode..."
"$PY" -m pip install --quiet -e "$PYTHON_PKG_DIR"

# Install node runtime dependencies (FastAPI stack, etc.)
NODE_REQUIREMENTS="$REPO_ROOT/requirements.txt"
if [[ -f "$NODE_REQUIREMENTS" ]]; then
    log "Installing node runtime requirements from $NODE_REQUIREMENTS..."
    "$PY" -m pip install --quiet -r "$NODE_REQUIREMENTS"
else
    die "Node requirements not found at $NODE_REQUIREMENTS"
fi

log "Verifying FastAPI import..."
"$PY" -c "import fastapi" >/dev/null 2>&1 || die "FastAPI import failed in build venv"

log "Verifying node package imports..."
"$PY" -c "import animica.cli.main; import consensus.state; import rpc.server; import p2p; import consensus; import execution; import mining; import wallet; import mempool; import randomness; print('imports ok')" \
    >/dev/null 2>&1 || die "Node package import precheck failed"

# ============================================================================
# Determine build method (PyInstaller for Python-based node)
# ============================================================================

# The node is Python-based, entry point is in the detected package directory
# We'll use PyInstaller to create a standalone binary

ENTRY_POINT="$OUT_DIR/animica-node-entry.py"
cat > "$ENTRY_POINT" <<'PYEOF'
import sys
from importlib import import_module

from animica.cli.main import main as cli_main


def _run_preflight_imports() -> int:
    modules = [
        "fastapi",
        "starlette",
        "pydantic",
        "uvicorn",
        "animica.cli.main",
        "rpc.server",
        "rpc.jsonrpc",
        "rpc.ws",
        "rpc.methods",
        "consensus.state",
        "core.db",
        "mempool",
        "p2p",
        "consensus",
        "execution",
        "mining",
        "wallet",
        "randomness",
        "pq",
    ]
    for module in modules:
        import_module(module)
    from rpc import methods as rpc_methods
    from rpc import server as rpc_server

    methods = rpc_server.ensure_rpc_methods_registered()
    rpc_server._assert_required_methods(methods)
    print(f"OK: {len(methods)} methods registered")
    print(",".join(methods))
    return 0


def _should_run_rpc(argv: list[str]) -> bool:
    rpc_flags = {
        "--rpc-bind",
        "--rpc-port",
        "--rpc-auth-token-file",
        "--data-dir",
        "--log-file",
    }
    return any(arg.split("=", 1)[0] in rpc_flags for arg in argv[1:])


if "--preflight-imports" in sys.argv:
    sys.exit(_run_preflight_imports())


if _should_run_rpc(sys.argv):
    from rpc.server import main as rpc_main

    rpc_main()
else:
    cli_main()
PYEOF

log "Entry point: $ENTRY_POINT"

# ============================================================================
# Create PyInstaller spec
# ============================================================================

SPEC_FILE="$OUT_DIR/animica-node.spec"
WORK_DIR="$OUT_DIR/build-work"
DIST_SUBDIR="$OUT_DIR"

log "Creating PyInstaller spec: $SPEC_FILE"

cat > "$SPEC_FILE" <<'SPEC_EOF'
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

internal_packages = [
    "animica",
    "core",
    "rpc",
    "p2p",
    "consensus",
    "execution",
    "mining",
    "wallet",
    "mempool",
    "pq",
    "randomness",
]

hiddenimports = []
datas = []
binaries = []

for package in internal_packages:
    hiddenimports += collect_submodules(package)
    datas += collect_data_files(package, include_py_files=True)

# Ensure RPC method modules are bundled explicitly (PyInstaller + dynamic imports)
hiddenimports += collect_submodules("rpc")
hiddenimports += collect_submodules("rpc.methods")
datas += collect_data_files("rpc", include_py_files=True)

hiddenimports += [
    "httpx",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "fastapi",
    "starlette",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.responses",
    "starlette.routing",
    "pydantic",
    "pydantic_core",
    "typing_extensions",
    "anyio",
    "sniffio",
    "h11",
    "httpcore",
    "httptools",
    "websockets",
    "watchfiles",
    "typer",
]

for package in ('fastapi', 'starlette', 'uvicorn'):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(Path(r'ENTRY_POINT_PLACEHOLDER'))],
    pathex=[str(Path(r'REPO_ROOT_PLACEHOLDER'))],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'test', 'unittest', 'matplotlib', 'PIL'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='animica-node',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='animica-node',
)
SPEC_EOF

# Replace placeholders
sed -i.bak "s|ENTRY_POINT_PLACEHOLDER|$ENTRY_POINT|g" "$SPEC_FILE"
sed -i.bak "s|REPO_ROOT_PLACEHOLDER|$NODE_PKG_DIR|g" "$SPEC_FILE"
rm -f "$SPEC_FILE.bak"

# ============================================================================
# Build with PyInstaller
# ============================================================================

log "Running PyInstaller..."
"$PY" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$DIST_SUBDIR" \
    --workpath "$WORK_DIR" \
    "$SPEC_FILE"

# ============================================================================
# Locate and verify output
# ============================================================================

# PyInstaller creates: dist/animica-node/animica-node (onedir)
BUILT_DIR="$DIST_SUBDIR/animica-node"
BUILT_BINARY="$BUILT_DIR/animica-node"

if [[ ! -f "$BUILT_BINARY" ]]; then
    die "Build failed: expected binary not found at $BUILT_BINARY"
fi

verify_executable "$BUILT_BINARY"

# Build-time preflight checks (runtime imports + basic CLI)
log "Running node preflight checks..."
"$BUILT_BINARY" --help >/dev/null 2>&1 || die "Node --help preflight failed"
"$BUILT_BINARY" --preflight-imports || die "Node --preflight-imports failed"

log "Running RPC runtime preflight..."
RUNTIME_DATA_DIR="$OUT_DIR/preflight-data"
mkdir -p "$RUNTIME_DATA_DIR"
TOKEN_FILE="$(mktemp)"
python - <<'PY' "$TOKEN_FILE"
import secrets
import sys
token_path = sys.argv[1]
with open(token_path, "w", encoding="utf-8") as handle:
    handle.write(secrets.token_hex(32))
PY

PORT="$(python - <<'PY'
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
sock.close()
print(port)
PY
)"

"$BUILT_BINARY" \
    --rpc-bind 127.0.0.1 \
    --rpc-port "$PORT" \
    --rpc-auth-token-file "$TOKEN_FILE" \
    --data-dir "$RUNTIME_DATA_DIR" \
    --log-file "$OUT_DIR/preflight-node.log" \
    >/dev/null 2>&1 &
NODE_PID=$!

RPC_PAYLOAD='{"jsonrpc":"2.0","id":1,"method":"rpc.methods","params":[]}'
RPC_RESP="$(mktemp)"
RPC_OK="0"
for _ in $(seq 1 50); do
    HTTP_CODE="$(curl -s -o "$RPC_RESP" -w "%{http_code}" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
        -d "$RPC_PAYLOAD" \
        "http://127.0.0.1:$PORT/rpc" || true)"
    if [[ "$HTTP_CODE" == "200" ]]; then
        RPC_OK="1"
        break
    fi
    sleep 0.2
done

if [[ "$RPC_OK" != "1" ]]; then
    kill "$NODE_PID" >/dev/null 2>&1 || true
    wait "$NODE_PID" >/dev/null 2>&1 || true
    die "RPC runtime preflight failed: rpc.methods unavailable (HTTP $HTTP_CODE)"
fi

python - <<'PY' "$RPC_RESP"
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)

methods = payload.get("result") or []
required_all = {"chain.getHead"}
required_any = {
    "sync": {"sync.getStatus", "sync.dump"},
    "peers": {"net.peers", "net.getPeers", "peer.list", "p2p.peers"},
}

missing_all = sorted(required_all - set(methods))
missing_any = {
    key: sorted(list(values))
    for key, values in required_any.items()
    if not set(values).intersection(methods)
}

if missing_all or missing_any:
    raise SystemExit(f"RPC runtime preflight missing methods: {missing_all} {missing_any}")
print(f"RPC runtime preflight OK ({len(methods)} methods)")
PY

kill "$NODE_PID" >/dev/null 2>&1 || true
wait "$NODE_PID" >/dev/null 2>&1 || true
rm -f "$TOKEN_FILE" "$RPC_RESP"

# Validate PyInstaller runtime layout
INTERNAL_DIR="$BUILT_DIR/_internal"
if [[ ! -d "$INTERNAL_DIR" ]]; then
    die "Build failed: missing PyInstaller runtime directory at $INTERNAL_DIR"
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
    if ! ls "$INTERNAL_DIR"/libpython*.dylib >/dev/null 2>&1; then
        die "Build failed: missing libpython dylib in $INTERNAL_DIR"
    fi
else
    if ! ls "$INTERNAL_DIR"/libpython*.so* >/dev/null 2>&1; then
        die "Build failed: missing libpython shared library in $INTERNAL_DIR"
    fi
fi

# ============================================================================
# Create manifest
# ============================================================================

MANIFEST_FILE="$OUT_DIR/animica-node.manifest.json"
create_manifest "$MANIFEST_FILE" "animica-node" "$VERSION"

# ============================================================================
# Clean up intermediate files
# ============================================================================

log "Cleaning up intermediate build files..."
safe_rm_rf "$WORK_DIR"
safe_rm_rf "$SPEC_FILE"
safe_rm_rf "$ENTRY_POINT"
safe_rm_rf "$VENV_DIR"

# ============================================================================
# Success
# ============================================================================

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  Binary:   $BUILT_BINARY"
log "  Runtime:  $INTERNAL_DIR"
log "  Manifest: $MANIFEST_FILE"
log ""
log "Test:"
log "  $BUILT_BINARY --help"
