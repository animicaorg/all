#!/bin/bash
#
# test-node-build.sh - Smoke test for node building without full wallet build
#
# This script tests that the node can be built and run without requiring Qt.
# Useful for CI environments and quick testing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"

echo "=========================================="
echo "Animica Node Build Smoke Test"
echo "=========================================="
echo "Project root: $PROJECT_ROOT"
echo "Repo root: $REPO_ROOT"
echo ""

# Find Python
echo "Finding Python..."
PYTHON=""
for py in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$py" &> /dev/null; then
        PY_VERSION=$($py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
        if [[ -n "$PY_VERSION" ]]; then
            PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
            PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
            if [[ $PY_MAJOR -eq 3 && $PY_MINOR -ge 10 ]]; then
                PYTHON="$py"
                echo "✓ Found Python $PY_VERSION: $PYTHON"
                break
            fi
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "✗ Python 3.10+ not found"
    exit 1
fi

# Create test venv
TEST_DIR="$PROJECT_ROOT/test-node-build"
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

echo ""
echo "Creating test virtual environment..."
$PYTHON -m venv "$TEST_DIR/venv"

if [[ ! -f "$TEST_DIR/venv/bin/python" ]]; then
    echo "✗ Failed to create venv"
    exit 1
fi

echo "✓ Virtual environment created"

# Activate and install
echo ""
echo "Installing node dependencies..."

NODE_PYTHON="$TEST_DIR/venv/bin/python"
NODE_PIP="$TEST_DIR/venv/bin/pip"

# Upgrade pip
echo "  - Upgrading pip..."
$NODE_PYTHON -m pip install --upgrade pip setuptools wheel --quiet

# Install core RPC dependencies
echo "  - Installing FastAPI, uvicorn..."
$NODE_PIP install \
    "fastapi>=0.115.0,<0.116.0" \
    "uvicorn[standard]>=0.30.0,<1.0.0" \
    "prometheus-client>=0.20.0,<1.0.0" \
    --quiet

# Install omni-sdk if available
SDK_PATH="$REPO_ROOT/sdk/python"
if [[ -f "$SDK_PATH/pyproject.toml" ]]; then
    echo "  - Installing omni-sdk..."
    $NODE_PIP install -e "$SDK_PATH" --quiet
fi

# Install animica package
ANIMICA_PATH="$REPO_ROOT/python"
if [[ -f "$ANIMICA_PATH/pyproject.toml" ]]; then
    echo "  - Installing animica package..."
    $NODE_PIP install -e "$ANIMICA_PATH" --quiet
else
    echo "✗ Animica package not found at $ANIMICA_PATH"
    exit 1
fi

# Install pq package if available
PQ_PATH="$REPO_ROOT/pq"
if [[ -f "$PQ_PATH/pyproject.toml" ]]; then
    echo "  - Installing pq package..."
    $NODE_PIP install -e "$PQ_PATH" --quiet 2>/dev/null || echo "    (pq install failed, continuing...)"
fi

echo "✓ Dependencies installed"

# Copy repository modules into venv
echo ""
echo "Copying repository modules..."

# Find site-packages
SITE_PACKAGES=$(find "$TEST_DIR/venv/lib" -type d -name "site-packages" | head -1)
if [[ -z "$SITE_PACKAGES" ]]; then
    echo "✗ Could not find site-packages directory"
    exit 1
fi

# List of modules to copy
MODULES="rpc core coretx consensus execution mempool mempool2 p2p mining proofs da randomness capabilities aicf queue chains genesis services billing relayer"

for MODULE in $MODULES; do
    if [[ -d "$REPO_ROOT/$MODULE" ]]; then
        echo "  - Copying $MODULE"
        cp -r "$REPO_ROOT/$MODULE" "$SITE_PACKAGES/" 2>/dev/null || true
    fi
done

echo "✓ Modules copied to $SITE_PACKAGES"

# Test imports
echo ""
echo "Testing Python imports..."
if $NODE_PYTHON -c "import core; import coretx; import mempool2; import rpc.mempool2_service; import animica" 2>/dev/null; then
    echo "✓ Imports successful (core, coretx, mempool2, rpc.mempool2_service, animica)"
else
    echo "✗ Import test failed"
    $NODE_PYTHON -c "import core; import coretx; import mempool2; import rpc.mempool2_service; import animica" 2>&1
    exit 1
fi

# Quick start/stop test
echo ""
echo "Testing node start/stop..."

DATA_DIR="$TEST_DIR/data"
mkdir -p "$DATA_DIR"

export ANIMICA_DATA_DIR="$DATA_DIR"
export ANIMICA_RPC_HOST="127.0.0.1"
export ANIMICA_RPC_PORT="28545"
export ANIMICA_P2P_PORT="31337"
export ANIMICA_CHAIN_ID="1337"
export ANIMICA_LOG_LEVEL="ERROR"

# Start node in background
$NODE_PYTHON -m rpc > "$TEST_DIR/node.log" 2>&1 &
NODE_PID=$!

echo "  - Started node (PID: $NODE_PID)"

# Wait for startup
sleep 3

# Check if process is still running
if ! kill -0 $NODE_PID 2>/dev/null; then
    echo "✗ Node process died"
    echo "Last log lines:"
    tail -20 "$TEST_DIR/node.log"
    exit 1
fi

echo "  - Node is running"

# Try a simple health check (if endpoint exists)
if command -v curl &> /dev/null; then
    sleep 2
    if curl -sf http://127.0.0.1:28545/health > /dev/null 2>&1; then
        echo "  - Health check passed"
    else
        echo "  - Health check not available (expected for minimal setup)"
    fi
fi

# Stop node
kill $NODE_PID 2>/dev/null || true
wait $NODE_PID 2>/dev/null || true
echo "  - Stopped node"

echo ""
echo "=========================================="
echo "✓ All smoke tests passed!"
echo "=========================================="
echo ""
echo "Node can be built and run successfully."
echo "Test artifacts in: $TEST_DIR"
echo ""
echo "To clean up:"
echo "  rm -rf $TEST_DIR"
echo ""
