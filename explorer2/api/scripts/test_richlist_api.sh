#!/bin/bash
# Test script for rich list API endpoints
# Usage: ./test_richlist_api.sh [API_URL]
#
# This script tests the rich list endpoints to verify they work correctly.

API_URL="${1:-http://localhost:8081}"

echo "Testing Rich List API endpoints against: $API_URL"
echo "================================================"
echo ""

# Test 1: Health check
echo "1. Testing health endpoint..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/api/health")
if [ "$HTTP_CODE" = "200" ]; then
  echo "   ✓ Health check passed"
else
  echo "   ✗ Health check failed (HTTP $HTTP_CODE)"
  exit 1
fi
echo ""

# Test 2: Diagnostics
echo "2. Checking diagnostics..."
DIAG=$(curl -s "$API_URL/api/diagnostics")
MODE=$(echo "$DIAG" | jq -r '.mode' 2>/dev/null)
echo "   Connection mode: $MODE"
echo ""

# Test 3: Rich list endpoint
echo "3. Testing /api/richlist endpoint..."
RICHLIST=$(curl -s -w "\n%{http_code}" "$API_URL/api/richlist?limit=10&offset=0")
HTTP_CODE=$(echo "$RICHLIST" | tail -n1)
BODY=$(echo "$RICHLIST" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
  echo "   ✓ Rich list endpoint returned 200 OK"
  HEIGHT=$(echo "$BODY" | jq -r '.height' 2>/dev/null)
  TOTAL=$(echo "$BODY" | jq -r '.totalAddresses' 2>/dev/null)
  ITEMS=$(echo "$BODY" | jq -r '.items | length' 2>/dev/null)
  echo "   Height: $HEIGHT"
  echo "   Total addresses: $TOTAL"
  echo "   Items returned: $ITEMS"
  
  if [ "$ITEMS" != "0" ] && [ "$ITEMS" != "null" ]; then
    echo "   ✓ Rich list contains data"
    # Show first entry
    FIRST=$(echo "$BODY" | jq -r '.items[0]' 2>/dev/null)
    echo "   Top address:"
    echo "$FIRST" | jq '.' 2>/dev/null | sed 's/^/     /'
  else
    echo "   ℹ Rich list is empty (no addresses with balance)"
  fi
elif [ "$HTTP_CODE" = "501" ]; then
  echo "   ⚠ Rich list endpoint returned 501 Not Implemented"
  ERROR=$(echo "$BODY" | jq -r '.message' 2>/dev/null)
  echo "   Error: $ERROR"
  echo ""
  echo "   This means the node does not support state.getRichList RPC method."
  echo "   To fix this:"
  echo "   1. Ensure you're running a node with rich list support"
  echo "   2. Check that rpc/methods/state.py has the @method decorator for state.getRichList"
  echo "   3. Restart the node and API"
  exit 2
else
  echo "   ✗ Rich list endpoint failed (HTTP $HTTP_CODE)"
  echo "$BODY" | jq '.' 2>/dev/null
  exit 1
fi
echo ""

# Test 4: Rich list summary endpoint
echo "4. Testing /api/richlist/summary endpoint..."
SUMMARY=$(curl -s -w "\n%{http_code}" "$API_URL/api/richlist/summary")
HTTP_CODE=$(echo "$SUMMARY" | tail -n1)
BODY=$(echo "$SUMMARY" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
  echo "   ✓ Rich list summary endpoint returned 200 OK"
  HEIGHT=$(echo "$BODY" | jq -r '.height' 2>/dev/null)
  SUPPLY=$(echo "$BODY" | jq -r '.totalSupply' 2>/dev/null)
  COUNT=$(echo "$BODY" | jq -r '.addressCount' 2>/dev/null)
  TOP10=$(echo "$BODY" | jq -r '.top10Pct' 2>/dev/null)
  echo "   Height: $HEIGHT"
  echo "   Total supply: $SUPPLY"
  echo "   Address count: $COUNT"
  echo "   Top 10 hold: $TOP10%"
elif [ "$HTTP_CODE" = "501" ]; then
  echo "   ⚠ Rich list summary endpoint returned 501 Not Implemented"
  ERROR=$(echo "$BODY" | jq -r '.message' 2>/dev/null)
  echo "   Error: $ERROR"
  echo ""
  echo "   This means the node does not support state.getTotalSupply RPC method."
  echo "   To fix this:"
  echo "   1. Ensure you're running a node with rich list support"
  echo "   2. Check that rpc/methods/state.py has the @method decorator for state.getTotalSupply"
  echo "   3. Restart the node and API"
  exit 2
else
  echo "   ✗ Rich list summary endpoint failed (HTTP $HTTP_CODE)"
  echo "$BODY" | jq '.' 2>/dev/null
  exit 1
fi
echo ""

echo "================================================"
echo "✓ All rich list tests passed!"
echo ""
echo "You can now view the rich list in the web UI."
echo "The web UI typically runs on a different port (e.g., http://localhost:3001/richlist)"
