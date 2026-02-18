#!/bin/bash
# Smoke test for ENA service

set -e

echo "==================================="
echo "ENA Service Smoke Test"
echo "==================================="

# Configuration
ENA_ENDPOINT="${ENA_ENDPOINT:-http://localhost:8080}"
export ENA_DEV_MODE=1  # Enable dev mode for testing

echo ""
echo "1. Starting ENA service in dev mode..."
python -m ena.services.ena_node.main &
ENA_PID=$!
sleep 5

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "Cleaning up..."
    kill $ENA_PID 2>/dev/null || true
}
trap cleanup EXIT

echo ""
echo "2. Testing health endpoint..."
curl -f "$ENA_ENDPOINT/v1/health" || {
    echo "Health check failed!"
    exit 1
}

echo ""
echo ""
echo "3. Testing pricing endpoint..."
curl -f "$ENA_ENDPOINT/v1/pricing" || {
    echo "Pricing endpoint failed!"
    exit 1
}

echo ""
echo ""
echo "4. Testing models endpoint..."
curl -f "$ENA_ENDPOINT/v1/models" || {
    echo "Models endpoint failed!"
    exit 1
}

echo ""
echo ""
echo "5. Testing inference (dev mode - no payment)..."
curl -f -X POST "$ENA_ENDPOINT/v1/infer" \
    -H "Content-Type: application/json" \
    -d '{
        "prompt": "Hello, world!",
        "max_tokens": 50,
        "payment": {
            "mode": "per_call_tx",
            "payer": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq000000",
            "tx_hash": "0x0000000000000000000000000000000000000000000000000000000000000001"
        }
    }' || {
    echo "Inference failed!"
    exit 1
}

echo ""
echo ""
echo "==================================="
echo "✓ All smoke tests passed!"
echo "==================================="
