#!/usr/bin/env python3
"""
Manual verification script for wallet.py RPC option fix.
This script verifies the fix without requiring PySide6 dependencies.
"""

import sys


def verify_rpc_option_fix():
    """Verify that wallet.py uses --rpc-url instead of --rpc."""
    wallet_file = "apps/miner-gui/animica_miner_gui/ui/tabs/wallet.py"
    
    print("=" * 60)
    print("Verifying wallet.py RPC option fix")
    print("=" * 60)
    
    try:
        with open(wallet_file, 'r') as f:
            content = f.read()
        
        # Check for the old incorrect option (using regex for robustness)
        import re
        if re.search(r'["\']--rpc["\'],\s*rpc_url', content):
            print("❌ FAIL: Found old '--rpc' option in wallet.py")
            print("   The code still uses '--rpc' which is incorrect.")
            return False
        
        # Check for the new correct option
        if re.search(r'["\']--rpc-url["\'],\s*rpc_url', content):
            print("✅ PASS: Found correct '--rpc-url' option in wallet.py")
        else:
            print("⚠️  WARNING: Could not find '--rpc-url' option")
            print("   This might indicate the fix wasn't applied correctly.")
            return False
        
        # Verify imports include QApplication (needed for clipboard)
        if 'from PySide6.QtWidgets import' in content:
            if 'QApplication' in content:
                print("✅ PASS: QApplication imported for clipboard functionality")
            else:
                print("❌ FAIL: QApplication not imported")
                return False
        
        # Check for copy button functionality
        if 'copy_address_button' in content:
            print("✅ PASS: Copy address button added")
        else:
            print("❌ FAIL: Copy address button not found")
            return False
        
        # Check for clipboard functionality
        if 'copy_address_to_clipboard' in content:
            print("✅ PASS: Copy to clipboard method added")
        else:
            print("❌ FAIL: Copy to clipboard method not found")
            return False
        
        # Verify the clipboard implementation
        if 'QApplication.clipboard()' in content:
            print("✅ PASS: Clipboard access implemented correctly")
        else:
            print("⚠️  WARNING: Clipboard implementation may be incorrect")
        
        print("\n" + "=" * 60)
        print("All checks passed! ✅")
        print("=" * 60)
        return True
        
    except FileNotFoundError:
        print(f"❌ ERROR: File not found: {wallet_file}")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False


def verify_tx_cli_option():
    """Verify that tx.py CLI accepts --rpc-url option."""
    tx_file = "python/animica/cli/tx.py"
    
    print("\n" + "=" * 60)
    print("Verifying tx.py CLI accepts --rpc-url")
    print("=" * 60)
    
    try:
        with open(tx_file, 'r') as f:
            content = f.read()
        
        # Check that --rpc-url is defined in the send command
        if '--rpc-url' in content and 'typer.Option' in content:
            print("✅ PASS: tx.py CLI accepts --rpc-url option")
            
            # Extract the line with --rpc-url for verification
            for line in content.split('\n'):
                if '--rpc-url' in line and 'typer.Option' in line:
                    print(f"   Found: {line.strip()}")
                    break
            return True
        else:
            print("❌ FAIL: Could not verify --rpc-url option in tx.py")
            return False
            
    except FileNotFoundError:
        print(f"❌ ERROR: File not found: {tx_file}")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False


def main():
    """Run all verification checks."""
    print("\n🔍 Running verification checks for wallet GUI fixes\n")
    
    results = []
    results.append(("Wallet RPC option fix", verify_rpc_option_fix()))
    results.append(("TX CLI option verification", verify_tx_cli_option()))
    
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    for check_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {check_name}")
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n🎉 All verifications passed!")
        return 0
    else:
        print("\n⚠️  Some verifications failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
