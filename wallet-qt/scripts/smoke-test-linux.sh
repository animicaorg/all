#!/bin/bash
# smoke-test-linux.sh - Smoke test for Linux AnimicaWallet
#
# Tests:
# 1. Node binary exists and runs
# 2. Node starts and RPC becomes reachable
# 3. Node responds to status queries
# 4. Node shuts down cleanly
#
# Usage:
#   ./scripts/smoke-test-linux.sh <path-to-executable-or-appimage-or-tarball>

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path-to-executable-or-appimage-or-tarball>"
    echo "Example: $0 ./build/linux/bin/animica-wallet"
    echo "Example: $0 ./AnimicaWallet-v0.1.0-linux-x86_64.AppImage"
    echo "Example: $0 ./AnimicaWallet-v0.1.0-linux-x86_64.tar.gz"
    exit 1
fi

WALLET_PATH="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"

if [ ! -f "$WALLET_PATH" ]; then
    echo "Error: Wallet not found: $WALLET_PATH"
    exit 1
fi

echo "======================================"
echo "Linux Wallet Smoke Test"
echo "======================================"
echo "Wallet: $WALLET_PATH"
echo ""

# Determine artifact type
IS_APPIMAGE=false
IS_TARBALL=false
if echo "$WALLET_PATH" | grep -q "\.AppImage$"; then
    IS_APPIMAGE=true
    echo "Detected AppImage format"
elif echo "$WALLET_PATH" | grep -Eq "\.(tar\.gz|tgz)$"; then
    IS_TARBALL=true
    echo "Detected portable tarball format"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/linux-layout.sh"

print_linux_root_candidates() {
    local root="$1"
    while IFS= read -r candidate; do
        echo "  - $candidate" >&2
    done < <(list_linux_node_root_candidates_from_root "$root")
}

print_linux_wallet_candidates() {
    local wallet_bin="$1"
    while IFS= read -r candidate; do
        echo "  - $candidate" >&2
    done < <(list_linux_node_root_candidates_from_wallet "$wallet_bin")
}

NODE_ROOT=""
NODE_PYTHON=""
NODE_PID=""

# Test 1: Check node binary exists
echo "[1/5] Checking node binary..."

if [ "$IS_APPIMAGE" = true ]; then
    # For AppImage, we need to extract it first
    echo "Extracting AppImage to check contents..."
    EXTRACT_DIR="$(mktemp -d /tmp/animica-appimage-XXXXXX)"
    (
        cd "$EXTRACT_DIR"
        "$WALLET_PATH" --appimage-extract > /dev/null 2>&1 || true
    )
    
    if [ -d "$EXTRACT_DIR/squashfs-root" ]; then
        python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform linux --path "$EXTRACT_DIR/squashfs-root"
        if ! NODE_ROOT="$(resolve_linux_node_root_from_root "$EXTRACT_DIR/squashfs-root")"; then
            echo "❌ FAIL: Could not resolve bundled node root inside extracted AppImage" >&2
            print_linux_root_candidates "$EXTRACT_DIR/squashfs-root"
            exit 1
        fi
        NODE_PYTHON="$NODE_ROOT/venv/bin/python"
    else
        echo "❌ FAIL: Could not extract AppImage"
        exit 1
    fi
    
    # Cleanup function for AppImage
    cleanup_appimage() {
        rm -rf "$EXTRACT_DIR"
    }
    trap cleanup_appimage EXIT
elif [ "$IS_TARBALL" = true ]; then
    EXTRACT_DIR="$(mktemp -d /tmp/animica-tarball-XXXXXX)"
    tar -xzf "$WALLET_PATH" -C "$EXTRACT_DIR"

    EXTRACT_ROOT="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -1)"
    if [ -z "$EXTRACT_ROOT" ] || [ ! -d "$EXTRACT_ROOT" ]; then
        echo "❌ FAIL: Could not determine extracted tarball root"
        exit 1
    fi

    python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform linux --path "$EXTRACT_ROOT"
    if ! NODE_ROOT="$(resolve_linux_node_root_from_root "$EXTRACT_ROOT")"; then
        echo "❌ FAIL: Could not resolve bundled node root inside extracted tarball" >&2
        print_linux_root_candidates "$EXTRACT_ROOT"
        exit 1
    fi
    NODE_PYTHON="$NODE_ROOT/venv/bin/python"

    cleanup_tarball() {
        rm -rf "$EXTRACT_DIR"
    }
    trap cleanup_tarball EXIT
else
    VERIFY_ROOT="$(dirname "$WALLET_PATH")/.."
    python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform linux --path "$VERIFY_ROOT"

    if ! NODE_ROOT="$(resolve_linux_node_root_from_wallet "$WALLET_PATH")"; then
        echo "❌ FAIL: Could not resolve bundled node root relative to $WALLET_PATH" >&2
        print_linux_wallet_candidates "$WALLET_PATH"
        exit 1
    fi
    NODE_PYTHON="$NODE_ROOT/venv/bin/python"
fi

if [ ! -f "$NODE_PYTHON" ]; then
    echo "❌ FAIL: Node Python not found at $NODE_PYTHON"
    exit 1
fi

if [ ! -x "$NODE_PYTHON" ]; then
    echo "❌ FAIL: Node Python is not executable"
    exit 1
fi

echo "✓ Node binary exists and is executable"
echo "✓ Bundled node root: $NODE_ROOT"
echo ""

# Test 2: Check node version and imports
echo "[2/5] Testing node imports..."
if ! "$NODE_PYTHON" --version; then
    echo "❌ FAIL: Node Python --version failed"
    exit 1
fi

if ! "$NODE_PYTHON" -c "import sys; import rpc; import animica.qt_wallet_bridge; import animica.wallet_qr; import omni_sdk; import core; print('All imports OK')" 2>&1; then
    echo "❌ FAIL: Node imports failed"
    exit 1
fi

echo "✓ Node imports successful"
echo ""

# Test 3: Start node and check RPC
echo "[3/5] Starting node..."

# Use a temporary datadir for testing
TEST_DATADIR="/tmp/animica-smoke-test-$$"
mkdir -p "$TEST_DATADIR"
TEST_HOME="$TEST_DATADIR/home"
mkdir -p "$TEST_HOME"

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    if [ -n "$NODE_PID" ] && kill -0 "$NODE_PID" 2>/dev/null; then
        echo "Stopping node (PID $NODE_PID)..."
        kill "$NODE_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$NODE_PID" 2>/dev/null || true
    fi
    rm -rf "$TEST_DATADIR"
    
    if [ "$IS_APPIMAGE" = true ]; then
        cleanup_appimage
    elif [ "$IS_TARBALL" = true ]; then
        cleanup_tarball
    fi
}

trap cleanup EXIT

# Start node in background
RPC_PORT=18545  # Use non-standard port to avoid conflicts
HOME="$TEST_HOME" "$NODE_PYTHON" -m rpc \
    --host 127.0.0.1 \
    --port $RPC_PORT \
    --chain-id 1337 \
    --datadir "$TEST_DATADIR" \
    --log-level INFO \
    > "$TEST_DATADIR/node.log" 2>&1 &

NODE_PID=$!
echo "Node started with PID $NODE_PID"

# Wait for node to become ready
echo "Waiting for node RPC to become ready..."
MAX_WAIT=30
WAITED=0

while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -s -f "http://127.0.0.1:$RPC_PORT/health" > /dev/null 2>&1; then
        echo "✓ Node RPC is ready"
        break
    fi
    
    # Check if process is still running
    if ! kill -0 "$NODE_PID" 2>/dev/null; then
        echo "❌ FAIL: Node process died"
        echo "Last 20 lines of log:"
        tail -20 "$TEST_DATADIR/node.log"
        exit 1
    fi
    
    sleep 1
    WAITED=$((WAITED + 1))
    echo -n "."
done
echo ""

if [ $WAITED -ge $MAX_WAIT ]; then
    echo "❌ FAIL: Node RPC did not become ready within ${MAX_WAIT}s"
    echo "Last 20 lines of log:"
    tail -20 "$TEST_DATADIR/node.log"
    exit 1
fi

echo ""

# Test 4: Query node status
echo "[4/5] Testing node RPC calls..."

# Test /health endpoint
HEALTH_RESPONSE=$(curl -s -f "http://127.0.0.1:$RPC_PORT/health" || echo "ERROR")
if [ "$HEALTH_RESPONSE" = "ERROR" ]; then
    echo "❌ FAIL: /health endpoint failed"
    exit 1
fi
echo "✓ /health: $HEALTH_RESPONSE"

# Test /status endpoint
STATUS_RESPONSE=$(curl -s -f "http://127.0.0.1:$RPC_PORT/status" || echo "ERROR")
if [ "$STATUS_RESPONSE" = "ERROR" ]; then
    echo "❌ FAIL: /status endpoint failed"
    exit 1
fi

# Parse chain ID from status (basic check)
CHAIN_ID=$(echo "$STATUS_RESPONSE" | grep -o '"chain_id":[0-9]*' | cut -d: -f2 || echo "")
if [ "$CHAIN_ID" != "1337" ]; then
    echo "❌ FAIL: Expected chain_id 1337, got: $CHAIN_ID"
    exit 1
fi
echo "✓ /status: chain_id=$CHAIN_ID"

echo ""

# Test 5: Clean shutdown
echo "[5/5] Testing clean shutdown..."
kill "$NODE_PID"
sleep 2

if kill -0 "$NODE_PID" 2>/dev/null; then
    echo "Warning: Node did not stop gracefully, forcing..."
    kill -9 "$NODE_PID" 2>/dev/null || true
    sleep 1
fi

if kill -0 "$NODE_PID" 2>/dev/null; then
    echo "❌ FAIL: Node process still running after shutdown"
    exit 1
fi

NODE_PID=""  # Prevent cleanup from trying again
echo "✓ Node shutdown successful"

echo ""
echo "======================================"
echo "✅ All smoke tests passed!"
echo "======================================"
echo ""
echo "The wallet is ready for distribution."
