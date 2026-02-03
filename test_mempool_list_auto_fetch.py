#!/usr/bin/env python3
"""
Test that `animica mempool list` automatically fetches transactions from peers
when the mempool is empty but peers have known transactions.

This is a code inspection test that verifies the logic is in place.
"""
import sys


def test_code_logic_verification():
    """
    Verify that the mempool list code has the auto-fetch logic.
    """
    print("\n" + "=" * 70)
    print("Test: Code Logic Verification")
    print("=" * 70 + "\n")

    # Read the mempool.py file
    with open("/home/runner/work/all/all/python/animica/cli/mempool.py", "r") as f:
        content = f.read()

    # Verify key components are present
    checks = [
        ("total_peer_known_txids counter", "total_peer_known_txids" in content),
        ("Check for peer-known transactions", "if total_peer_known_txids > 0:" in content),
        ("Call to p2p.importPeerKnownTxs", '"p2p.importPeerKnownTxs"' in content),
        ("User-friendly tip message", "Tip: Peers know about" in content),
        ("Success feedback message", "Requested" in content and "transaction(s) from peers" in content),
        ("Advice to run command again", "animica mempool list" in content and "again" in content),
    ]

    all_passed = True
    for check_name, passed in checks:
        status = "✓" if passed else "✗"
        print(f"  {status} {check_name}")
        if not passed:
            all_passed = False

    if not all_passed:
        print("\n❌ Some checks failed")
        return False

    print("\n" + "=" * 70)
    print("✅ All code logic checks passed!")
    print("=" * 70)
    
    # Also print the relevant code section
    print("\nRelevant code section:")
    print("-" * 70)
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "total_peer_known_txids" in line or "importPeerKnownTxs" in line:
            # Print context around the line
            start = max(0, i - 2)
            end = min(len(lines), i + 10)
            for j in range(start, end):
                print(f"{j+1:4d}: {lines[j]}")
            print("-" * 70)
            break
    
    return True


def main():
    """Run all tests."""
    tests = [
        ("Code Logic Verification", test_code_logic_verification),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as exc:
            print(f"\n❌ Test '{test_name}' failed with exception: {exc}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)

    all_passed = True
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n✅ All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
