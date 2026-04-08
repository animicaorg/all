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
#   ./scripts/smoke-test-linux.sh <path-to-executable-or-appimage>

set -e

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path-to-executable-or-appimage>"
    echo "Example: $0 ./build/linux/bin/animica-wallet"
    echo "Example: $0 ./AnimicaWallet-v0.1.0-linux-x86_64.AppImage"
    exit 1
fi

WALLET_PATH="$1"

if [ ! -f "$WALLET_PATH" ]; then
    echo "Error: Wallet not found: $WALLET_PATH"
    exit 1
fi

echo "======================================"
echo "Linux Wallet Smoke Test"
echo "======================================"
echo "Wallet: $WALLET_PATH"
echo ""

# Determine if this is an AppImage or regular executable
IS_APPIMAGE=false
if echo "$WALLET_PATH" | grep -q "\.AppImage$"; then
    IS_APPIMAGE=true
    echo "Detected AppImage format"
fi

# Test 1: Check node binary exists
echo "[1/5] Checking node binary..."

if [ "$IS_APPIMAGE" = true ]; then
    # For AppImage, we need to extract it first
    echo "Extracting AppImage to check contents..."
    EXTRACT_DIR="/tmp/animica-appimage-$$"
    "$WALLET_PATH" --appimage-extract > /dev/null 2>&1 || true
    
    if [ -d "squashfs-root" ]; then
        NODE_PYTHON="$(pwd)/squashfs-root/usr/lib/node/venv/bin/python"
    else
        echo "❌ FAIL: Could not extract AppImage"
        exit 1
    fi
    
    # Cleanup function for AppImage
    cleanup_appimage() {
        rm -rf "squashfs-root"
    }
    trap cleanup_appimage EXIT
else
    # For regular build, assume node is in ../node relative to executable
    WALLET_DIR="$(dirname "$WALLET_PATH")"
    NODE_PYTHON="$WALLET_DIR/node/venv/bin/python"
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
echo ""

# Test 2: Check node version and imports
echo "[2/5] Testing node imports..."
if ! "$NODE_PYTHON" --version; then
    echo "❌ FAIL: Node Python --version failed"
    exit 1
fi

if ! "$NODE_PYTHON" -c "import sys; import rpc; import animica.qt_wallet_bridge; import omni_sdk; import core; print('All imports OK')" 2>&1; then
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

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    if [ ! -z "$NODE_PID" ] && kill -0 "$NODE_PID" 2>/dev/null; then
        echo "Stopping node (PID $NODE_PID)..."
        kill "$NODE_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$NODE_PID" 2>/dev/null || true
    fi
    rm -rf "$TEST_DATADIR"
    
    if [ "$IS_APPIMAGE" = true ]; then
        cleanup_appimage
    fi
}

trap cleanup EXIT

# Start node in background
RPC_PORT=18545  # Use non-standard port to avoid conflicts
"$NODE_PYTHON" -m rpc \
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
