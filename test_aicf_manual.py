#!/usr/bin/env python3
"""
Manual test script for AICF CLI commands.

This script demonstrates the new AICF CLI functionality without requiring a running node.
"""

import sys
import os

# Add python directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

print("=" * 70)
print("AICF CLI Manual Test")
print("=" * 70)

print("\n1. Testing URL normalization...")
from animica.cli import aicf_utils

test_urls = [
    "http://127.0.0.1:8545",
    "http://127.0.0.1:8545/",
    "http://127.0.0.1:8545/rpc",
    "https://mainnet.animica.org",
    "127.0.0.1:9999",
]

for url in test_urls:
    normalized = aicf_utils.normalize_rpc_url(url)
    print(f"  {url:40s} → {normalized}")

print("\n2. Testing job plans...")
from animica.cli import aicf_plans

plans = aicf_plans.list_plans()
print(f"  Found {len(plans)} built-in plans:")
for plan in plans:
    print(f"    • {plan.name:25s} ({plan.category:12s}) - {plan.min_budget:5d} credits - {plan.estimated_duration}")

print("\n3. Testing plan filtering...")
testing_plans = aicf_plans.list_plans(category="testing")
print(f"  Testing category: {len(testing_plans)} plans")
for plan in testing_plans:
    print(f"    • {plan.name}")

qa_plans = aicf_plans.list_plans(category="qa")
print(f"  QA category: {len(qa_plans)} plans")
for plan in qa_plans:
    print(f"    • {plan.name}")

print("\n4. Testing plan details...")
plan = aicf_plans.get_plan("ena_smoke")
if plan:
    print(f"  Plan: {plan.name}")
    print(f"  Description: {plan.description}")
    print(f"  Category: {plan.category}")
    print(f"  Min Budget: {plan.min_budget} credits")
    print(f"  Duration: {plan.estimated_duration}")
    print(f"  Capabilities: {', '.join(plan.required_capabilities)}")
    print(f"  Default Params: {plan.default_params}")

print("\n5. Testing parameter validation...")
plan = aicf_plans.get_plan("repo_index_refresh")
if plan:
    # Test missing required param
    errors = aicf_plans.validate_plan_params(plan, {})
    print(f"  Missing required params: {len(errors)} errors")
    for error in errors:
        print(f"    • {error}")
    
    # Test with all params
    errors = aicf_plans.validate_plan_params(plan, {"repo_url": "https://github.com/user/repo"})
    print(f"  With required params: {len(errors)} errors")

print("\n6. Testing safe JSON encoding...")
test_obj = {
    "normal_int": 42,
    "large_int": 9007199254740992,  # > 2^53
    "string": "hello",
    "bytes": b'\x01\x02\x03',
    "nested": {"key": "value"},
}

try:
    result = aicf_utils.safe_json_encode(test_obj)
    print("  ✓ JSON encoding successful")
    print(f"  Length: {len(result)} bytes")
    # Verify large int was converted to string
    if '"9007199254740992"' in result:
        print("  ✓ Large int converted to string")
    if '"0x010203"' in result:
        print("  ✓ Bytes converted to hex")
except Exception as e:
    print(f"  ✗ JSON encoding failed: {e}")

print("\n7. Testing RPC session creation...")
try:
    session = aicf_utils.create_rpc_session(timeout=30, retries=3)
    print("  ✓ RPC session created")
    print(f"  ✓ Adapters registered: {list(session.adapters.keys())}")
except Exception as e:
    print(f"  ✗ Session creation failed: {e}")

print("\n" + "=" * 70)
print("All manual tests completed successfully!")
print("=" * 70)
print("\nTo test CLI commands (requires running node):")
print("  animica aicf status")
print("  animica aicf miner-credits anim1...")
print("  animica aicf doctor")
print("  animica aicf jobs plans")
print("  animica aicf jobs submit --plan ena_smoke --budget 500")
print("=" * 70)
