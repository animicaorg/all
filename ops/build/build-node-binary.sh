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

# ============================================================================
# Setup build environment
# ============================================================================

setup_python_build_env

# Install build dependencies
log "Installing build dependencies..."
pip_install "$PY" pip setuptools wheel pyinstaller pyinstaller-hooks-contrib

# Determine Python project directory using shared helper
# The function provides detailed error messages if not found
PYTHON_PKG_DIR="$(find_python_package_dir "$REPO_ROOT")" || \
    die "Python project not found. Please ensure the repository contains a valid pyproject.toml at 'python/' or repo root."

log "Using Python project root: $PYTHON_PKG_DIR"

# Install repo in editable mode (ensures all dependencies are available)
log "Installing Animica in editable mode..."
"$PY" -m pip install --quiet -e "$PYTHON_PKG_DIR"

# ============================================================================
# Determine build method (PyInstaller for Python-based node)
# ============================================================================

# The node is Python-based, entry point is in the detected package directory
# We'll use PyInstaller to create a standalone binary

ENTRY_POINT="$OUT_DIR/animica-node-entry.py"
cat > "$ENTRY_POINT" <<'PYEOF'
import sys

from animica.cli.main import main as cli_main
from rpc.server import main as rpc_main


def _should_run_rpc(argv: list[str]) -> bool:
    rpc_flags = {
        "--rpc-bind",
        "--rpc-port",
        "--rpc-auth-token-file",
        "--data-dir",
        "--log-file",
    }
    return any(arg.split("=", 1)[0] in rpc_flags for arg in argv[1:])


if _should_run_rpc(sys.argv):
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

block_cipher = None

# Hidden imports for all node functionality
hiddenimports = [
    'animica',
    'animica.cli',
    'animica.cli.main',
    'animica.cli.node',
    'animica.cli.mining',
    'animica.cli.wallet',
    'animica.cli.tx',
    'animica.cli.chain',
    'animica.cli.rpc',
    'animica.cli.p2p',
    'animica.cli.peer',
    'animica.cli.sync',
    'animica.cli.mempool',
    'animica.cli.network',
    'animica.cli.key',
    'animica.cli.balance',
    'animica.config',
    'animica.bootstrap',
    'animica.seeds',
    'core',
    'core.db',
    'core.state',
    'consensus',
    'execution',
    'mining',
    'mining.cli',
    'mining.cli.miner',
    'p2p',
    'p2p.protocol',
    'p2p.discovery',
    'mempool',
    'rpc',
    'rpc.server',
    'rpc.jsonrpc',
    'rpc.ws',
    'rpc.openrpc_mount',
    'rpc.metrics',
    'rpc.middleware',
    'rpc.methods',
    'rpc.methods.bootstrap',
    'rpc.methods.net',
    'rpc.methods.node',
    'rpc.methods.tx',
    'rpc.methods.receipt',
    'rpc.methods.state',
    'rpc.methods.chain',
    'rpc.methods.sync',
    'rpc.methods.miner',
    'rpc.methods.mempool',
    'rpc.methods.ptl',
    'rpc.methods.da',
    'rpc.methods.faucet',
    'rpc.methods.p2p',
    'rpc.methods.snapshot',
    'rpc.methods.marketplace',
    'rpc.methods.admin',
    'wallet',
    'pq',
    'pq.dilithium',
    'httpx',
    'uvicorn',
    'fastapi',
    'pydantic',
    'typer',
]

a = Analysis(
    [str(Path(r'ENTRY_POINT_PLACEHOLDER'))],
    pathex=[str(Path(r'REPO_ROOT_PLACEHOLDER'))],
    binaries=[],
    datas=[],
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
sed -i.bak "s|REPO_ROOT_PLACEHOLDER|$REPO_ROOT|g" "$SPEC_FILE"
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
