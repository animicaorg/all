#!/usr/bin/env bash
# =============================================================================
# Wait for Service Script
# =============================================================================
# Waits for a service to be available before continuing
# Usage: ./wait_for.sh <host> <port> [timeout]

set -euo pipefail

HOST="${1:-localhost}"
PORT="${2:-5432}"
TIMEOUT="${3:-60}"

echo "Waiting for $HOST:$PORT to be available (timeout: ${TIMEOUT}s)..."

start_time=$(date +%s)

while ! nc -z "$HOST" "$PORT" 2>/dev/null; do
    current_time=$(date +%s)
    elapsed=$((current_time - start_time))
    
    if [ $elapsed -ge "$TIMEOUT" ]; then
        echo "✗ Timeout waiting for $HOST:$PORT after ${TIMEOUT}s"
        exit 1
    fi
    
    echo "  Waiting... (${elapsed}s/${TIMEOUT}s)"
    sleep 2
done

echo "✓ $HOST:$PORT is available"
