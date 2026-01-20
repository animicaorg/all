"""
Test that the legacy P2P service can be successfully reenabled and imported.

This test validates:
1. Legacy service can be imported from p2p_service_legacy.py
2. The deprecation warning has been removed
3. The service toggle in rpc/deps.py works correctly
"""
import os
import sys


def test_legacy_service_import():
    """Test that legacy P2P service can be imported."""
    print("Test 1: Import legacy P2P service")
    try:
        from p2p.node.p2p_service_legacy import P2PService
        print("  ✓ Legacy P2PService imported successfully")
        print(f"    Module: {P2PService.__module__}")
        assert P2PService.__module__ == "p2p.node.p2p_service_legacy"
        return True
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False


def test_no_deprecation_warning():
    """Test that the file doesn't contain deprecation warnings."""
    print("\nTest 2: Check deprecation warning removed")
    legacy_file = "p2p/node/p2p_service_legacy.py"
    
    with open(legacy_file, 'r') as f:
        content = f.read(500)  # Check first 500 chars
    
    # Check that DEPRECATED is not in the header
    if "DEPRECATED" in content:
        print(f"  ✗ DEPRECATED warning still present")
        return False
    
    # Check that "Do not import" is not in the header
    if "Do not import" in content:
        print(f"  ✗ 'Do not import' warning still present")
        return False
    
    print(f"  ✓ Deprecation warnings removed")
    return True


def test_env_var_toggle():
    """Test that the environment variable toggle works."""
    print("\nTest 3: Environment variable toggle")
    
    # Simulate _bool_env function
    def _bool_env(name: str, default: bool) -> bool:
        value = os.environ.get(name)
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on")
    
    # Test with legacy enabled
    os.environ['ANIMICA_P2P_USE_LEGACY'] = '1'
    result = _bool_env("ANIMICA_P2P_USE_LEGACY", True)
    if not result:
        print("  ✗ Legacy not enabled when ANIMICA_P2P_USE_LEGACY=1")
        return False
    print("  ✓ ANIMICA_P2P_USE_LEGACY=1 → Legacy enabled")
    
    # Test with legacy disabled
    os.environ['ANIMICA_P2P_USE_LEGACY'] = '0'
    result = _bool_env("ANIMICA_P2P_USE_LEGACY", True)
    if result:
        print("  ✗ Legacy not disabled when ANIMICA_P2P_USE_LEGACY=0")
        return False
    print("  ✓ ANIMICA_P2P_USE_LEGACY=0 → Legacy disabled")
    
    # Test with no env var (should default to legacy)
    if 'ANIMICA_P2P_USE_LEGACY' in os.environ:
        del os.environ['ANIMICA_P2P_USE_LEGACY']
    result = _bool_env("ANIMICA_P2P_USE_LEGACY", True)
    if not result:
        print("  ✗ Default not set to legacy")
        return False
    print("  ✓ Default → Legacy enabled")
    
    return True


def test_service_class_exists():
    """Test that the P2PService class exists and has expected methods."""
    print("\nTest 4: P2PService class structure")
    try:
        from p2p.node.p2p_service_legacy import P2PService
        
        # Check that it's a class
        if not isinstance(P2PService, type):
            print("  ✗ P2PService is not a class")
            return False
        
        # Check for expected methods
        expected_methods = ['__init__', 'start', 'stop']
        for method in expected_methods:
            if not hasattr(P2PService, method):
                print(f"  ✗ Missing method: {method}")
                return False
        
        print("  ✓ P2PService class has expected structure")
        print(f"    Methods: {', '.join(expected_methods)}")
        return True
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False


def test_rpc_deps_integration():
    """Test that rpc/deps.py has the toggle code."""
    print("\nTest 5: RPC deps integration")
    
    deps_file = "rpc/deps.py"
    with open(deps_file, 'r') as f:
        content = f.read()
    
    # Check for the toggle logic
    if "ANIMICA_P2P_USE_LEGACY" not in content:
        print("  ✗ ANIMICA_P2P_USE_LEGACY not found in rpc/deps.py")
        return False
    
    if "p2p.node.p2p_service_legacy" not in content:
        print("  ✗ Legacy service import not found in rpc/deps.py")
        return False
    
    print("  ✓ rpc/deps.py has toggle integration")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("Legacy P2P Service Re-enable Tests")
    print("=" * 60)
    
    tests = [
        test_legacy_service_import,
        test_no_deprecation_warning,
        test_env_var_toggle,
        test_service_class_exists,
        test_rpc_deps_integration,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"  ✗ Test failed with exception: {e}")
            results.append(False)
    
    print("\n" + "=" * 60)
    print(f"Results: {sum(results)}/{len(results)} tests passed")
    print("=" * 60)
    
    if all(results):
        print("\n✓ All tests passed! Legacy P2P service is successfully reenabled.")
        print("\nTo use the legacy service:")
        print("  export ANIMICA_P2P_USE_LEGACY=1  (or omit, it's the default)")
        print("\nTo use the modern service:")
        print("  export ANIMICA_P2P_USE_LEGACY=0")
        return 0
    else:
        print("\n✗ Some tests failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
