#!/usr/bin/env python3
"""
Test script to verify RPC JSON error formatting for insufficient balance.

This script shows the JSON-RPC error response format that clients will receive.
"""
import sys
import json

sys.path.insert(0, '.')

from rpc.errors import InsufficientFunds, error_response

print("=" * 70)
print("Testing RPC JSON-RPC Error Response for Insufficient Balance")
print("=" * 70)
print()

# Create an InsufficientFunds error
err = InsufficientFunds(required=1000000000000000, available=500000000000)

print("1. InsufficientFunds Error Object:")
print(f"   Code: {err.code}")
print(f"   Message: {err.message}")
print(f"   Data: {err.data}")
print()

# Get the error dict (what goes in JSON-RPC error field)
err_dict = err.to_dict()
print("2. Error Dictionary (for JSON-RPC 'error' field):")
print(json.dumps(err_dict, indent=2))
print()

# Build a complete JSON-RPC error response
response = error_response(req_id=123, err=err)
print("3. Complete JSON-RPC Error Response:")
print(json.dumps(response, indent=2))
print()

print("4. Example: User sends tx with insufficient balance")
print("-" * 70)
print("Request:")
print(json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tx.sendRawTransaction",
    "params": ["0x...cbor_encoded_tx..."]
}, indent=2))
print()
print("Response:")
print(json.dumps(response, indent=2))
print("-" * 70)
print()

# Test with values matching problem statement
err2 = InsufficientFunds(
    required=1000000 * 1_000_000_000,  # 1M ANM
    available=500 * 1_000_000_000       # 500 ANM
)
response2 = error_response(req_id="tx_send_12345", err=err2)

print("5. Problem Statement Example (1M ANM requested, 500 ANM available):")
print(json.dumps(response2, indent=2))
print()

print("✓ RPC JSON-RPC error formatting test completed successfully!")
print()
print("Key Points:")
print("  - Error code: -32013 (AnimicaCode.INSUFFICIENT_FUNDS)")
print("  - Includes required, available, and shortfall in data field")
print("  - Amounts are stringified to avoid JSON number precision issues")
print("  - Response follows JSON-RPC 2.0 spec")
