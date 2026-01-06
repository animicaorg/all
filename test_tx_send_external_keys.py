#!/usr/bin/env python3
"""
Test script to verify external keys support in tx send command.

This tests the new --secret-key-hex, --public-key-hex, and --alg-id options.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Add the python directory to the path
sys.path.insert(0, str(Path(__file__).parent / "python"))

from typer.testing import CliRunner
from animica.cli import tx

# Enable PQ fallback for testing
os.environ["ANIMICA_ALLOW_PQ_PURE_FALLBACK"] = "1"
os.environ["ANIMICA_UNSAFE_PQ_FAKE"] = "1"

runner = CliRunner()


def test_external_keys_validation():
    """Test that external keys require all parameters."""
    print("\n=== Test 1: External keys validation ===")
    
    # Test: Missing public key (should fail)
    result = runner.invoke(tx.app, [
        "send",
        "--from", "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--secret-key-hex", "0011223344556677889900112233445566778899001122334455667788990011",
        "--rpc-url", "http://localhost:9999/rpc",
    ])
    
    print(f"Exit code: {result.exit_code}")
    print(f"Output: {result.output}")
    
    if "Both --secret-key-hex and --public-key-hex must be provided together" in result.output:
        print("✓ Correctly requires both secret and public keys")
    else:
        print("✗ Failed to validate partial keys")
        return False
    
    # Test: Missing alg-id (should fail)
    result = runner.invoke(tx.app, [
        "send",
        "--from", "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--secret-key-hex", "0011223344556677889900112233445566778899001122334455667788990011",
        "--public-key-hex", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "--rpc-url", "http://localhost:9999/rpc",
    ])
    
    print(f"\nExit code: {result.exit_code}")
    print(f"Output: {result.output}")
    
    if "--alg-id is required when using --secret-key-hex" in result.output:
        print("✓ Correctly requires alg-id with external keys")
    else:
        print("✗ Failed to require alg-id")
        return False
    
    return True


def test_wallet_file_not_found_message():
    """Test helpful error message when address not in wallet file."""
    print("\n=== Test 2: Wallet file not found message ===")
    
    # Create empty temp directory (no wallets.json)
    with tempfile.TemporaryDirectory() as tmpdir:
        # Override home to point to empty temp dir
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = tmpdir
        
        try:
            result = runner.invoke(tx.app, [
                "send",
                "--from", "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
                "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
                "--value", "1.0",
                "--rpc-url", "http://localhost:9999/rpc",
            ])
            
            print(f"Exit code: {result.exit_code}")
            print(f"Output: {result.output}")
            
            if "provide signing keys via --secret-key-hex" in result.output or "Wallet file not found" in result.output:
                print("✓ Provides helpful error message about external keys")
                return True
            else:
                print("✗ Error message doesn't mention external keys option")
                return False
        finally:
            if old_home:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)


def test_address_not_in_wallet_message():
    """Test helpful error message when address not found in wallet file."""
    print("\n=== Test 3: Address not in wallet message ===")
    
    # Create temp wallet file with some addresses
    with tempfile.TemporaryDirectory() as tmpdir:
        wallet_file = Path(tmpdir) / ".animica" / "wallets.json"
        wallet_file.parent.mkdir(parents=True)
        
        wallet_data = {
            "version": 1,
            "wallets": [
                {
                    "label": "test",
                    "address": "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
                    "alg_id": 4098,
                    "alg_name": "sphincs_shake_128s",
                    "public_key_hex": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "secret_key_hex": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "created_at": "2025-01-01T00:00:00Z"
                }
            ]
        }
        
        with open(wallet_file, "w") as f:
            json.dump(wallet_data, f)
        
        # Override home to point to temp dir
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = tmpdir
        
        try:
            # Try to send from an address that's NOT in the wallet
            result = runner.invoke(tx.app, [
                "send",
                "--from", "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
                "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
                "--value", "1.0",
                "--rpc-url", "http://localhost:9999/rpc",
            ])
            
            print(f"Exit code: {result.exit_code}")
            print(f"Output: {result.output}")
            
            if "provide signing keys using --secret-key-hex" in result.output:
                print("✓ Provides helpful tip about using external keys")
                return True
            else:
                print("✗ Error message doesn't mention external keys option")
                return False
        finally:
            if old_home:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)


def main():
    """Run all tests."""
    print("Testing external keys support in tx send command...")
    
    tests = [
        ("External keys validation", test_external_keys_validation),
        ("Wallet file not found message", test_wallet_file_not_found_message),
        ("Address not in wallet message", test_address_not_in_wallet_message),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
                print(f"✓ {name} PASSED\n")
            else:
                failed += 1
                print(f"✗ {name} FAILED\n")
        except Exception as e:
            failed += 1
            print(f"✗ {name} FAILED with exception: {e}\n")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
