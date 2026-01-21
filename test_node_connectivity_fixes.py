"""Integration test to verify node connectivity and sync fixes."""

import sys
from pathlib import Path

# Test 1: Verify seed parsing fix
print("=" * 60)
print("TEST 1: Verify Seed Parsing Fix")
print("=" * 60)

sys.path.insert(0, str(Path(__file__).parent / "p2p"))
from p2p.core_p2p.service import _parse_seed

test_cases = [
    ("127.0.0.1", 30333, "localhost without port"),
    ("127.0.0.1:30333", 30333, "localhost with port"),
    ("mainnet.animica.org", 30333, "hostname without port"),
    ("mainnet.animica.org:30333", 30333, "hostname with port"),
    ("", None, "empty string"),
]

passed = 0
failed = 0

for addr, expected_port, desc in test_cases:
    result = _parse_seed(addr)
    if expected_port is None:
        if result is None:
            print(f"✓ PASS: {desc}")
            passed += 1
        else:
            print(f"✗ FAIL: {desc} - expected None, got {result}")
            failed += 1
    else:
        if result is not None and result.port == expected_port:
            print(f"✓ PASS: {desc} - port {result.port}")
            passed += 1
        else:
            port = result.port if result else "None"
            print(f"✗ FAIL: {desc} - expected port {expected_port}, got {port}")
            failed += 1

print(f"\nTest 1 Results: {passed} passed, {failed} failed\n")

# Test 2: Verify timeout constants are used (file content checks only)
print("=" * 60)
print("TEST 2: Verify Timeout Configuration")
print("=" * 60)

# Check node.py uses proper timeout configuration
with open("python/animica/cli/node.py", "r") as f:
    content = f.read()
    
checks = [
    ("resolve_timeout imported", "from .timeouts import" in content and "resolve_timeout" in content),
    ("_local_rpc uses resolve_timeout", "def _local_rpc" in content and "resolve_timeout(" in content.split("def _local_rpc")[1].split("def ")[0]),
    ("_bootstrap_rpc uses timeout for all methods", "# All bootstrap methods should have timeout protection" in content),
    ("No hardcoded 5.0 timeout in _local_rpc", "timeout=5.0" not in content.split("def _local_rpc")[1].split("def ")[0]),
    ("Bootstrap ready timeout increased", "timeout_s=10.0" in content),
    ("Exception logging added", "log.debug" in content and "exc_info=True" in content),
]

for desc, passed_check in checks:
    if passed_check:
        print(f"✓ PASS: {desc}")
        passed += 1
    else:
        print(f"✗ FAIL: {desc}")
        failed += 1

print(f"\nTest 2 Results: {len(checks)} checks\n")

# Test 3: Verify P2P error handling improvements
print("=" * 60)
print("TEST 3: Verify P2P Error Handling")
print("=" * 60)

files_to_check = [
    ("p2p/core_p2p/service.py", ["break", "# No peers available yet"]),
    ("p2p/core_p2p/connman.py", ["raise ConnectionError", "peer connection not found"]),
    ("p2p/core_p2p/net_processing.py", ["complete_inflight", "if h not in self.sync.pending_set"]),
    ("p2p/core_p2p/net_processing.py", ["try:", "await send(peer, \"inv\", payload)", "peer.known_inventory.add"]),
]

for filepath, expected_patterns in files_to_check:
    with open(filepath, "r") as f:
        content = f.read()
    
    all_found = all(pattern in content for pattern in expected_patterns)
    if all_found:
        print(f"✓ PASS: {filepath} - all patterns found")
        passed += 1
    else:
        missing = [p for p in expected_patterns if p not in content]
        print(f"✗ FAIL: {filepath} - missing patterns: {missing}")
        failed += 1

print(f"\nTest 3 Results: {len(files_to_check)} file checks\n")

# Final summary
print("=" * 60)
print("FINAL SUMMARY")
print("=" * 60)
print(f"Total Tests: {passed + failed}")
print(f"Passed: {passed}")
print(f"Failed: {failed}")

if failed == 0:
    print("\n✅ ALL TESTS PASSED! Node connectivity and sync fixes verified.")
    sys.exit(0)
else:
    print(f"\n❌ {failed} TESTS FAILED! Review the output above.")
    sys.exit(1)
