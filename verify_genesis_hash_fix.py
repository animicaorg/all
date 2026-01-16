#!/usr/bin/env python3
"""
Verify Genesis Hash Pin Fix
============================

This script verifies that:
1. All network genesis files compute to their pinned hashes
2. The error message provides helpful Docker rebuild instructions
3. The documentation is in place

Run: python3 verify_genesis_hash_fix.py
"""

import sys
from pathlib import Path

# Add repo to path
repo_root = Path(__file__).parent.resolve()
sys.path.insert(0, str(repo_root))

from core.genesis.loader import compute_genesis_identity
from core.network_params import (
    get_pinned_genesis_hash,
    get_network_genesis_path,
    enforce_pinned_genesis,
)
from core.errors import GenesisError


def test_pinned_hashes_match():
    """Test that all pinned hashes match their genesis files."""
    print("Testing pinned genesis hashes...")
    networks = [
        ("mainnet", 1),
        ("testnet", 2),
        ("devnet", 1337),
    ]
    
    HASH_DISPLAY_LENGTH = 16  # Show first 16 hex chars of hash
    
    for name, chain_id in networks:
        genesis_path = get_network_genesis_path(network_name=name, chain_id=chain_id)
        if genesis_path is None:
            print(f"  ❌ {name}: No genesis path configured")
            return False
            
        identity = compute_genesis_identity(genesis_path)
        pinned = get_pinned_genesis_hash(network_name=name, chain_id=chain_id)
        
        if pinned is None:
            print(f"  ❌ {name}: No pinned hash configured")
            return False
            
        if identity.genesis_block_hash == pinned:
            hash_preview = identity.genesis_block_hash.hex()[:HASH_DISPLAY_LENGTH]
            print(f"  ✅ {name}: Hash matches (0x{hash_preview}...)")
        else:
            print(f"  ❌ {name}: Hash mismatch!")
            print(f"     Expected: 0x{pinned.hex()}")
            print(f"     Got:      0x{identity.genesis_block_hash.hex()}")
            return False
    
    return True


def test_error_message():
    """Test that the error message includes Docker rebuild instructions."""
    print("\nTesting error message...")
    
    HASH_LENGTH = 32  # Standard hash length in bytes
    
    genesis_path = get_network_genesis_path(network_name="mainnet", chain_id=1)
    if genesis_path is None:
        print("  ❌ No mainnet genesis path")
        return False
    
    try:
        # Intentionally wrong hash
        wrong_hash = b"\x00" * HASH_LENGTH
        enforce_pinned_genesis(
            chain_id=1,
            genesis_block_hash=wrong_hash,
            genesis_path=str(genesis_path.resolve()),
            network_name="mainnet",
        )
        print("  ❌ Expected GenesisError but got none")
        return False
    except GenesisError as e:
        hint = e.data.get("hint", "")
        hint_lower = hint.lower()
        
        # Check for key phrases (case-insensitive for consistency)
        checks = [
            ("docker" in hint_lower, "mentions Docker"),
            ("rebuild" in hint_lower, "mentions rebuild"),
            ("docker compose build" in hint_lower, "includes rebuild command"),
            ("--no-cache" in hint, "includes --no-cache flag"),
        ]
        
        all_passed = True
        for passed, description in checks:
            if passed:
                print(f"  ✅ Error message {description}")
            else:
                print(f"  ❌ Error message does not {description}")
                all_passed = False
        
        if not all_passed:
            print(f"\nActual hint:\n{hint}")
        
        return all_passed


def test_documentation_exists():
    """Test that documentation files exist."""
    print("\nTesting documentation...")
    
    docs_to_check = [
        "ops/docker/TROUBLESHOOTING.md",
        "ops/docker/README.md",
        "README.md",
    ]
    
    all_exist = True
    for doc_path in docs_to_check:
        path = repo_root / doc_path
        if path.exists():
            # Check for key content
            content = path.read_text()
            if doc_path == "ops/docker/TROUBLESHOOTING.md":
                if "Genesis Hash Mismatch" in content:
                    print(f"  ✅ {doc_path} exists with genesis troubleshooting")
                else:
                    print(f"  ⚠️  {doc_path} exists but missing genesis section")
                    all_exist = False
            elif doc_path == "ops/docker/README.md":
                if "rebuild" in content.lower() and "genesis" in content.lower():
                    print(f"  ✅ {doc_path} updated with rebuild instructions")
                else:
                    print(f"  ⚠️  {doc_path} missing rebuild/genesis guidance")
                    all_exist = False
            else:
                print(f"  ✅ {doc_path} exists")
        else:
            print(f"  ❌ {doc_path} does not exist")
            all_exist = False
    
    return all_exist


def main():
    print("=" * 70)
    print("Genesis Hash Pin Fix Verification")
    print("=" * 70)
    print()
    
    tests = [
        ("Pinned hashes match genesis files", test_pinned_hashes_match),
        ("Error message includes Docker help", test_error_message),
        ("Documentation exists", test_documentation_exists),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"  💥 Test crashed: {e}")
            results.append((name, False))
        print()
    
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
